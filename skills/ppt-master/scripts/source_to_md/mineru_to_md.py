#!/usr/bin/env python3
"""
PPT Master - MinerU to Markdown Converter

Parse a source file with MinerU, normalize the returned bundle to Markdown,
and keep extracted images in a sibling `<output>_files/` directory.

Usage:
    python3 scripts/source_to_md/mineru_to_md.py <file.pdf> [options]
    python3 scripts/source_to_md/mineru_to_md.py mineru_result.zip --from-zip -o output.md

Examples:
    python3 scripts/source_to_md/mineru_to_md.py paper.pdf
    python3 scripts/source_to_md/mineru_to_md.py paper.pdf -o sources/paper.md --is-ocr
    python3 scripts/source_to_md/mineru_to_md.py mineru_result.zip --from-zip -o paper.md

Dependencies:
    requests, Pillow
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional metadata path
    Image = None  # type: ignore[assignment]


DEFAULT_BASE_URL = "https://mineru.net/api/v4"
TERMINAL_STATES = {"done", "failed"}
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 300.0
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
}
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HTML_IMAGE_RE = re.compile(r"""<img\b[^>]*\bsrc=["']([^"']+)["']""", re.IGNORECASE)


@dataclass
class MinerUNormalizeResult:
    """Result of normalizing a MinerU zip bundle."""

    markdown_path: Path
    asset_dir: Path | None
    image_count: int
    manifest_path: Path | None


def _strip_inline_env_comment(value: str) -> str:
    """Strip shell-style comments outside quoted values."""
    quote_char = ""
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote_char == char:
                quote_char = ""
            elif not quote_char:
                quote_char = char
            continue
        if char == "#" and not quote_char:
            return value[:index]
    return value


def _strip_env_quotes(value: str) -> str:
    """Remove one matching quote pair from an env value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_named_env_keys(keys: tuple[str, ...]) -> None:
    """Load selected keys from nearby .env files without overriding the environment."""
    candidates: list[Path] = [Path.cwd() / ".env"]
    candidates.extend(parent / ".env" for parent in Path(__file__).resolve().parents)

    seen: set[Path] = set()
    for env_path in candidates:
        resolved = env_path.resolve()
        if resolved in seen or not env_path.is_file():
            continue
        seen.add(resolved)
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in keys:
                continue
            cleaned = _strip_env_quotes(_strip_inline_env_comment(value).strip())
            os.environ.setdefault(key, cleaned)


def _get_api_token() -> str:
    """Return the configured MinerU API token."""
    _load_named_env_keys(("MINERU_API_TOKEN", "MINERU_API_KEY", "MINERU_TOKEN"))
    token = (
        os.environ.get("MINERU_API_TOKEN")
        or os.environ.get("MINERU_API_KEY")
        or os.environ.get("MINERU_TOKEN")
        or ""
    ).strip()
    if token:
        return token
    raise RuntimeError(
        "MinerU API token is not configured. Set MINERU_API_TOKEN "
        "in your environment or .env file."
    )


def _get_base_url() -> str:
    """Return the configured MinerU API base URL."""
    _load_named_env_keys(("MINERU_API_BASE_URL", "MINERU_BASE_URL"))
    base_url = (
        os.environ.get("MINERU_API_BASE_URL")
        or os.environ.get("MINERU_BASE_URL")
        or DEFAULT_BASE_URL
    ).strip()
    return base_url.rstrip("/") or DEFAULT_BASE_URL


def _auth_headers(token: str) -> dict[str, str]:
    """Build JSON auth headers for MinerU API calls."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _api_error(payload: dict[str, object], fallback: str) -> RuntimeError:
    """Create a readable MinerU API error."""
    message = str(payload.get("msg") or fallback).strip() or fallback
    code = payload.get("code")
    if code not in {None, ""}:
        message = f"{message} (code={code})"
    return RuntimeError(message)


def _default_output_path(input_path: Path) -> Path:
    """Return the default Markdown output path for an input file."""
    return input_path.with_suffix(".md")


def _safe_extract_zip(zip_path: Path, output_dir: Path) -> None:
    """Extract a zip file while rejecting path traversal entries."""
    output_root = output_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (output_dir / member.filename).resolve()
            try:
                target.relative_to(output_root)
            except ValueError as exc:
                raise RuntimeError(f"Unsafe zip member path: {member.filename}") from exc
        archive.extractall(output_dir)


def _find_markdown_source(extracted_dir: Path) -> Path:
    """Find the primary Markdown file inside a MinerU bundle."""
    full_md = sorted(extracted_dir.rglob("full.md"))
    if full_md:
        return full_md[0]

    markdown_candidates = sorted(extracted_dir.rglob("*.md"))
    if markdown_candidates:
        return markdown_candidates[0]

    raise RuntimeError("MinerU result is missing a Markdown file.")


def _find_images_dir(bundle_root: Path, extracted_dir: Path) -> Path | None:
    """Find the MinerU images directory, preferring the Markdown sibling."""
    direct = bundle_root / "images"
    if direct.is_dir():
        return direct

    candidates = sorted(
        path for path in extracted_dir.rglob("images")
        if path.is_dir()
    )
    return candidates[0] if candidates else None


def _rewrite_markdown_asset_refs(content: str, old_dir_name: str, new_dir_name: str) -> str:
    """Rewrite Markdown and HTML image references to the normalized asset directory."""
    updated = content
    for prefix in (old_dir_name, f"./{old_dir_name}"):
        updated = updated.replace(f"]({prefix}/", f"]({new_dir_name}/")
        updated = updated.replace(f'src="{prefix}/', f'src="{new_dir_name}/')
        updated = updated.replace(f"src='{prefix}/", f"src='{new_dir_name}/")
    return updated


def _image_size(path: Path) -> tuple[int | None, int | None]:
    """Return bitmap dimensions when Pillow can decode the image."""
    if Image is None:
        return None, None
    try:
        with Image.open(path) as image:
            return image.width, image.height
    except Exception:
        return None, None


def _referenced_image_counts(content: str) -> dict[str, int]:
    """Count Markdown/HTML image references by basename."""
    counts: dict[str, int] = {}
    targets = MARKDOWN_IMAGE_RE.findall(content)
    targets.extend(HTML_IMAGE_RE.findall(content))
    for target in targets:
        target = target.split("#", 1)[0].split("?", 1)[0]
        name = Path(target.replace("\\", "/")).name
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _build_image_manifest(
    asset_dir: Path,
    *,
    markdown_content: str,
    source_name: str | None,
) -> tuple[Path | None, int]:
    """Write image_manifest.json for normalized MinerU image assets."""
    image_files = [
        path for path in sorted(asset_dir.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not image_files:
        return None, 0

    reference_counts = _referenced_image_counts(markdown_content)
    manifest: list[dict[str, object]] = []
    for index, image_path in enumerate(image_files, start=1):
        width, height = _image_size(image_path)
        pixel_ratio = width / height if width and height else None
        item: dict[str, object] = {
            "index": index,
            "filename": image_path.name,
            "original_filename": image_path.name,
            "asset_kind": "bitmap",
            "svg_renderable": True,
            "pptx_native_supported": True,
            "source_kind": "mineru",
            "source_file": source_name,
            "source_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "pixel_width": width,
            "pixel_height": height,
            "pixel_ratio": round(pixel_ratio, 6) if pixel_ratio else None,
            "display_ratio": round(pixel_ratio, 6) if pixel_ratio else None,
            "usage_count": reference_counts.get(image_path.name, 1),
        }
        manifest.append(item)

    manifest_path = asset_dir / "image_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path, len(image_files)


def normalize_mineru_bundle(
    extracted_dir: Path,
    output_path: Path,
    *,
    source_name: str | None = None,
) -> MinerUNormalizeResult:
    """Normalize an extracted MinerU bundle to Markdown plus sibling assets."""
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    markdown_source = _find_markdown_source(extracted_dir)
    bundle_root = markdown_source.parent
    content = markdown_source.read_text(encoding="utf-8", errors="replace")

    asset_dir = output_path.parent / f"{output_path.stem}_files"
    if asset_dir.exists():
        shutil.rmtree(asset_dir)

    image_count = 0
    manifest_path: Path | None = None
    images_dir = _find_images_dir(bundle_root, extracted_dir)
    if images_dir is not None:
        shutil.copytree(images_dir, asset_dir)
        content = _rewrite_markdown_asset_refs(content, "images", asset_dir.name)
        manifest_path, image_count = _build_image_manifest(
            asset_dir,
            markdown_content=content,
            source_name=source_name,
        )
        if image_count == 0:
            shutil.rmtree(asset_dir)
            asset_dir = None  # type: ignore[assignment]
    else:
        asset_dir = None  # type: ignore[assignment]

    output_path.write_text(content, encoding="utf-8")
    return MinerUNormalizeResult(
        markdown_path=output_path,
        asset_dir=asset_dir,
        image_count=image_count,
        manifest_path=manifest_path,
    )


def normalize_mineru_zip(
    zip_path: Path,
    output_path: Path,
    *,
    source_name: str | None = None,
) -> MinerUNormalizeResult:
    """Normalize an existing MinerU result zip to Markdown plus assets."""
    with tempfile.TemporaryDirectory(prefix="pptmaster_mineru_") as temp_dir:
        extract_dir = Path(temp_dir) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(zip_path, extract_dir)
        return normalize_mineru_bundle(
            extract_dir,
            output_path,
            source_name=source_name or zip_path.name,
        )


def _upload_url_from_item(item: object) -> str:
    """Extract an upload URL from MinerU's file_urls payload."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("url", "upload_url", "file_url"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def convert_with_mineru(
    input_path: Path,
    output_path: Path,
    *,
    is_ocr: bool = False,
    page_ranges: str = "",
    enable_formula: bool = True,
    enable_table: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    keep_zip: bool = False,
) -> MinerUNormalizeResult:
    """Upload a file to MinerU and normalize the returned full zip."""
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    token = _get_api_token()
    base_url = _get_base_url()
    batch_file_name = input_path.name
    create_payload = {
        "enable_formula": enable_formula,
        "enable_table": enable_table,
        "is_ocr": is_ocr,
        "files": [{
            "name": batch_file_name,
            "data_id": uuid.uuid4().hex,
            "page_ranges": page_ranges,
        }],
    }

    try:
        create_response = requests.post(
            f"{base_url}/file-urls/batch",
            headers=_auth_headers(token),
            json=create_payload,
            timeout=60,
        )
        create_response.raise_for_status()
        create_json = create_response.json()
        if create_json.get("code") != 0:
            raise _api_error(create_json, "MinerU initialization failed.")

        create_data = create_json.get("data") or {}
        if not isinstance(create_data, dict):
            raise RuntimeError("MinerU returned invalid initialization data.")
        batch_id = str(create_data.get("batch_id") or "").strip()
        raw_file_urls = create_data.get("file_urls") or []
        if isinstance(raw_file_urls, dict):
            file_urls = list(raw_file_urls.values())
        else:
            file_urls = list(raw_file_urls)
        upload_url = _upload_url_from_item(file_urls[0]) if file_urls else ""
        if not batch_id or not upload_url:
            raise RuntimeError("MinerU did not return a valid upload URL or batch_id.")

        with input_path.open("rb") as file_handle:
            upload_response = requests.put(upload_url, data=file_handle, timeout=300)
        upload_response.raise_for_status()

        deadline = time.monotonic() + timeout_seconds
        extract_results: list[dict[str, object]] = []
        while True:
            batch_response = requests.get(
                f"{base_url}/extract-results/batch/{batch_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            batch_response.raise_for_status()
            batch_json = batch_response.json()
            if batch_json.get("code") != 0:
                raise _api_error(batch_json, "MinerU result polling failed.")

            extract_results = list((batch_json.get("data") or {}).get("extract_result") or [])
            states = {str(item.get("state") or "") for item in extract_results}
            if extract_results and states.issubset(TERMINAL_STATES):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("MinerU parsing timed out.")
            time.sleep(max(0.5, poll_interval))

        result_item = extract_results[0] if extract_results else {}
        if str(result_item.get("state") or "") != "done":
            raise RuntimeError(str(result_item.get("err_msg") or "MinerU parsing failed."))

        zip_url = str(
            result_item.get("full_zip_url")
            or result_item.get("full_zip_link")
            or ""
        ).strip()
        if not zip_url:
            raise RuntimeError("MinerU finished but did not return a result zip URL.")

        with tempfile.TemporaryDirectory(prefix="pptmaster_mineru_") as temp_dir:
            temp_root = Path(temp_dir)
            zip_path = temp_root / "mineru_result.zip"
            extract_dir = temp_root / "extract"
            with requests.get(zip_url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with zip_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)

            if keep_zip:
                saved_zip = output_path.with_suffix(".mineru.zip")
                saved_zip.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(zip_path, saved_zip)

            extract_dir.mkdir(parents=True, exist_ok=True)
            _safe_extract_zip(zip_path, extract_dir)
            return normalize_mineru_bundle(
                extract_dir,
                output_path,
                source_name=input_path.name,
            )
    except requests.RequestException as exc:
        message = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            body = response.text[:240].strip()
            if body:
                message = f"{message}: {body}"
        raise RuntimeError(f"MinerU request failed: {message}") from exc


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Parse a file with MinerU and normalize the result to Markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Source file to parse, or an existing MinerU result zip.")
    parser.add_argument("-o", "--output", help="Output Markdown path.")
    parser.add_argument(
        "--from-zip",
        action="store_true",
        help="Treat input as an existing MinerU result zip and skip API calls.",
    )
    parser.add_argument(
        "--is-ocr",
        action="store_true",
        help="Ask MinerU to use OCR mode for scanned/image PDFs.",
    )
    parser.add_argument(
        "--page-ranges",
        default="",
        help="Optional MinerU page range string, e.g. '1-5,8'.",
    )
    parser.add_argument(
        "--no-formula",
        action="store_true",
        help="Disable MinerU formula parsing.",
    )
    parser.add_argument(
        "--no-table",
        action="store_true",
        help="Disable MinerU table parsing.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Polling interval in seconds (default: 2.0).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Polling timeout in seconds (default: 300).",
    )
    parser.add_argument(
        "--keep-zip",
        action="store_true",
        help="Save the downloaded MinerU result zip next to the Markdown output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else _default_output_path(input_path)
    output_path = output_path.resolve()

    try:
        if args.from_zip or input_path.suffix.lower() == ".zip":
            result = normalize_mineru_zip(
                input_path,
                output_path,
                source_name=input_path.name,
            )
        else:
            result = convert_with_mineru(
                input_path,
                output_path,
                is_ocr=args.is_ocr,
                page_ranges=args.page_ranges,
                enable_formula=not args.no_formula,
                enable_table=not args.no_table,
                poll_interval=args.poll_interval,
                timeout_seconds=args.timeout,
                keep_zip=args.keep_zip,
            )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(result.markdown_path)
    print(f"[OK] Saved Markdown to: {result.markdown_path}", file=sys.stderr)
    if result.asset_dir:
        print(
            f"[OK] Saved {result.image_count} image asset(s) to: {result.asset_dir}",
            file=sys.stderr,
        )
    if result.manifest_path:
        print(f"[OK] Wrote image manifest: {result.manifest_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
