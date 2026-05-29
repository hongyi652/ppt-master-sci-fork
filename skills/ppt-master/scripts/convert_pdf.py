#!/usr/bin/env python3
"""
PPT Master - MinerU Document Conversion Wrapper

Stable wrapper around MinerU that handles proxy/SSL setup, automatic retry,
zip preservation, and a conversion report.  When MinerU fails after all
retries the script prints clear fallback instructions instead of a raw
traceback.

MinerU (v3.1+) natively supports: PDF, DOCX, PPTX, XLSX, and images.
This script is format-agnostic — it uploads whatever file it receives and
lets MinerU handle format detection.

Usage:
    python3 scripts/convert_pdf.py <file> -o <project_path>/sources/output.md
    python3 scripts/convert_pdf.py <file.pdf> --retries 3 --keep-zip
    python3 scripts/convert_pdf.py <file.docx> --timeout 600

Examples:
    python3 scripts/convert_pdf.py paper.pdf -o projects/demo/sources/paper.md
    python3 scripts/convert_pdf.py report.docx -o projects/demo/sources/report.md
    python3 scripts/convert_pdf.py slides.pptx -o projects/demo/sources/slides.md
    python3 scripts/convert_pdf.py data.xlsx -o projects/demo/sources/data.md
    python3 scripts/convert_pdf.py paper.pdf --retries 2 --is-ocr

Dependencies:
    requests, Pillow (via mineru_to_md)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_SOURCE_TO_MD_DIR = _SCRIPTS_DIR / "source_to_md"
if str(_SOURCE_TO_MD_DIR) not in sys.path:
    sys.path.insert(0, str(_SOURCE_TO_MD_DIR))

from console_encoding import configure_utf8_stdio  # noqa: E402
from mineru_to_md import (  # noqa: E402
    MinerUNormalizeResult,
    convert_with_mineru,
    normalize_mineru_zip,
)
from output_guard import resolve_project_bound_markdown_output  # noqa: E402

configure_utf8_stdio()

DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 5.0
PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "NO_PROXY", "no_proxy",
)
MINERU_HOST = "mineru.net"


# ------------------------------------------------------------------
# Proxy / SSL helpers
# ------------------------------------------------------------------

def _setup_proxy_bypass() -> list[str]:
    """Ensure MinerU host is in NO_PROXY so local proxies don't interfere.

    Returns a list of human-readable notes about what was changed.
    """
    notes: list[str] = []
    current_no_proxy = os.environ.get("NO_PROXY", "")
    if MINERU_HOST not in current_no_proxy:
        separator = "," if current_no_proxy else ""
        os.environ["NO_PROXY"] = f"{current_no_proxy}{separator}{MINERU_HOST}"
        os.environ["no_proxy"] = os.environ["NO_PROXY"]
        notes.append(f"Added {MINERU_HOST} to NO_PROXY")

    return notes


def _collect_proxy_snapshot() -> dict[str, str]:
    """Snapshot current proxy env vars for the report."""
    return {key: os.environ.get(key, "") for key in PROXY_ENV_KEYS if os.environ.get(key)}


# ------------------------------------------------------------------
# Retry wrapper
# ------------------------------------------------------------------

def _convert_with_retry(
    input_path: Path,
    output_path: Path,
    *,
    is_ocr: bool = False,
    page_ranges: str = "",
    enable_formula: bool = True,
    enable_table: bool = True,
    poll_interval: float = 2.0,
    timeout_seconds: float = 300.0,
    keep_zip: bool = False,
    max_retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> tuple[MinerUNormalizeResult, list[dict[str, object]]]:
    """Call convert_with_mineru with automatic retry on transient errors.

    Returns (result, attempt_log) where attempt_log records each try.
    """
    attempt_log: list[dict[str, object]] = []

    for attempt in range(1, max_retries + 1):
        t0 = time.monotonic()
        try:
            result = convert_with_mineru(
                input_path,
                output_path,
                is_ocr=is_ocr,
                page_ranges=page_ranges,
                enable_formula=enable_formula,
                enable_table=enable_table,
                poll_interval=poll_interval,
                timeout_seconds=timeout_seconds,
                keep_zip=keep_zip,
            )
            attempt_log.append({
                "attempt": attempt,
                "status": "success",
                "elapsed_seconds": round(time.monotonic() - t0, 2),
            })
            return result, attempt_log
        except Exception as exc:
            elapsed = round(time.monotonic() - t0, 2)
            error_text = str(exc).strip() or "unknown error"
            attempt_log.append({
                "attempt": attempt,
                "status": "failed",
                "error": error_text,
                "elapsed_seconds": elapsed,
            })
            if attempt < max_retries:
                print(
                    f"[WARN] Attempt {attempt}/{max_retries} failed: {error_text}  "
                    f"— retrying in {retry_delay}s …",
                    file=sys.stderr,
                )
                time.sleep(retry_delay)
            else:
                raise  # re-raise on final attempt


# ------------------------------------------------------------------
# Conversion report
# ------------------------------------------------------------------

def _write_report(
    report_path: Path,
    *,
    input_path: Path,
    output_path: Path,
    result: MinerUNormalizeResult | None,
    attempt_log: list[dict[str, object]],
    proxy_notes: list[str],
    proxy_snapshot: dict[str, str],
    keep_zip: bool,
    success: bool,
    error_message: str = "",
) -> None:
    """Write a JSON conversion report next to the output."""
    report: dict[str, object] = {
        "tool": "convert_pdf.py",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "input_file": str(input_path),
        "output_file": str(output_path),
        "success": success,
        "attempts": attempt_log,
        "proxy_setup": proxy_notes,
        "proxy_env": proxy_snapshot,
        "keep_zip": keep_zip,
    }
    if result is not None:
        report["markdown_path"] = str(result.markdown_path)
        report["image_count"] = result.image_count
        if result.asset_dir:
            report["asset_dir"] = str(result.asset_dir)
        if result.manifest_path:
            report["manifest_path"] = str(result.manifest_path)
    if error_message:
        report["error"] = error_message

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _print_fallback_instructions(error_message: str) -> None:
    """Print clear fallback instructions when MinerU fails."""
    print("\n" + "=" * 70, file=sys.stderr)
    print("MinerU conversion failed after all retries.", file=sys.stderr)
    print(f"Error: {error_message}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print("\nFallback options:", file=sys.stderr)
    print(
        "  1. Manual MinerU upload:\n"
        "     Go to https://mineru.net, upload the PDF manually,\n"
        "     download the result zip, then run:\n"
        "       python3 scripts/source_to_md/mineru_to_md.py result.zip --from-zip -o output.md",
        file=sys.stderr,
    )
    print(
        "  2. Check proxy / network:\n"
        "     - Ensure MINERU_API_TOKEN is set in .env\n"
        "     - If behind a corporate proxy, set HTTP_PROXY / HTTPS_PROXY\n"
        "     - The wrapper already adds mineru.net to NO_PROXY",
        file=sys.stderr,
    )
    print(
        "  3. Retry with --retries N / --timeout N:\n"
        "       python3 scripts/convert_pdf.py paper.pdf --retries 5 --timeout 600",
        file=sys.stderr,
    )
    print("", file=sys.stderr)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Stable MinerU document-to-Markdown wrapper with proxy setup, retry, and reporting. "
                    "Supports PDF, DOCX, PPTX, XLSX, and images (MinerU v3.1+).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Document file to convert (PDF, DOCX, PPTX, XLSX, or image).")
    parser.add_argument(
        "-o",
        "--output",
        help="Output Markdown path. Required when the input file is outside a project tree.",
    )
    parser.add_argument(
        "--from-zip", action="store_true",
        help="Treat input as an existing MinerU result zip; skip API calls.",
    )
    parser.add_argument("--is-ocr", action="store_true", help="Enable OCR mode.")
    parser.add_argument("--page-ranges", default="", help="MinerU page range, e.g. '1-5,8'.")
    parser.add_argument("--no-formula", action="store_true", help="Disable formula parsing.")
    parser.add_argument("--no-table", action="store_true", help="Disable table parsing.")
    parser.add_argument(
        "--retries", type=int, default=DEFAULT_RETRIES,
        help=f"Max retry attempts on transient failure (default: {DEFAULT_RETRIES}).",
    )
    parser.add_argument(
        "--retry-delay", type=float, default=DEFAULT_RETRY_DELAY_SECONDS,
        help=f"Seconds between retries (default: {DEFAULT_RETRY_DELAY_SECONDS}).",
    )
    parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="MinerU polling timeout in seconds (default: 300).",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=2.0,
        help="MinerU polling interval in seconds (default: 2.0).",
    )
    parser.add_argument(
        "--keep-zip", action="store_true",
        help="Save the MinerU result zip next to the output.",
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="Skip writing the JSON conversion report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"[ERROR] File not found: {input_path}", file=sys.stderr)
        return 1

    try:
        output_path = resolve_project_bound_markdown_output(input_path, args.output)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    report_path = output_path.with_suffix(".convert_report.json")

    # --- proxy setup ---
    proxy_notes = _setup_proxy_bypass()
    proxy_snapshot = _collect_proxy_snapshot()
    if proxy_notes:
        for note in proxy_notes:
            print(f"[proxy] {note}", file=sys.stderr)

    # --- from-zip shortcut (no retry needed) ---
    if args.from_zip or input_path.suffix.lower() == ".zip":
        try:
            result = normalize_mineru_zip(
                input_path, output_path, source_name=input_path.name,
            )
        except Exception as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            if not args.no_report:
                _write_report(
                    report_path, input_path=input_path, output_path=output_path,
                    result=None, attempt_log=[{"attempt": 1, "status": "failed", "error": str(exc)}],
                    proxy_notes=proxy_notes, proxy_snapshot=proxy_snapshot,
                    keep_zip=args.keep_zip, success=False, error_message=str(exc),
                )
            return 1
        if not args.no_report:
            _write_report(
                report_path, input_path=input_path, output_path=output_path,
                result=result, attempt_log=[{"attempt": 1, "status": "success"}],
                proxy_notes=proxy_notes, proxy_snapshot=proxy_snapshot,
                keep_zip=args.keep_zip, success=True,
            )
        print(result.markdown_path)
        print(f"[OK] {result.markdown_path}", file=sys.stderr)
        return 0

    # --- API conversion with retry ---
    result: MinerUNormalizeResult | None = None
    attempt_log: list[dict[str, object]] = []
    error_message = ""
    success = False

    try:
        result, attempt_log = _convert_with_retry(
            input_path,
            output_path,
            is_ocr=args.is_ocr,
            page_ranges=args.page_ranges,
            enable_formula=not args.no_formula,
            enable_table=not args.no_table,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout,
            keep_zip=args.keep_zip,
            max_retries=args.retries,
            retry_delay=args.retry_delay,
        )
        success = True
    except Exception as exc:
        error_message = str(exc).strip() or "unknown error"
        _print_fallback_instructions(error_message)

    if not args.no_report:
        _write_report(
            report_path, input_path=input_path, output_path=output_path,
            result=result, attempt_log=attempt_log,
            proxy_notes=proxy_notes, proxy_snapshot=proxy_snapshot,
            keep_zip=args.keep_zip, success=success, error_message=error_message,
        )
        print(f"[report] {report_path}", file=sys.stderr)

    if not success:
        return 1

    assert result is not None
    print(result.markdown_path)
    print(f"[OK] Saved Markdown to: {result.markdown_path}", file=sys.stderr)
    if result.asset_dir:
        print(f"[OK] {result.image_count} image asset(s): {result.asset_dir}", file=sys.stderr)
    if result.manifest_path:
        print(f"[OK] Image manifest: {result.manifest_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
