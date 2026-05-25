#!/usr/bin/env python3
"""
PPT Master - Image Asset Stabilizer

Post-import step that gives every asset in a project's images/ directory a
short, stable alias, records dimensions/aspect ratio, and writes (or updates)
image/formula manifests plus human-readable asset tables in notes/.

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
import html
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

from latex_to_svg import annotate_formula_svg  # noqa: E402
from latex_to_svg import FormulaEntry  # noqa: E402
from latex_to_svg import load_manifest as load_formula_manifest  # noqa: E402
from latex_to_svg import save_manifest as save_formula_manifest  # noqa: E402

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    ".emf", ".wmf", ".svg",
}

FORMULA_RE = re.compile(r"^formula_", re.IGNORECASE)
FIG_ALIAS_RE = re.compile(r"^fig\d+_", re.IGNORECASE)
FORMULA_MANIFEST_FILENAME = "formula_manifest.json"
FORMULA_ASSET_TABLE_FILENAME = "formula_asset_table.md"
EQUATION_TAG_RE = re.compile(r"\\tag\s*\{([^}]+)\}")
FORMULA_IMAGE_TAG_RE = re.compile(
    r'<image\b[^>]*\bhref=("|\')([^"\']+)(?:"|\')[^>]*?/?>',
    re.IGNORECASE,
)


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


def _latex_compact_length(latex: str) -> int:
    """Return a rough visual complexity score for LaTeX text."""
    return len(re.sub(r"\s+", "", latex or ""))


def _fit_formula_display(
    width: float,
    height: float,
    *,
    target_h: int,
    max_w: int,
) -> tuple[int, int]:
    """Scale formula dimensions to a bounded display box."""
    ratio = width / height
    display_h = max(1, target_h)
    display_w = int(round(display_h * ratio))
    if display_w > max_w:
        display_w = max_w
        display_h = max(1, int(round(display_w / ratio)))
    return display_w, display_h


def _recommend_formula_display(
    width: float,
    height: float,
    content_w: int,
    content_h: int,
    *,
    latex: str = "",
    display: bool = True,
) -> dict[str, object]:
    """Compute formula display dimensions without over-scaling short formulas."""
    if width <= 0 or height <= 0:
        return {}

    compact_len = _latex_compact_length(latex)
    is_short = not display or width <= 60 or compact_len <= 45
    is_medium = width <= 140 or compact_len <= 110

    if is_short:
        target_h = int(round(max(22, min(48, height * 2.5))))
        display_w, display_h = _fit_formula_display(
            width,
            height,
            target_h=target_h,
            max_w=min(content_w, 320),
        )
        return {
            "layout": "inline-or-callout",
            "display_w": display_w,
            "display_h": display_h,
            "scale_note": "short formula: keep near text scale; do not enlarge to hero size",
        }

    if is_medium:
        target_h = int(round(max(40, min(88, height * 2.6))))
        display_w, display_h = _fit_formula_display(
            width,
            height,
            target_h=target_h,
            max_w=min(content_w, 560),
        )
        return {
            "layout": "formula-compact",
            "display_w": display_w,
            "display_h": display_h,
            "scale_note": "compact formula: cap size unless it is the slide's main object",
        }

    display_info = _recommended_display_for_size(width, height, content_w, content_h)
    if display_info:
        display_info["scale_note"] = "display equation: may use recommended full-width sizing"
    return display_info


def _entry_text(entry: object, name: str) -> str:
    """Read a string field from a FormulaEntry-like object."""
    value = getattr(entry, name, "")
    return str(value or "").strip()


def _entry_bool(entry: object, name: str, *, default: bool = False) -> bool:
    """Read a boolean field from a FormulaEntry-like object."""
    value = getattr(entry, name, default)
    return bool(value)


def _entry_line_number(entry: object) -> int | None:
    """Read a formula source line number."""
    value = getattr(entry, "line_number", None)
    return value if isinstance(value, int) else None


def _extract_equation_label(latex: str) -> str:
    """Return the explicit LaTeX equation tag, if any."""
    match = EQUATION_TAG_RE.search(latex or "")
    return match.group(1).strip() if match else ""


def _markdown_cell(value: object, *, limit: int = 120) -> str:
    """Format a compact Markdown table cell."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text.replace("|", "\\|")


def _load_formula_entries(manifest_path: Path) -> list[object]:
    """Load formula manifest entries or return an empty list."""
    if not manifest_path.is_file():
        return []
    try:
        return list(load_formula_manifest(manifest_path))
    except Exception:
        return []


def _generated_image_aliases(manifest_data: list[dict]) -> set[str]:
    """Return alias filenames that were created in alias-only mode.

    We skip these on subsequent runs so stabilization remains idempotent.
    """
    aliases: set[str] = set()
    for item in manifest_data:
        filename = item.get("filename")
        short_alias = item.get("short_alias")
        if not isinstance(filename, str) or not isinstance(short_alias, str):
            continue
        if filename == short_alias:
            continue
        aliases.add(short_alias)
    return aliases


def _drop_generated_alias_entries(
    manifest_data: list[dict],
    generated_aliases: set[str],
) -> list[dict]:
    """Remove manifest rows that describe alias-copy files from prior runs."""
    cleaned: list[dict] = []
    for item in manifest_data:
        filename = item.get("filename")
        original_filename = item.get("original_filename")
        if (
            isinstance(filename, str)
            and original_filename == filename
            and (filename in generated_aliases or FIG_ALIAS_RE.match(filename))
        ):
            continue
        cleaned.append(item)
    return cleaned


def _formula_id_from_name(filename: str) -> str:
    """Return the logical formula id for a formula SVG filename."""
    stem = Path(filename).stem
    if stem.lower().startswith("formula_"):
        return stem[len("formula_"):]
    return stem


def _collapse_text(text: str, *, limit: int = 240) -> str:
    """Collapse whitespace and cap text for compact metadata/labels."""
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _xml_attr_escape(value: str) -> str:
    """Escape XML attribute values."""
    return html.escape(value, quote=True)


def _svg_attr(text: str, name: str) -> str:
    """Read an attribute value from raw SVG text."""
    match = re.search(rf'\b{name}=["\']([^"\']*)["\']', text, re.IGNORECASE)
    return html.unescape(match.group(1)).strip() if match else ""


def _svg_tag_text(text: str, tag: str) -> str:
    """Read a text node from a raw SVG tag."""
    match = re.search(
        rf'<{tag}\b[^>]*>(.*?)</{tag}>',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return html.unescape(_collapse_text(match.group(1), limit=800))


def _desc_field(desc: str, field: str) -> str:
    """Extract a named field from the structured SVG <desc> text."""
    match = re.search(
        rf'{re.escape(field)}:\s*(.*?)(?:\s*\|\s*[A-Za-z_-]+:|$)',
        desc,
        re.IGNORECASE,
    )
    return _collapse_text(match.group(1), limit=800) if match else ""


def _backfill_formula_entry(svg_path: Path) -> FormulaEntry:
    """Create a manifest entry for a legacy formula SVG already on disk."""
    try:
        text = svg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""

    desc = _svg_tag_text(text, "desc")
    title = _svg_tag_text(text, "title")
    formula_id = _svg_attr(text, "data-formula-id") or _formula_id_from_name(svg_path.name)
    display_token = _svg_attr(text, "data-formula-display").lower()
    display = display_token != "inline" if display_token else "inline" not in formula_id.lower()
    latex = _desc_field(desc, "latex")
    context = _desc_field(desc, "context")
    source_value = _svg_attr(text, "data-formula-source") or _desc_field(desc, "source")
    line_value = _svg_attr(text, "data-formula-line")
    if not line_value:
        source_match = re.search(r':(\d+)$', source_value)
        if source_match:
            line_value = source_match.group(1)
            source_value = source_value[: source_match.start()]

    line_number = int(line_value) if line_value.isdigit() else None
    width, height = _measure_svg_dimensions(svg_path)

    entry = FormulaEntry(
        id=formula_id,
        latex=latex,
        display=display,
        render=True,
        context=context,
        source_file=source_value,
        line_number=line_number,
        status="rendered",
        svg_path=svg_path.name,
        svg_width=width,
        svg_height=height,
    )
    if title:
        entry.extra["svg_title"] = title
    if desc:
        entry.extra["svg_desc"] = desc
    alias = _svg_attr(text, "data-formula-alias") or _desc_field(desc, "alias")
    if alias:
        entry.extra["short_alias"] = alias
    entry.extra["backfilled_from_svg"] = True
    return entry


def _discover_formula_assets(
    images_dir: Path,
    formula_entries: list[FormulaEntry],
) -> tuple[list[FormulaEntry], list[Path], dict[str, FormulaEntry], int]:
    """Return formula entries plus any legacy formula SVGs found on disk."""
    formula_by_name: dict[str, FormulaEntry] = {}
    for entry in formula_entries:
        svg_name = _entry_text(entry, "svg_path")
        if svg_name:
            formula_by_name[svg_name] = entry

    backfilled = 0
    formula_paths: list[Path] = []
    for svg_path in sorted(images_dir.glob("formula_*.svg")):
        if not svg_path.is_file():
            continue
        entry = formula_by_name.get(svg_path.name)
        if entry is None:
            entry = _backfill_formula_entry(svg_path)
            formula_entries.append(entry)
            formula_by_name[svg_path.name] = entry
            backfilled += 1
        formula_paths.append(svg_path)
    return formula_entries, formula_paths, formula_by_name, backfilled


def _formula_lookup_keys(entry: FormulaEntry) -> set[str]:
    """Return lookup keys for matching formula references."""
    keys = {
        _entry_text(entry, "id"),
        Path(_entry_text(entry, "svg_path")).name,
        Path(str(entry.extra.get("short_alias", "") or "")).name,
    }
    return {key for key in keys if key}


def _set_xml_attr(tag: str, name: str, value: str) -> str:
    """Set or insert an XML attribute on a self-contained tag string."""
    escaped = _xml_attr_escape(value)
    if re.search(rf'\b{name}=["\']', tag, re.IGNORECASE):
        return re.sub(
            rf'(\b{name}=)["\'][^"\']*["\']',
            rf'\1"{escaped}"',
            tag,
            count=1,
            flags=re.IGNORECASE,
        )
    insert_at = tag.rfind("/>")
    if insert_at == -1:
        insert_at = tag.rfind(">")
    if insert_at == -1:
        return tag
    return tag[:insert_at] + f' {name}="{escaped}"' + tag[insert_at:]


def _patch_formula_refs_in_svg(
    svg_path: Path,
    formula_lookup: dict[str, FormulaEntry],
) -> int:
    """Inject data-formula-id / aria-label into formula image tags."""
    try:
        content = svg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    changes = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changes
        tag = match.group(0)
        href = match.group(2)
        entry = formula_lookup.get(Path(href).name)
        if entry is None:
            return tag

        expected_id = _entry_text(entry, "id") or _formula_id_from_name(Path(href).name)
        latex = _entry_text(entry, "latex")
        aria_label = f"LaTeX: {_collapse_text(latex, limit=220)}" if latex else f"Formula {expected_id}"

        updated = _set_xml_attr(tag, "data-formula-id", expected_id)
        updated = _set_xml_attr(updated, "aria-label", aria_label)
        if updated != tag:
            changes += 1
        return updated

    updated_content = FORMULA_IMAGE_TAG_RE.sub(replace, content)
    if updated_content != content:
        svg_path.write_text(updated_content, encoding="utf-8")
    return changes


def _patch_project_formula_refs(
    project_dir: Path,
    formula_entries: list[FormulaEntry],
) -> tuple[int, int]:
    """Patch formula references in generated slide SVGs for legacy projects."""
    formula_lookup: dict[str, FormulaEntry] = {}
    for entry in formula_entries:
        for key in _formula_lookup_keys(entry):
            formula_lookup[key] = entry

    patched_files = 0
    patched_refs = 0
    for folder_name in ("svg_output", "svg_final"):
        svg_dir = project_dir / folder_name
        if not svg_dir.is_dir():
            continue
        for svg_path in sorted(svg_dir.glob("*.svg")):
            ref_changes = _patch_formula_refs_in_svg(svg_path, formula_lookup)
            if ref_changes > 0:
                patched_files += 1
                patched_refs += ref_changes
    return patched_files, patched_refs


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
    generated_aliases = _generated_image_aliases(manifest_data)
    manifest_data = _drop_generated_alias_entries(manifest_data, generated_aliases)
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
        and not FIG_ALIAS_RE.match(p.name)
        and p.name not in generated_aliases
    )

    formula_manifest_path = images_dir / FORMULA_MANIFEST_FILENAME
    loaded_formula_entries = _load_formula_entries(formula_manifest_path)
    formula_entries = [entry for entry in loaded_formula_entries if isinstance(entry, FormulaEntry)]
    formula_entries, formula_paths, formula_by_name, formula_backfilled = _discover_formula_assets(
        images_dir,
        formula_entries,
    )

    if not image_files and not formula_paths:
        return {"count": 0, "image_count": 0, "formula_count": 0, "message": "No image or formula assets found."}

    # Canvas dimensions
    margins = LAYOUT_MARGINS.get(canvas_key, LAYOUT_MARGINS.get("ppt169", {}))
    content_w = int(margins.get("content_width", 1160))
    content_h = int(margins.get("content_height", 600))

    # Process each image
    table_rows: list[dict[str, object]] = []
    formula_table_rows: list[dict[str, object]] = []
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

        latex = _entry_text(entry, "latex") if entry is not None else ""
        context = _entry_text(entry, "context") if entry is not None else ""
        source_file = _entry_text(entry, "source_file") if entry is not None else ""
        line_number = _entry_line_number(entry) if entry is not None else None
        display = _entry_bool(entry, "display", default=True) if entry is not None else True
        equation_label = _extract_equation_label(latex)

        ratio = round(width / height, 4) if width and height else None
        display_info: dict[str, object] = {}
        if width and height:
            display_info = _recommend_formula_display(
                width,
                height,
                content_w,
                content_h,
                latex=latex,
                display=display,
            )

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
            row["scale_note"] = display_info.get("scale_note")
        table_rows.append(row)

        formula_table_rows.append({
            **row,
            "formula_index": idx,
            "id": _entry_text(entry, "id") if entry is not None else "",
            "latex": latex,
            "display": "display" if display else "inline",
            "equation_label": equation_label,
            "source_file": source_file,
            "line_number": line_number,
            "context": context,
        })

        title, desc = annotate_formula_svg(
            formula_path,
            formula_id=_entry_text(entry, "id"),
            latex=latex,
            display=display,
            source_file=source_file,
            line_number=line_number,
            context=context,
            short_alias=alias,
        )
        entry.extra["short_alias"] = alias
        entry.extra["asset_kind"] = "formula"
        if title:
            entry.extra["svg_title"] = title
        if desc:
            entry.extra["svg_desc"] = desc
        if equation_label:
            entry.extra["equation_label"] = equation_label
        if ratio is not None:
            entry.extra["display_ratio"] = ratio
        if display_info:
            entry.extra["recommended_layout"] = display_info.get("layout")
            entry.extra["recommended_display_w"] = display_info.get("display_w")
            entry.extra["recommended_display_h"] = display_info.get("display_h")
            entry.extra["recommended_scale_note"] = display_info.get("scale_note")
        formula_alias_count += 1

    if formula_entries:
        save_formula_manifest(formula_manifest_path, list(formula_entries))

    formula_ref_files_patched, formula_refs_patched = _patch_project_formula_refs(
        project_dir,
        formula_entries,
    )

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
    formula_table_path = notes_dir / FORMULA_ASSET_TABLE_FILENAME
    if formula_table_rows:
        _write_formula_asset_table(formula_table_path, formula_table_rows, canvas_key)

    # If renamed, also update markdown refs in sources/
    if rename and rename_map:
        _rewrite_source_refs(project_dir / "sources", rename_map)

    return {
        "count": len(table_rows),
        "image_count": len(image_files),
        "formula_count": len(formula_paths),
        "renamed": len(rename_map),
        "formula_alias_count": formula_alias_count,
        "formula_backfilled": formula_backfilled,
        "formula_ref_files_patched": formula_ref_files_patched,
        "formula_refs_patched": formula_refs_patched,
        "manifest": str(manifest_path),
        "formula_manifest": str(formula_manifest_path) if formula_entries else "",
        "table": str(table_path),
        "formula_table": str(formula_table_path) if formula_table_rows else "",
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


def _write_formula_asset_table(
    path: Path,
    rows: list[dict[str, object]],
    canvas_key: str,
) -> None:
    """Write a formula lookup table with LaTeX and sizing guidance."""
    lines = [
        "# Formula Asset Table",
        "",
        f"Canvas: `{canvas_key}`",
        "",
        "Use this table when selecting formula SVGs for slide pages.",
        "Reference `SVG href` in `<image href=\"../images/...\">`; "
        "use `Alias` only as a lookup label.",
        "Short or inline formulas must stay near text/callout scale and "
        "should not be enlarged as hero images.",
        "",
        "| # | ID | Alias | SVG href | Type | Eq | Display | Scale note | LaTeX | Source | Context |",
        "|---|----|-------|----------|------|----|---------|------------|-------|--------|---------|",
    ]
    for r in rows:
        dw = r.get("recommended_display_w")
        dh = r.get("recommended_display_h")
        display_size = f"{dw}x{dh}" if dw and dh else ""
        source = r.get("source_file") or ""
        if r.get("line_number"):
            source = (
                f"{source}:{r['line_number']}"
                if source else f"line {r['line_number']}"
            )
        lines.append(
            f"| {r.get('formula_index', r.get('index', ''))} "
            f"| `{_markdown_cell(r.get('id'), limit=40)}` "
            f"| `{_markdown_cell(r.get('short_alias'), limit=48)}` "
            f"| `../images/{_markdown_cell(r.get('filename'), limit=64)}` "
            f"| {_markdown_cell(r.get('display'), limit=16)} "
            f"| {_markdown_cell(r.get('equation_label'), limit=24)} "
            f"| {_markdown_cell(display_size, limit=24)} "
            f"| {_markdown_cell(r.get('scale_note'), limit=90)} "
            f"| `{_markdown_cell(r.get('latex'), limit=180)}` "
            f"| {_markdown_cell(source, limit=80)} "
            f"| {_markdown_cell(r.get('context'), limit=180)} |"
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
    formula_backfilled = result.get("formula_backfilled", 0)
    formula_ref_files_patched = result.get("formula_ref_files_patched", 0)
    formula_refs_patched = result.get("formula_refs_patched", 0)
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
    if result.get("formula_table"):
        print(f"[OK] Formula table: {result['formula_table']}", file=sys.stderr)
    if formula_backfilled:
        print(f"[OK] Backfilled {formula_backfilled} legacy formula SVG(s) into the manifest", file=sys.stderr)
    if formula_ref_files_patched:
        print(
            f"[OK] Patched {formula_refs_patched} formula reference(s) in {formula_ref_files_patched} slide SVG file(s)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
