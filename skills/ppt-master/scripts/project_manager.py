#!/usr/bin/env python3
"""PPT Master project management helpers.

Usage:
    python3 scripts/project_manager.py init <project_name> [--format ppt169] [--dir projects]
    python3 scripts/project_manager.py import-sources <project_path> <source1> [<source2> ...] [--move | --copy]
    python3 scripts/project_manager.py validate <project_path>
    python3 scripts/project_manager.py info <project_path>
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    from project_utils import (
        CANVAS_FORMATS,
        get_project_info as get_project_info_common,
        normalize_canvas_format,
        validate_project_structure,
        validate_svg_viewbox,
    )
except ImportError:
    tools_dir = Path(__file__).resolve().parent
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    from project_utils import (  # type: ignore
        CANVAS_FORMATS,
        get_project_info as get_project_info_common,
        normalize_canvas_format,
        validate_project_structure,
        validate_svg_viewbox,
    )

TOOLS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TOOLS_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent
SOURCE_DIRNAME = "sources"
FORMULA_MANIFEST_FILENAME = "formula_manifest.json"
FORMULA_REPORT_JSON = "formula_render_report.json"
FORMULA_REPORT_MD = "formula_render_report.md"
ASSET_TAG_PATTERN = re.compile(r"[A-Za-z0-9_+\-]{3,}|[\u4e00-\u9fff]{2,}")
TEXT_SOURCE_SUFFIXES = {".md", ".markdown", ".txt"}
TABLE_TEXT_SUFFIXES = {".csv", ".tsv"}
PDF_SUFFIXES = {".pdf"}
PRESENTATION_SUFFIXES = {".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
LEGACY_EXCEL_SUFFIXES = {".xls"}
DOC_SUFFIXES = {
    ".docx", ".doc", ".odt", ".rtf",          # Office documents
    ".epub",                                    # eBooks
    ".html", ".htm",                            # Web pages
    ".tex", ".latex", ".rst", ".org",           # Academic / technical
    ".ipynb", ".typ",                           # Notebooks / Typst
}
WECHAT_HOST_KEYWORDS = ("mp.weixin.qq.com", "weixin.qq.com")
IMAGE_ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    ".emf", ".wmf", ".svg",
}

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from extract_formulas import (  # type: ignore
    build_manifest as build_formula_manifest,
    extract_formulas_from_markdown,
    save_manifest as save_formula_manifest,
)
from latex_to_svg import (  # type: ignore
    load_manifest as load_formula_manifest,
    process_manifest as process_formula_manifest,
    save_manifest as save_rendered_formula_manifest,
)
from stabilize_image_assets import stabilize_assets as stabilize_image_assets  # type: ignore


def _curl_cffi_available() -> bool:
    """Return whether curl_cffi is importable (enables Python TLS impersonation)."""
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def is_url(value: str) -> bool:
    """Return whether a string looks like an HTTP(S) URL."""
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def sanitize_name(value: str) -> str:
    """Sanitize a user-facing name into a filesystem-safe token."""
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value.strip())
    safe = safe.strip("._")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe[:120] or "source"


def derive_url_basename(url: str) -> str:
    """Derive a stable base filename from a URL."""
    parsed = urlparse(url)
    parts = [sanitize_name(parsed.netloc)]
    if parsed.path and parsed.path != "/":
        path_part = sanitize_name(parsed.path.strip("/").replace("/", "_"))
        if path_part:
            parts.append(path_part)
    return "_".join(part for part in parts if part) or "web_source"


def is_within_path(path: Path, parent: Path) -> bool:
    """Return whether `path` resolves inside `parent`."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class ProjectManager:
    """Create, inspect, validate, and populate project folders."""

    CANVAS_FORMATS = CANVAS_FORMATS

    def __init__(self, base_dir: str = "projects") -> None:
        self.base_dir = Path(base_dir)

    def init_project(
        self,
        project_name: str,
        canvas_format: str = "ppt169",
        base_dir: str | None = None,
    ) -> str:
        base_path = Path(base_dir) if base_dir else self.base_dir

        normalized_format = normalize_canvas_format(canvas_format)
        if normalized_format not in self.CANVAS_FORMATS:
            available = ", ".join(sorted(self.CANVAS_FORMATS.keys()))
            raise ValueError(
                f"Unsupported canvas format: {canvas_format} "
                f"(available: {available}; common alias: xhs -> xiaohongshu)"
            )

        date_str = datetime.now().strftime("%Y%m%d")
        project_dir_name = f"{project_name}_{normalized_format}_{date_str}"
        project_path = base_path / project_dir_name

        # Auto-increment suffix to avoid overwriting existing projects.
        # Never reuse or overwrite an existing project directory.
        if project_path.exists():
            counter = 2
            while True:
                candidate = base_path / f"{project_name}_{normalized_format}_{date_str}_{counter}"
                if not candidate.exists():
                    project_path = candidate
                    project_dir_name = candidate.name
                    print(
                        f"note: {base_path / f'{project_name}_{normalized_format}_{date_str}'} "
                        f"already exists; creating {project_dir_name} instead.",
                        file=sys.stderr,
                    )
                    break
                counter += 1

        for rel_path in (
            "svg_output",
            "svg_final",
            "images",
            "notes",
            "templates",
            SOURCE_DIRNAME,
            "exports",
        ):
            (project_path / rel_path).mkdir(parents=True, exist_ok=True)

        canvas_info = self.CANVAS_FORMATS[normalized_format]
        readme_path = project_path / "README.md"
        readme_path.write_text(
            (
                f"# {project_name}\n\n"
                f"- Canvas format: {normalized_format}\n"
                f"- Created: {date_str}\n\n"
                "## Directories\n\n"
                "- `svg_output/`: raw SVG output\n"
                "- `svg_final/`: finalized SVG output\n"
                "- `images/`: presentation assets\n"
                "- `notes/`: speaker notes\n"
                "- `templates/`: project templates\n"
                "- `sources/`: source materials and normalized markdown\n"
                "- `exports/`: main native pptx (timestamped); `_svg.pptx` sibling added when exported with `--svg-snapshot`\n"
                "- `backup/<timestamp>/`: svg_output/ archive (always written in default-flow mode; safe to delete old timestamps)\n"
            ),
            encoding="utf-8",
        )

        print(f"Project created: {project_path}")
        print(f"Canvas: {canvas_info['name']} ({canvas_info['dimensions']})")
        return str(project_path)

    def _source_dir(self, project_path: Path) -> Path:
        sources_dir = project_path / SOURCE_DIRNAME
        sources_dir.mkdir(parents=True, exist_ok=True)
        return sources_dir

    @staticmethod
    def _detect_canvas_format(project_dir: Path) -> str:
        """Infer canvas format from the project directory name."""
        name = project_dir.name
        for fmt_key in CANVAS_FORMATS:
            if f"_{fmt_key}_" in name or name.startswith(f"{fmt_key}_"):
                return fmt_key
        return "ppt169"

    def _ensure_unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path

        suffix = path.suffix
        stem = path.stem
        counter = 2
        while True:
            candidate = path.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _copy_or_move_file(self, source: Path, destination: Path, move: bool) -> Path:
        try:
            if source.resolve() == destination.resolve():
                return destination
        except FileNotFoundError:
            pass

        destination = self._ensure_unique_path(destination)
        if move:
            shutil.move(str(source), str(destination))
        else:
            shutil.copy2(source, destination)
        return destination

    def _copy_or_move_tree(self, source: Path, destination: Path, move: bool) -> Path:
        try:
            if source.resolve() == destination.resolve():
                return destination
        except FileNotFoundError:
            pass

        destination = self._ensure_unique_path(destination)
        if move:
            shutil.move(str(source), str(destination))
        else:
            shutil.copytree(source, destination)
        return destination

    def _run_tool(self, args: list[str]) -> None:
        try:
            result = subprocess.run(
                args,
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Missing executable: {args[0]}") from exc
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(details or "tool execution failed") from exc

        if result.stdout.strip():
            print(result.stdout.strip())

    def _import_pdf(
        self,
        pdf_path: Path,
        markdown_path: Path,
    ) -> None:
        # Use convert_pdf.py (stable wrapper) instead of raw mineru_to_md.py.
        # The wrapper handles proxy/SSL bypass (NO_PROXY for mineru.net),
        # automatic retry on transient network errors, and writes a
        # conversion report — all of which raw mineru_to_md.py lacks.
        self._run_tool(
            [
                sys.executable,
                str(TOOLS_DIR / "convert_pdf.py"),
                str(pdf_path),
                "-o",
                str(markdown_path),
            ]
        )

    def _import_doc(self, doc_path: Path, markdown_path: Path) -> None:
        self._run_tool(
            [
                sys.executable,
                str(TOOLS_DIR / "source_to_md" / "doc_to_md.py"),
                str(doc_path),
                "-o",
                str(markdown_path),
            ]
        )

    def _import_presentation(self, presentation_path: Path, markdown_path: Path) -> None:
        self._run_tool(
            [
                sys.executable,
                str(TOOLS_DIR / "source_to_md" / "ppt_to_md.py"),
                str(presentation_path),
                "-o",
                str(markdown_path),
            ]
        )

    def _import_excel(self, excel_path: Path, markdown_path: Path) -> None:
        self._run_tool(
            [
                sys.executable,
                str(TOOLS_DIR / "source_to_md" / "excel_to_md.py"),
                str(excel_path),
                "-o",
                str(markdown_path),
            ]
        )

    def _import_url(self, url: str, markdown_path: Path) -> None:
        # Prefer web_to_md.py: it uses curl_cffi internally when available,
        # which handles WeChat and other TLS-fingerprint-blocked sites.
        # Fall back to the Node.js version only when the URL is known to
        # require TLS impersonation AND curl_cffi isn't installed.
        host = urlparse(url).netloc.lower()
        is_tls_sensitive = any(keyword in host for keyword in WECHAT_HOST_KEYWORDS)

        if is_tls_sensitive and not _curl_cffi_available() and shutil.which("node"):
            command = ["node", str(TOOLS_DIR / "source_to_md" / "web_to_md.cjs"),
                       url, "-o", str(markdown_path)]
        else:
            command = [
                sys.executable,
                str(TOOLS_DIR / "source_to_md" / "web_to_md.py"),
                url,
                "-o",
                str(markdown_path),
            ]
        self._run_tool(command)

    def _archive_url_record(self, sources_dir: Path, url: str) -> Path:
        file_path = self._ensure_unique_path(sources_dir / f"{derive_url_basename(url)}.url.txt")
        file_path.write_text(
            f"URL: {url}\nImported: {datetime.now().isoformat(timespec='seconds')}\n",
            encoding="utf-8",
        )
        return file_path

    def _normalize_text_source(self, source_path: Path, sources_dir: Path) -> Path:
        target = self._ensure_unique_path(sources_dir / f"{source_path.stem}.md")
        content = source_path.read_text(encoding="utf-8", errors="replace")
        target.write_text(content, encoding="utf-8")
        return target

    def _canonicalize_markdown_content(self, content: str) -> str:
        canonical = content.replace("\r\n", "\n")
        canonical = re.sub(r"(?m)^(\s*Crawled:\s+).*$", r"\1__IGNORED__", canonical)
        canonical = re.sub(r"(?m)^(\s*Imported:\s+).*$", r"\1__IGNORED__", canonical)
        canonical = re.sub(r"([^\s\]()/]+_files)/", "__ASSET_DIR__/", canonical)
        return canonical.strip()

    def _find_equivalent_markdown(self, source_path: Path, sources_dir: Path) -> Path | None:
        source_content = source_path.read_text(encoding="utf-8", errors="replace")
        canonical_source = self._canonicalize_markdown_content(source_content)

        for existing in sorted(sources_dir.iterdir()):
            if existing.suffix.lower() not in {".md", ".markdown"}:
                continue
            try:
                if existing.resolve() == source_path.resolve():
                    continue
            except FileNotFoundError:
                pass

            existing_content = existing.read_text(encoding="utf-8", errors="replace")
            if self._canonicalize_markdown_content(existing_content) == canonical_source:
                return existing

        return None

    def _companion_asset_dir(self, source_path: Path) -> Path | None:
        candidate = source_path.with_name(f"{source_path.stem}_files")
        if candidate.exists() and candidate.is_dir():
            return candidate
        return None

    def _rewrite_markdown_asset_refs(
        self,
        markdown_path: Path,
        original_asset_dirname: str,
        imported_asset_dirname: str,
    ) -> None:
        if original_asset_dirname == imported_asset_dirname:
            return

        content = markdown_path.read_text(encoding="utf-8", errors="replace")
        updated = content.replace(f"{original_asset_dirname}/", f"{imported_asset_dirname}/")
        if updated != content:
            markdown_path.write_text(updated, encoding="utf-8")

    def _merge_image_manifest(self, source_items: list[dict], destination_manifest: Path) -> None:
        """Merge per-source manifest items into the project-level manifest, keyed by filename."""
        existing_data: list[object] = []
        if destination_manifest.is_file():
            try:
                loaded = json.loads(destination_manifest.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing_data = loaded
                else:
                    print(f"[WARN] Replacing non-list image manifest: {destination_manifest}")
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[WARN] Replacing unreadable image manifest {destination_manifest}: {exc}")

        new_by_filename: dict[str, dict] = {}
        new_order: list[str] = []
        for item in source_items:
            filename = item.get("filename")
            if not isinstance(filename, str):
                continue
            if filename not in new_by_filename:
                new_order.append(filename)
            new_by_filename[filename] = item

        merged: list[dict] = []
        seen: set[str] = set()
        for item in existing_data:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            if not isinstance(filename, str):
                continue
            if filename in new_by_filename:
                merged.append(new_by_filename[filename])
            else:
                merged.append(item)
            seen.add(filename)

        for filename in new_order:
            if filename not in seen:
                merged.append(new_by_filename[filename])

        destination_manifest.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _namespace_from_asset_dir(asset_dir: Path) -> str:
        """Derive a per-source namespace from a `<stem>_files` companion directory name."""
        name = asset_dir.name
        suffix = "_files"
        return name[:-len(suffix)] if name.endswith(suffix) else name

    def _propagate_image_assets(self, asset_dir: Path, project_dir: Path) -> None:
        """Copy converter-generated image assets and manifest into project images/.

        Files are namespaced by source stem to avoid collisions when multiple
        DOCX/PPTX sources contain identically-named internal media (image1.png, ...).
        """
        image_files = [
            path for path in sorted(asset_dir.iterdir())
            if path.is_file() and path.suffix.lower() in IMAGE_ASSET_SUFFIXES
        ]
        if not image_files:
            return

        manifest_path = asset_dir / "image_manifest.json"
        source_data: list[object] = []
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    source_data = loaded
                else:
                    print(f"[WARN] Ignoring non-list image manifest: {manifest_path}")
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[WARN] Cannot read image manifest {manifest_path}: {exc}")

        if not source_data:
            source_data = [
                {
                    "index": index,
                    "filename": source_file.name,
                    "original_filename": source_file.name,
                    "asset_kind": "bitmap",
                    "svg_renderable": source_file.suffix.lower() not in {".emf", ".wmf"},
                    "pptx_native_supported": True,
                    "source_kind": "markdown_asset",
                }
                for index, source_file in enumerate(image_files, start=1)
            ]

        images_dir = project_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        namespace = self._namespace_from_asset_dir(asset_dir)
        rename_map: dict[str, str] = {}

        copied_count = 0
        for source_file in image_files:
            new_name = f"{namespace}__{source_file.name}"
            shutil.copy2(source_file, images_dir / new_name)
            rename_map[source_file.name] = new_name
            copied_count += 1

        rebased_items: list[dict] = []
        for item in source_data:
            if not isinstance(item, dict):
                continue
            original = item.get("filename")
            if not isinstance(original, str):
                continue
            new_item = dict(item)
            new_item["filename"] = rename_map.get(original, f"{namespace}__{original}")
            new_item["source_namespace"] = namespace
            rebased_items.append(new_item)

        self._merge_image_manifest(rebased_items, images_dir / "image_manifest.json")
        print(
            f"Propagated {copied_count} image asset(s) + manifest "
            f"from {asset_dir} -> images/ (namespace: {namespace})"
        )

    def _propagate_companion_image_assets(self, markdown_path: Path, project_dir: Path) -> None:
        asset_dir = markdown_path.with_name(f"{markdown_path.stem}_files")
        if asset_dir.is_dir():
            self._propagate_image_assets(asset_dir, project_dir)

    def _ensure_all_companion_images_propagated(
        self,
        sources_dir: Path,
        project_dir: Path,
    ) -> None:
        """Safety net: scan sources/ for *_files/ dirs with images not yet in images/.

        The primary propagation runs inline per source type, but edge cases
        (MinerU API timing, manual pre-conversion, naming mismatches) can
        leave companion images stranded in sources/.  This post-import sweep
        catches them.
        """
        images_dir = project_dir / "images"
        if not sources_dir.is_dir():
            return
        existing_images: set[str] = set()
        if images_dir.is_dir():
            existing_images = {
                p.name for p in images_dir.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_ASSET_SUFFIXES
            }

        for candidate in sorted(sources_dir.iterdir()):
            if not candidate.is_dir() or not candidate.name.endswith("_files"):
                continue
            companion_images = [
                p for p in sorted(candidate.iterdir())
                if p.is_file() and p.suffix.lower() in IMAGE_ASSET_SUFFIXES
            ]
            if not companion_images:
                continue
            # Check if any image from this companion dir is missing from images/
            namespace = self._namespace_from_asset_dir(candidate)
            missing = [
                p for p in companion_images
                if f"{namespace}__{p.name}" not in existing_images
                and p.name not in existing_images
            ]
            if missing:
                self._propagate_image_assets(candidate, project_dir)
                # Update existing_images set for subsequent dirs
                if images_dir.is_dir():
                    existing_images = {
                        p.name for p in images_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in IMAGE_ASSET_SUFFIXES
                    }

    def _import_markdown_with_assets(
        self,
        source_path: Path,
        sources_dir: Path,
        move: bool,
    ) -> tuple[Path, Path | None, str | None]:
        archived_markdown = self._copy_or_move_file(
            source_path,
            sources_dir / source_path.name,
            move=move,
        )

        asset_dir = self._companion_asset_dir(source_path)
        if asset_dir is None:
            return archived_markdown, None, None

        imported_asset_dir = self._copy_or_move_tree(
            asset_dir,
            sources_dir / f"{archived_markdown.stem}_files",
            move=move,
        )
        self._rewrite_markdown_asset_refs(
            archived_markdown,
            original_asset_dirname=asset_dir.name,
            imported_asset_dirname=imported_asset_dir.name,
        )

        note = None
        if archived_markdown.stem != source_path.stem:
            note = (
                f"{source_path}: renamed imported markdown to {archived_markdown.name} "
                f"and rewrote asset references to {imported_asset_dir.name}/"
            )
        return archived_markdown, imported_asset_dir, note

    def import_sources(
        self,
        project_path: str,
        source_items: list[str],
        move: bool = False,
        copy: bool = False,
        pdf_parser: str = "mineru",
    ) -> dict[str, list[str]]:
        if move and copy:
            raise ValueError("--move and --copy are mutually exclusive")
        if pdf_parser != "mineru":
            raise ValueError("Native PDF parsing has been removed; only --pdf-parser mineru is supported")
        project_dir = Path(project_path)
        if not project_dir.exists() or not project_dir.is_dir():
            raise FileNotFoundError(f"Project directory not found: {project_dir}")
        if not source_items:
            raise ValueError("At least one source path or URL is required")

        sources_dir = self._source_dir(project_dir)
        summary: dict[str, list[str]] = {
            "archived": [],
            "markdown": [],
            "assets": [],
            "notes": [],
            "skipped": [],
        }
        import_started_at = datetime.now()
        import_start = time.perf_counter()
        stage_report: list[dict[str, object]] = []

        def _begin_stage(name: str, detail: str = "") -> float:
            label = f"[stage] {name}"
            if detail:
                label += f": {detail}"
            print(label, file=sys.stderr)
            return time.perf_counter()

        def _finish_stage(
            name: str,
            started: float,
            *,
            detail: str = "",
            status: str = "ok",
            error: str | None = None,
        ) -> None:
            elapsed = round(time.perf_counter() - started, 3)
            stage: dict[str, object] = {
                "name": name,
                "status": status,
                "elapsed_seconds": elapsed,
            }
            if detail:
                stage["detail"] = detail
            if error:
                stage["error"] = error
            stage_report.append(stage)

            label = f"[stage] {name}: {status} ({elapsed:.2f}s)"
            if detail:
                label += f" - {detail}"
            if error:
                label += f" - {error}"
            print(label, file=sys.stderr)

        def _write_import_report() -> None:
            notes_dir = project_dir / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)
            report_path = notes_dir / "import_sources_report.json"
            report = {
                "project": str(project_dir),
                "source_items": source_items,
                "started_at": import_started_at.isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_seconds": round(time.perf_counter() - import_start, 3),
                "summary": summary,
                "stages": stage_report,
            }
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        explicit_markdown_stems = {
            Path(item).stem
            for item in source_items
            if not is_url(item)
            and Path(item).exists()
            and Path(item).is_file()
            and Path(item).suffix.lower() in {".md", ".markdown"}
        }

        for item in source_items:
            if is_url(item):
                stage_start = _begin_stage("archive_url", item)
                archived = self._archive_url_record(sources_dir, item)
                _finish_stage("archive_url", stage_start, detail=item)
                markdown_path = self._ensure_unique_path(
                    sources_dir / f"{derive_url_basename(item)}.md"
                )
                stage_start = _begin_stage("convert_url", item)
                try:
                    self._import_url(item, markdown_path)
                except Exception as exc:  # pragma: no cover - summary path
                    _finish_stage(
                        "convert_url",
                        stage_start,
                        detail=item,
                        status="failed",
                        error=str(exc),
                    )
                    summary["skipped"].append(f"{item}: {exc}")
                    continue
                _finish_stage("convert_url", stage_start, detail=item)

                summary["archived"].append(str(archived))
                summary["markdown"].append(str(markdown_path))
                self._propagate_companion_image_assets(markdown_path, project_dir)
                continue

            source_path = Path(item)
            if not source_path.exists():
                summary["skipped"].append(f"{item}: path not found")
                continue
            if source_path.is_dir():
                summary["skipped"].append(f"{item}: directories are not supported")
                continue

            suffix = source_path.suffix.lower()

            # ⛔ IRON RULE: user-provided original documents (PDF, DOCX,
            # PPTX, XLSX, etc.) are ALWAYS copied, NEVER moved — regardless
            # of --move or repo-internal auto-move.  Only intermediate /
            # generated files (Markdown from Step 1 conversion, companion
            # asset dirs) may be moved.
            _ORIGINAL_DOC_SUFFIXES = (
                PDF_SUFFIXES | PRESENTATION_SUFFIXES | EXCEL_SUFFIXES
                | LEGACY_EXCEL_SUFFIXES | DOC_SUFFIXES
                | {'.txt', '.csv', '.tsv'}
            )
            is_original_doc = suffix in _ORIGINAL_DOC_SUFFIXES

            if copy or is_original_doc:
                effective_move = False
                if is_original_doc and move:
                    print(
                        f"note: {source_path.name} is an original document; "
                        f"copied (not moved) to protect the source file.",
                        file=sys.stderr,
                    )
            elif move:
                effective_move = True
            elif is_within_path(source_path, REPO_ROOT):
                effective_move = True
                print(
                    f"note: {source_path} is inside the ppt-master repo; moved "
                    f"(not copied) to avoid accidental commit. Pass --copy to override.",
                    file=sys.stderr,
                )
            else:
                effective_move = False

            if suffix in {".md", ".markdown"}:
                duplicate_markdown = self._find_equivalent_markdown(source_path, sources_dir)
                if duplicate_markdown is not None:
                    summary["markdown"].append(str(duplicate_markdown))
                    self._propagate_companion_image_assets(duplicate_markdown, project_dir)
                    summary["notes"].append(
                        f"{item}: skipped duplicate markdown import because equivalent content already exists as {duplicate_markdown.name}"
                    )
                    continue

                stage_start = _begin_stage("archive_markdown", str(source_path))
                archived_markdown, asset_dir, note = self._import_markdown_with_assets(
                    source_path,
                    sources_dir,
                    move=effective_move,
                )
                _finish_stage("archive_markdown", stage_start, detail=str(source_path))
                summary["archived"].append(str(archived_markdown))
                summary["markdown"].append(str(archived_markdown))
                if asset_dir is not None:
                    summary["assets"].append(str(asset_dir))
                    self._propagate_image_assets(asset_dir, project_dir)
                if note:
                    summary["notes"].append(note)
                continue

            stage_start = _begin_stage("archive_source", str(source_path))
            archived_path = self._copy_or_move_file(
                source_path,
                sources_dir / source_path.name,
                move=effective_move,
            )
            _finish_stage("archive_source", stage_start, detail=str(source_path))
            summary["archived"].append(str(archived_path))

            if suffix in PDF_SUFFIXES:
                canonical_markdown_path = sources_dir / f"{archived_path.stem}.md"
                if archived_path.stem in explicit_markdown_stems:
                    summary["notes"].append(
                        f"{item}: skipped PDF auto-conversion because a same-stem Markdown source was provided"
                    )
                    continue
                if canonical_markdown_path.exists():
                    summary["markdown"].append(str(canonical_markdown_path))
                    self._propagate_companion_image_assets(canonical_markdown_path, project_dir)
                    summary["notes"].append(
                        f"{item}: skipped PDF auto-conversion because {canonical_markdown_path.name} already exists"
                    )
                    continue
                markdown_path = canonical_markdown_path
                stage_start = _begin_stage("convert_pdf", str(archived_path))
                try:
                    self._import_pdf(archived_path, markdown_path)
                    _finish_stage("convert_pdf", stage_start, detail=str(archived_path))
                    summary["markdown"].append(str(markdown_path))
                    self._propagate_companion_image_assets(markdown_path, project_dir)
                except Exception as exc:  # pragma: no cover - summary path
                    _finish_stage(
                        "convert_pdf",
                        stage_start,
                        detail=str(archived_path),
                        status="failed",
                        error=str(exc),
                    )
                    summary["skipped"].append(f"{item}: PDF conversion failed ({exc})")
            elif suffix in PRESENTATION_SUFFIXES:
                canonical_markdown_path = sources_dir / f"{archived_path.stem}.md"
                if archived_path.stem in explicit_markdown_stems:
                    summary["notes"].append(
                        f"{item}: skipped presentation auto-conversion because a same-stem Markdown source was provided"
                    )
                    continue
                if canonical_markdown_path.exists():
                    summary["markdown"].append(str(canonical_markdown_path))
                    self._propagate_companion_image_assets(canonical_markdown_path, project_dir)
                    summary["notes"].append(
                        f"{item}: skipped presentation auto-conversion because {canonical_markdown_path.name} already exists"
                    )
                    continue
                markdown_path = canonical_markdown_path
                stage_start = _begin_stage("convert_presentation", str(archived_path))
                try:
                    self._import_presentation(archived_path, markdown_path)
                    _finish_stage("convert_presentation", stage_start, detail=str(archived_path))
                    summary["markdown"].append(str(markdown_path))
                    self._propagate_companion_image_assets(markdown_path, project_dir)
                except Exception as exc:  # pragma: no cover - summary path
                    _finish_stage(
                        "convert_presentation",
                        stage_start,
                        detail=str(archived_path),
                        status="failed",
                        error=str(exc),
                    )
                    summary["skipped"].append(f"{item}: presentation conversion failed ({exc})")
            elif suffix in EXCEL_SUFFIXES:
                canonical_markdown_path = sources_dir / f"{archived_path.stem}.md"
                if archived_path.stem in explicit_markdown_stems:
                    summary["notes"].append(
                        f"{item}: skipped Excel auto-conversion because a same-stem Markdown source was provided"
                    )
                    continue
                if canonical_markdown_path.exists():
                    summary["markdown"].append(str(canonical_markdown_path))
                    self._propagate_companion_image_assets(canonical_markdown_path, project_dir)
                    summary["notes"].append(
                        f"{item}: skipped Excel auto-conversion because {canonical_markdown_path.name} already exists"
                    )
                    continue
                markdown_path = canonical_markdown_path
                stage_start = _begin_stage("convert_excel", str(archived_path))
                try:
                    self._import_excel(archived_path, markdown_path)
                    _finish_stage("convert_excel", stage_start, detail=str(archived_path))
                    summary["markdown"].append(str(markdown_path))
                    self._propagate_companion_image_assets(markdown_path, project_dir)
                except Exception as exc:  # pragma: no cover - summary path
                    _finish_stage(
                        "convert_excel",
                        stage_start,
                        detail=str(archived_path),
                        status="failed",
                        error=str(exc),
                    )
                    summary["skipped"].append(f"{item}: Excel conversion failed ({exc})")
            elif suffix in LEGACY_EXCEL_SUFFIXES:
                summary["notes"].append(
                    f"{item}: archived only; legacy .xls is not converted automatically. "
                    "Resave as .xlsx to generate Markdown."
                )
            elif suffix in TABLE_TEXT_SUFFIXES:
                summary["notes"].append(
                    f"{item}: archived as a plain-text table source; no Markdown conversion needed"
                )
            elif suffix in DOC_SUFFIXES:
                canonical_markdown_path = sources_dir / f"{archived_path.stem}.md"
                if archived_path.stem in explicit_markdown_stems:
                    summary["notes"].append(
                        f"{item}: skipped document auto-conversion because a same-stem Markdown source was provided"
                    )
                    continue
                if canonical_markdown_path.exists():
                    summary["markdown"].append(str(canonical_markdown_path))
                    self._propagate_companion_image_assets(canonical_markdown_path, project_dir)
                    summary["notes"].append(
                        f"{item}: skipped document auto-conversion because {canonical_markdown_path.name} already exists"
                    )
                    continue
                markdown_path = canonical_markdown_path
                stage_start = _begin_stage("convert_document", str(archived_path))
                try:
                    self._import_doc(archived_path, markdown_path)
                    _finish_stage("convert_document", stage_start, detail=str(archived_path))
                    summary["markdown"].append(str(markdown_path))
                    self._propagate_companion_image_assets(markdown_path, project_dir)
                except Exception as exc:  # pragma: no cover - summary path
                    _finish_stage(
                        "convert_document",
                        stage_start,
                        detail=str(archived_path),
                        status="failed",
                        error=str(exc),
                    )
                    summary["skipped"].append(f"{item}: document conversion failed ({exc})")
            elif suffix == ".txt":
                stage_start = _begin_stage("normalize_text", str(archived_path))
                markdown_path = self._normalize_text_source(archived_path, sources_dir)
                _finish_stage("normalize_text", stage_start, detail=str(archived_path))
                summary["markdown"].append(str(markdown_path))
            else:
                summary["notes"].append(f"{item}: archived only, no automatic conversion")

        stage_start = _begin_stage("sync_formulas", str(project_dir))
        formula_summary = self._sync_formula_assets(project_dir)
        _finish_stage("sync_formulas", stage_start, detail=str(project_dir))
        if formula_summary.get("total"):
            summary["notes"].append(
                "Formula sync: "
                f"{formula_summary.get('rendered', 0)} rendered, "
                f"{formula_summary.get('failed', 0)} failed, "
                f"{formula_summary.get('pending', 0)} pending "
                f"out of {formula_summary.get('total', 0)} extracted formulas."
            )
        elif formula_summary.get("removed"):
            summary["notes"].append("Formula sync: no LaTeX formulas detected, cleared stale formula artifacts.")

        # Safety net: ensure every *_files/ companion dir under sources/ has
        # its images propagated to images/.  The primary propagation happens
        # inline during each source type's import path, but edge cases
        # (MinerU timing, naming mismatches) can leave images behind.
        stage_start = _begin_stage("propagate_companion_images", str(sources_dir))
        self._ensure_all_companion_images_propagated(sources_dir, project_dir)
        _finish_stage("propagate_companion_images", stage_start, detail=str(sources_dir))

        # Stabilize image assets: add short aliases and dimension table
        canvas_format = self._detect_canvas_format(project_dir)
        stage_start = _begin_stage("stabilize_image_assets", str(project_dir / "images"))
        stabilize_result = stabilize_image_assets(
            str(project_dir), canvas_key=canvas_format,
        )
        _finish_stage("stabilize_image_assets", stage_start, detail=str(project_dir / "images"))
        asset_count = stabilize_result.get("count", 0)
        formula_count = stabilize_result.get("formula_count", 0)
        if asset_count:
            summary["notes"].append(
                f"Asset stabilization: {asset_count} asset(s) measured, "
                f"including {formula_count} formula SVG(s); aliases + asset tables written."
            )

        summary["notes"].append("Import report: notes/import_sources_report.json")
        _write_import_report()
        return summary

    def _extract_asset_tags(self, *parts: str) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for part in parts:
            for raw_tag in ASSET_TAG_PATTERN.findall(part or ""):
                tag = raw_tag.strip().lower()
                if len(tag) < 2 or tag.isdigit() or tag in seen:
                    continue
                seen.add(tag)
                tags.append(tag)
        return tags[:16]

    def _formula_report_paths(self, project_dir: Path) -> tuple[Path, Path]:
        notes_dir = project_dir / "notes"
        return notes_dir / FORMULA_REPORT_JSON, notes_dir / FORMULA_REPORT_MD

    def _clear_formula_artifacts(self, project_dir: Path) -> list[str]:
        images_dir = project_dir / "images"
        manifest_path = images_dir / FORMULA_MANIFEST_FILENAME
        report_json_path, report_md_path = self._formula_report_paths(project_dir)
        removed: list[str] = []
        for path in [manifest_path, report_json_path, report_md_path, *sorted(images_dir.glob("formula_*.svg"))]:
            if not path.exists() or not path.is_file():
                continue
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(str(path))
        return removed

    def _write_formula_report(self, project_dir: Path, report: dict[str, object]) -> None:
        report_json_path, report_md_path = self._formula_report_paths(project_dir)
        report_json_path.parent.mkdir(parents=True, exist_ok=True)
        report_json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        summary = dict(report.get("summary") or {})
        failed_items = list(report.get("failed_formulas") or [])
        lines = [
            "# Formula Render Report",
            "",
            f"- Total formulas: {summary.get('total', 0)}",
            f"- Rendered: {summary.get('rendered', 0)}",
            f"- Failed: {summary.get('failed', 0)}",
            f"- Pending: {summary.get('pending', 0)}",
            f"- Missing SVG: {summary.get('missing', 0)}",
        ]
        if failed_items:
            lines.extend(["", "## Failed formulas", ""])
            for item in failed_items:
                if not isinstance(item, dict):
                    continue
                source_file = str(item.get("source_file") or "unknown")
                line_number = item.get("line_number")
                latex = str(item.get("latex") or "").strip().replace("\n", " ")
                render_error = str(item.get("render_error") or "").strip()
                lines.append(f"- {source_file}:{line_number or '?'} {latex[:160]}")
                if render_error:
                    lines.append(f"  - Error: {render_error}")
        report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _sync_formula_assets(self, project_dir: Path) -> dict[str, object]:
        sources_dir = self._source_dir(project_dir)
        text_sources: list[tuple[Path, str]] = []
        if sources_dir.exists():
            for source_path in sorted(sources_dir.iterdir()):
                if not source_path.is_file() or source_path.suffix.lower() not in TEXT_SOURCE_SUFFIXES:
                    continue
                text_sources.append((source_path, source_path.read_text(encoding="utf-8")))

        formula_sources = [
            (source_path, content)
            for source_path, content in text_sources
            if "$" in content or "\\begin{" in content
        ]
        removed = self._clear_formula_artifacts(project_dir)
        if not formula_sources:
            return {
                "total": 0,
                "rendered": 0,
                "failed": 0,
                "pending": 0,
                "missing": 0,
                "removed": removed,
            }

        extracted_formulas = []
        for source_path, content in formula_sources:
            extracted_formulas.extend(
                extract_formulas_from_markdown(content, source_file=source_path.name)
            )
        if not extracted_formulas:
            return {
                "total": 0,
                "rendered": 0,
                "failed": 0,
                "pending": 0,
                "missing": 0,
                "removed": removed,
            }

        manifest = build_formula_manifest(extracted_formulas, source_file="")
        formulas = list(manifest.get("formulas") or [])
        for formula in formulas:
            if not isinstance(formula, dict):
                continue
            formula["render"] = True
            formula["status"] = "pending"
            formula["tags"] = formula.get("tags") or self._extract_asset_tags(
                str(formula.get("latex") or ""),
                str(formula.get("context") or ""),
                str(formula.get("source_file") or ""),
            )
            formula["candidate_material"] = False
            formula["selected_for_deck"] = False

        manifest_path = project_dir / "images" / FORMULA_MANIFEST_FILENAME
        save_formula_manifest(manifest, manifest_path)
        try:
            process_formula_manifest(manifest_path)
        except Exception as exc:
            render_error = str(exc).strip() or "Formula rendering failed."
            entries = load_formula_manifest(manifest_path)
            for entry in entries:
                if str(entry.status or "pending").strip().lower() == "rendered":
                    continue
                entry.status = "failed"
                if not str(entry.error or "").strip():
                    entry.error = render_error
            save_rendered_formula_manifest(manifest_path, entries)

        summary = {
            "total": 0,
            "rendered": 0,
            "failed": 0,
            "pending": 0,
            "missing": 0,
        }
        failed_formulas: list[dict[str, object]] = []
        for entry in load_formula_manifest(manifest_path):
            status = str(entry.status or "pending").strip().lower()
            svg_path = manifest_path.parent / f"formula_{entry.id}.svg"
            if status == "error":
                status = "failed"
            if status == "rendered" and not svg_path.exists():
                status = "missing"
            elif status not in {"rendered", "failed", "pending", "missing"}:
                status = "rendered" if svg_path.exists() else "pending"

            summary["total"] += 1
            summary[status] += 1
            if status in {"failed", "missing"}:
                failed_formulas.append({
                    "id": entry.id,
                    "source_file": entry.source_file,
                    "line_number": entry.line_number,
                    "latex": entry.latex,
                    "status": status,
                    "render_error": entry.error,
                    "path": str(svg_path) if svg_path.exists() else "",
                })

        self._write_formula_report(
            project_dir,
            {
                "summary": summary,
                "failed_formulas": failed_formulas,
            },
        )
        summary["removed"] = removed
        return summary

    def validate_project(self, project_path: str) -> tuple[bool, list[str], list[str]]:
        project_path_obj = Path(project_path)
        is_valid, errors, warnings = validate_project_structure(str(project_path_obj))

        if project_path_obj.exists() and project_path_obj.is_dir():
            info = get_project_info_common(str(project_path_obj))
            if info.get("svg_files"):
                svg_files = [project_path_obj / "svg_output" / name for name in info["svg_files"]]
                expected_format = info.get("format")
                if expected_format == "unknown":
                    expected_format = None
                warnings.extend(validate_svg_viewbox(svg_files, expected_format))

        return is_valid, errors, warnings

    def get_project_info(self, project_path: str) -> dict[str, object]:
        shared = get_project_info_common(project_path)
        return {
            "name": shared.get("name", Path(project_path).name),
            "path": shared.get("path", str(project_path)),
            "exists": shared.get("exists", False),
            "svg_count": shared.get("svg_count", 0),
            "has_spec": shared.get("has_spec", False),
            "has_source": shared.get("has_source", False),
            "source_count": shared.get("source_count", 0),
            "canvas_format": shared.get("format_name", "Unknown"),
            "create_date": shared.get("date_formatted", "Unknown"),
        }


def print_usage() -> None:
    """Print CLI usage information from the module docstring."""
    print(__doc__)


def parse_init_args(argv: list[str]) -> tuple[str, str, str]:
    """Parse arguments for the `init` subcommand."""
    if len(argv) < 3:
        raise ValueError("Project name is required")

    project_name = argv[2]
    canvas_format = "ppt169"
    base_dir = "projects"

    i = 3
    while i < len(argv):
        if argv[i] == "--format" and i + 1 < len(argv):
            canvas_format = argv[i + 1]
            i += 2
        elif argv[i] == "--dir" and i + 1 < len(argv):
            base_dir = argv[i + 1]
            i += 2
        else:
            i += 1

    return project_name, canvas_format, base_dir


def parse_import_args(argv: list[str]) -> tuple[str, list[str], bool, bool, str]:
    """Parse arguments for the `import-sources` subcommand."""
    if len(argv) < 4:
        raise ValueError("Project path and at least one source are required")

    project_path = argv[2]
    move = False
    copy = False
    pdf_parser = "mineru"
    sources: list[str] = []

    i = 3
    while i < len(argv):
        arg = argv[i]
        if arg == "--move":
            move = True
            i += 1
        elif arg == "--copy":
            copy = True
            i += 1
        elif arg == "--pdf-parser":
            if i + 1 >= len(argv):
                raise ValueError("--pdf-parser requires a value: mineru")
            pdf_parser = argv[i + 1]
            i += 2
        elif arg.startswith("--pdf-parser="):
            pdf_parser = arg.split("=", 1)[1]
            i += 1
        else:
            sources.append(arg)
            i += 1

    if move and copy:
        raise ValueError("--move and --copy are mutually exclusive")
    if pdf_parser != "mineru":
        raise ValueError("Native PDF parsing has been removed; only --pdf-parser mineru is supported")
    if not sources:
        raise ValueError("At least one source path or URL is required")

    return project_path, sources, move, copy, pdf_parser


def main() -> None:
    """Run the CLI entry point."""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1]
    if command in {"-h", "--help", "help"}:
        print_usage()
        sys.exit(0)

    manager = ProjectManager()

    try:
        if command == "init":
            project_name, canvas_format, base_dir = parse_init_args(sys.argv)
            project_path = manager.init_project(project_name, canvas_format, base_dir=base_dir)
            print(f"[OK] Project initialized: {project_path}")
            print("Next:")
            print("1. Put source files into sources/ (or use import-sources)")
            print("2. Save your design spec to the project root")
            print("3. Generate SVG files into svg_output/")
            return

        if command == "import-sources":
            project_path, sources, move, copy, pdf_parser = parse_import_args(sys.argv)
            summary = manager.import_sources(
                project_path,
                sources,
                move=move,
                copy=copy,
                pdf_parser=pdf_parser,
            )
            print(f"[OK] Imported sources into: {project_path}")
            if summary["archived"]:
                print("\nArchived originals / URL records:")
                for item in summary["archived"]:
                    print(f"  - {item}")
            if summary["markdown"]:
                print("\nNormalized markdown:")
                for item in summary["markdown"]:
                    print(f"  - {item}")
            if summary["assets"]:
                print("\nImported asset directories:")
                for item in summary["assets"]:
                    print(f"  - {item}")
            if summary["notes"]:
                print("\nNotes:")
                for item in summary["notes"]:
                    print(f"  - {item}")
            if summary["skipped"]:
                print("\nSkipped:")
                for item in summary["skipped"]:
                    print(f"  - {item}")
            return

        if command == "validate":
            if len(sys.argv) < 3:
                raise ValueError("Project path is required")

            project_path = sys.argv[2]
            is_valid, errors, warnings = manager.validate_project(project_path)

            print(f"\nProject validation: {project_path}")
            print("=" * 60)

            if errors:
                print("\n[ERROR]")
                for error in errors:
                    print(f"  - {error}")

            if warnings:
                print("\n[WARN]")
                for warning in warnings:
                    print(f"  - {warning}")

            if is_valid and not warnings:
                print("\n[OK] Project structure is complete.")
            elif is_valid:
                print("\n[OK] Project structure is valid, with warnings.")
            else:
                print("\n[ERROR] Project structure is invalid.")
                sys.exit(1)
            return

        if command == "info":
            if len(sys.argv) < 3:
                raise ValueError("Project path is required")

            project_path = sys.argv[2]
            info = manager.get_project_info(project_path)

            print(f"\nProject info: {info['name']}")
            print("=" * 60)
            print(f"Path: {info['path']}")
            print(f"Exists: {'Yes' if info['exists'] else 'No'}")
            print(f"SVG files: {info['svg_count']}")
            print(f"Design spec: {'Yes' if info['has_spec'] else 'No'}")
            print(f"Source materials: {'Yes' if info['has_source'] else 'No'}")
            print(f"Source count: {info['source_count']}")
            print(f"Canvas format: {info['canvas_format']}")
            print(f"Created: {info['create_date']}")
            return

        raise ValueError(f"Unknown command: {command}")
    except Exception as exc:
        print(f"[ERROR] {exc}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
