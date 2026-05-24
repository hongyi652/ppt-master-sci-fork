#!/usr/bin/env python3
"""
PPT Master - Image Asset Stabilizer

Post-import step that gives every asset in a project's images/ directory a
short, stable alias, records dimensions/aspect ratio, and writes (or updates)
image/formula manifests plus a human-readable asset table in notes/.

Bitmap / vector images may optionally be renamed in-place. Formula SVGs are
kept on their original filenames because `formula_<id>.svg` is coupled to the
formula manifest and re-render pipeline; they receive alias metadata only.

Usage:
    python3 scripts/stabilize_image_assets.py <project_path>
    python3 scripts/stabilize_image_assets.py <project_path> --canvas ppt169
    python3 scripts/stabilize_image_assets.py <project_path> --rename

Examples:
    python3 scripts/stabilize_image_assets.py projects/my_project_ppt169_20260523
    python3 scripts/stabilize_image_assets.py projects/my_project_ppt169_20260523 --rename --canvas ppt169

Dependencies:
    Pillow
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

try:
    from config import CANVAS_FORMATS, LAYOUT_MARGINS
except ImportError:
    CANVAS_FORMATS = {
        "ppt169": {"name": "PPT 16:9", "width": 1280, "height": 720},
    }
    LAYOUT_MARGINS = {
        "ppt169": {
            "top": 60, "right": 60, "bottom": 60, "left": 60,
            "content_width": 1160, "content_height": 600,
        },
    }

from latex_to_svg import load_manifest as load_formula_manifest  # noqa: E402
from latex_to_svg import save_manifest as save_formula_manifest  # noqa: E402

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    ".emf", ".wmf", ".svg",
}

FORMULA_RE = re.compile(r"^formula_\d+", re.IGNORECASE)
FORMULA_MANIFEST_FILENAME = "formula_manifest.json"


# ------------------------------------------------------------------
# Dimension helpers
# ------------------------------------------------------------------

def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Return (width, height) for bitmap images; None for unreadable files."""
    if Image is None:
        return None, None
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def _classify_ratio(ratio: float) -> str:
    if ratio > 2.0:
        return "ultra-wide"
    if ratio > 1.5:
        return "wide"
    if ratio > 1.2:
        return "standard-landscape"
    if ratio > 0.8:
        return "near-square"
    return "portrait"


def _recommend_display(
    width: int,
    height: int,
    ratio: float,
    content_w: int,
    content_h: int,
) -> dict[str, object]:
    """Compute recommended display dimensions on-slide."""
    gap = 20
    min_text_h = 150
    min_text_w = 280

    # Try side-by-side layouts per image-layout-spec
    if ratio > 1.5:
        # wide → top-bottom
        img_w = content_w
        img_h = int(round(content_w / ratio))
        text_h = content_h - img_h - gap
        if text_h >= min_text_h:
            return {"layout": "top-bottom", "display_w": img_w, "display_h": img_h}

    # default → left-right height-first
    img_h = content_h
    img_w = int(round(content_h * ratio))
    text_w = content_w - img_w - gap
    if text_w >= min_text_w:
        return {"layout": "left-right", "display_w": img_w, "display_h": img_h}

    # fallback → left-right capped
    img_w = int(round(content_w * 0.65))
    img_h = min(int(round(img_w / ratio)), content_h)
    return {"layout": "left-right-capped", "display_w": img_w, "display_h": img_h}


# ------------------------------------------------------------------
# Short alias generation
# ------------------------------------------------------------------

def _short_alias(index: int, original_name: str, *, prefix: str = "fig") -> str:
    """Generate a stable short filename like fig01_overview.jpg."""
    stem = Path(original_name).stem
    ext = Path(original_name).suffix.lower()

    # Strip namespace prefix (e.g. full__image_xxxx → image_xxxx)
    if "__" in stem:
        stem = stem.split("__", 1)[1]

    # Keep first 24 chars of cleaned stem
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", stem)
    clean = re.sub(r"_+", "_", clean).strip("_")[:24].rstrip("_")
    if not clean:
        clean = "img"

    return f"{prefix}{index:02d}_{clean}{ext}"


def _coerce_positive_float(value: object) -> float | None:
    """Return a positive float when possible."""
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if numeric > 0 else None
    if isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return None
        return numeric if numeric > 0 else None
    return None


def _measure_svg_dimensions(path: Path) -> tuple[float | None, float | None]:
    """Extract SVG width/height from the root element when present."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    width_match = re.search(r'width=["\']([0-9.]+)', text)
    height_match = re.search(r'height=["\']([0-9.]+)', text)
    width = float(width_match.group(1)) if width_match else None
    height = float(height_match.group(1)) if height_match else None
    return width, height


def _recommended_display_for_size(
    width: float,
    height: float,
    content_w: int,
    content_h: int,
) -> dict[str, object]:
    """Compute a recommended display frame from measured width/height."""
    if width <= 0 or height <= 0:
        return {}
    ratio = width / height
    return _recommend_display(
        max(1, int(round(width))),
        max(1, int(round(height))),
        ratio,
        content_w,
        content_h,
    )


def _load_formula_entries(manifest_path: Path) -> list[object]:
    """Load formula manifest entries or return an empty list."""
    if not manifest_path.is_file():
        return []
    try:
        return list(load_formula_manifest(manifest_path))
    except Exception:
        return []


# ------------------------------------------------------------------
# Core
# ------------------------------------------------------------------

def stabilize_assets(
    project_path: str,
    *,
    canvas_key: str = "ppt169",
    rename: bool = False,
) -> dict[str, object]:
    """Stabilize image assets: alias, measure, enrich manifest, write table.

    Args:
        project_path: Path to the project directory.
        canvas_key: Canvas format key for display-size recommendations.
        rename: If True, rename files in-place to the short alias.
                If False (default), only update manifest with alias metadata.

    Returns:
        Summary dict.
    """
    project_dir = Path(project_path).resolve()
    images_dir = project_dir / "images"
    if not images_dir.is_dir():
        return {"error": f"images/ not found in {project_dir}", "count": 0}

    # Load existing manifest
    manifest_path = images_dir / "image_manifest.json"
    manifest_data: list[dict] = []
    manifest_by_filename: dict[str, dict] = {}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                manifest_data = [item for item in loaded if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            pass
    for item in manifest_data:
        fn = item.get("filename")
        if isinstance(fn, str):
            manifest_by_filename[fn] = item

    # Discover non-formula image files
    image_files = sorted(
        p for p in images_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTENSIONS
        and not FORMULA_RE.match(p.name)
    )

    formula_manifest_path = images_dir / FORMULA_MANIFEST_FILENAME
    formula_entries = _load_formula_entries(formula_manifest_path)
    formula_paths: list[Path] = []
    formula_by_name: dict[str, object] = {}
    for entry in formula_entries:
        svg_name = str(getattr(entry, "svg_path", "") or "").strip()
        if not svg_name:
            continue
        svg_path = images_dir / svg_name
        if not svg_path.is_file():
            continue
        formula_paths.append(svg_path)
        formula_by_name[svg_name] = entry

    if not image_files and not formula_paths:
        return {"count": 0, "image_count": 0, "formula_count": 0, "message": "No image or formula assets found."}

    # Canvas dimensions
    margins = LAYOUT_MARGINS.get(canvas_key, LAYOUT_MARGINS.get("ppt169", {}))
    content_w = int(margins.get("content_width", 1160))
    content_h = int(margins.get("content_height", 600))

    # Process each image
    table_rows: list[dict[str, object]] = []
    rename_map: dict[str, str] = {}  # old_name -> new_name
    formula_alias_count = 0

    for idx, img_path in enumerate(image_files, start=1):
        alias = _short_alias(idx, img_path.name, prefix="fig")
        width, height = _image_dimensions(img_path)

        # Derive ratio
        if width and height:
            ratio = round(width / height, 4)
        else:
            # Fallback from manifest
            meta = manifest_by_filename.get(img_path.name, {})
            ratio = meta.get("pixel_ratio") or meta.get("display_ratio")
            if not isinstance(ratio, (int, float)) or ratio <= 0:
                ratio = None

        # Recommendation
        display_info: dict[str, object] = {}
        if width and height and ratio:
            display_info = _recommend_display(width, height, ratio, content_w, content_h)

        row: dict[str, object] = {
            "index": idx,
            "asset_kind_label": "image",
            "filename": img_path.name,
            "short_alias": alias,
            "pixel_width": width,
            "pixel_height": height,
            "aspect_ratio": ratio,
            "ratio_class": _classify_ratio(ratio) if ratio else "unknown",
            "filesize_kb": round(img_path.stat().st_size / 1024, 1),
        }
        if display_info:
            row["recommended_layout"] = display_info.get("layout")
            row["recommended_display_w"] = display_info.get("display_w")
            row["recommended_display_h"] = display_info.get("display_h")
        table_rows.append(row)

        # Update manifest entry
        entry = manifest_by_filename.get(img_path.name)
        if entry is not None:
            entry["short_alias"] = alias
            if width is not None:
                entry["pixel_width"] = width
                entry["pixel_height"] = height
                entry["pixel_ratio"] = ratio
                entry["display_ratio"] = ratio
            if display_info:
                entry["recommended_layout"] = display_info.get("layout")
                entry["recommended_display_w"] = display_info.get("display_w")
                entry["recommended_display_h"] = display_info.get("display_h")
        else:
            # Create a new manifest entry
            new_entry: dict[str, object] = {
                "index": idx,
                "filename": img_path.name,
                "original_filename": img_path.name,
                "short_alias": alias,
                "asset_kind": "bitmap" if img_path.suffix.lower() not in {".svg", ".emf", ".wmf"} else "vector",
                "svg_renderable": img_path.suffix.lower() not in {".emf", ".wmf"},
                "pptx_native_supported": True,
                "pixel_width": width,
                "pixel_height": height,
                "pixel_ratio": ratio,
                "display_ratio": ratio,
            }
            if display_info:
                new_entry["recommended_layout"] = display_info.get("layout")
                new_entry["recommended_display_w"] = display_info.get("display_w")
                new_entry["recommended_display_h"] = display_info.get("display_h")
            manifest_data.append(new_entry)
            manifest_by_filename[img_path.name] = new_entry

        # Optionally rename
        if rename and img_path.name != alias:
            target = images_dir / alias
            if target.exists():
                # avoid collision — keep original name
                print(
                    f"[WARN] Alias collision: {alias} already exists, keeping {img_path.name}",
                    file=sys.stderr,
                )
            else:
                img_path.rename(target)
                rename_map[img_path.name] = alias
                # Update manifest filename
                entry_to_update = manifest_by_filename.pop(img_path.name, None)
                if entry_to_update is not None:
                    entry_to_update["original_filename"] = entry_to_update.get("original_filename", img_path.name)
                    entry_to_update["filename"] = alias
                    manifest_by_filename[alias] = entry_to_update
        elif not rename and img_path.name != alias:
            # Create a copy with the alias name so SVG <image href> references
            # using the short alias resolve to an actual file on disk.
            alias_target = images_dir / alias
            if not alias_target.exists():
                try:
                    # Prefer hard link (no extra disk space, instant)
                    os.link(img_path, alias_target)
                except OSError:
                    # Fallback to copy if hard links are unsupported
                    shutil.copy2(img_path, alias_target)

    for idx, formula_path in enumerate(sorted(formula_paths), start=1):
        entry = formula_by_name.get(formula_path.name)
        alias = _short_alias(idx, formula_path.name.replace("formula_", "", 1), prefix="eq")

        width = _coerce_positive_float(getattr(entry, "svg_width", None)) if entry is not None else None
        height = _coerce_positive_float(getattr(entry, "svg_height", None)) if entry is not None else None
        if width is None or height is None:
            measured_w, measured_h = _measure_svg_dimensions(formula_path)
            width = width or measured_w
            height = height or measured_h

        ratio = round(width / height, 4) if width and height else None
        display_info: dict[str, object] = {}
        if width and height:
            display_info = _recommended_display_for_size(width, height, content_w, content_h)

        row = {
            "index": len(table_rows) + 1,
            "asset_kind_label": "formula",
            "filename": formula_path.name,
            "short_alias": alias,
            "pixel_width": round(width, 2) if width is not None else None,
            "pixel_height": round(height, 2) if height is not None else None,
            "aspect_ratio": ratio,
            "ratio_class": _classify_ratio(ratio) if ratio else "unknown",
            "filesize_kb": round(formula_path.stat().st_size / 1024, 1),
        }
        if display_info:
            row["recommended_layout"] = display_info.get("layout")
            row["recommended_display_w"] = display_info.get("display_w")
            row["recommended_display_h"] = display_info.get("display_h")
        table_rows.append(row)

        if entry is not None:
            entry.extra["short_alias"] = alias
            entry.extra["asset_kind"] = "formula"
            if ratio is not None:
                entry.extra["display_ratio"] = ratio
            if display_info:
                entry.extra["recommended_layout"] = display_info.get("layout")
                entry.extra["recommended_display_w"] = display_info.get("display_w")
                entry.extra["recommended_display_h"] = display_info.get("display_h")
            formula_alias_count += 1

    if formula_entries:
        save_formula_manifest(formula_manifest_path, list(formula_entries))

    for table_index, row in enumerate(table_rows, start=1):
        row["table_index"] = table_index

    # Write updated manifest
    # Rebuild list in consistent order
    final_manifest: list[dict] = []
    seen: set[str] = set()
    for item in manifest_data:
        fn = item.get("filename")
        if isinstance(fn, str) and fn not in seen:
            final_manifest.append(item)
            seen.add(fn)
    manifest_path.write_text(
        json.dumps(final_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Write human-readable table to notes/
    notes_dir = project_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    table_path = notes_dir / "image_asset_table.md"
    _write_asset_table(table_path, table_rows, canvas_key, rename)

    # If renamed, also update markdown refs in sources/
    if rename and rename_map:
        _rewrite_source_refs(project_dir / "sources", rename_map)

    return {
        "count": len(table_rows),
        "image_count": len(image_files),
        "formula_count": len(formula_paths),
        "renamed": len(rename_map),
        "formula_alias_count": formula_alias_count,
        "manifest": str(manifest_path),
        "formula_manifest": str(formula_manifest_path) if formula_entries else "",
        "table": str(table_path),
    }


def _write_asset_table(
    path: Path,
    rows: list[dict[str, object]],
    canvas_key: str,
    renamed: bool,
) -> None:
    """Write a Markdown dimension/alias table."""
    lines = [
        "# Asset Size Table",
        "",
        f"Canvas: `{canvas_key}`  ",
        f"Mode: {'renamed' if renamed else 'alias-only (originals unchanged)'}",
        "Formula SVGs always remain alias-only so their manifest/render pipeline stays stable.",
        "",
        "| # | Kind | Filename | Alias | W×H | Ratio | Class | Size | Layout | Display |",
        "|---|------|----------|-------|-----|-------|-------|------|--------|---------|",
    ]
    for r in rows:
        w = r.get("pixel_width") or "?"
        h = r.get("pixel_height") or "?"
        dim = f"{w}×{h}"
        ratio = f'{r["aspect_ratio"]:.2f}' if r.get("aspect_ratio") else "?"
        layout = r.get("recommended_layout") or ""
        dw = r.get("recommended_display_w")
        dh = r.get("recommended_display_h")
        display = f"{dw}×{dh}" if dw and dh else ""
        lines.append(
            f"| {r.get('table_index', r['index'])} "
            f"| {r.get('asset_kind_label', 'image')} "
            f"| `{r['filename']}` "
            f"| `{r['short_alias']}` "
            f"| {dim} "
            f"| {ratio} "
            f"| {r.get('ratio_class', '')} "
            f"| {r.get('filesize_kb', '')} KB "
            f"| {layout} "
            f"| {display} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rewrite_source_refs(sources_dir: Path, rename_map: dict[str, str]) -> None:
    """Update Markdown image references in sources/ after renaming."""
    if not sources_dir.is_dir():
        return
    for md_path in sorted(sources_dir.glob("*.md")):
        content = md_path.read_text(encoding="utf-8", errors="replace")
        updated = content
        for old_name, new_name in rename_map.items():
            updated = updated.replace(old_name, new_name)
        if updated != content:
            md_path.write_text(updated, encoding="utf-8")
            print(f"  Updated refs in {md_path.name}", file=sys.stderr)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Generate short aliases and dimension table for project image assets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", help="Project directory path.")
    parser.add_argument(
        "--canvas", default="ppt169",
        help="Canvas format key for display recommendations (default: ppt169).",
    )
    parser.add_argument(
        "--rename", action="store_true",
        help="Rename files in-place to the short alias (default: alias-only).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if Image is None:
        print("[ERROR] Pillow is required: pip install Pillow", file=sys.stderr)
        return 1

    result = stabilize_assets(args.project, canvas_key=args.canvas, rename=args.rename)

    if result.get("error"):
        print(f"[ERROR] {result['error']}", file=sys.stderr)
        return 1

    count = result.get("count", 0)
    image_count = result.get("image_count", 0)
    formula_count = result.get("formula_count", 0)
    renamed = result.get("renamed", 0)
    print(
        f"[OK] Processed {count} asset(s) "
        f"({image_count} image(s), {formula_count} formula SVG(s))",
        file=sys.stderr,
    )
    if renamed:
        print(f"[OK] Renamed {renamed} file(s) to short aliases", file=sys.stderr)
    if result.get("manifest"):
        print(f"[OK] Manifest: {result['manifest']}", file=sys.stderr)
    if result.get("formula_manifest"):
        print(f"[OK] Formula manifest: {result['formula_manifest']}", file=sys.stderr)
    if result.get("table"):
        print(f"[OK] Table: {result['table']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
