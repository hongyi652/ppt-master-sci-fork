#!/usr/bin/env python3
"""
PPT Master - LaTeX Formula to SVG Converter

Convert LaTeX formula strings into standalone SVG files using the local
TeX distribution (MiKTeX / TeX Live) and dvisvgm.

Usage:
    python3 scripts/latex_to_svg.py "E=mc^2" -o images/formula_01.svg
    python3 scripts/latex_to_svg.py --manifest <project_path>/images/formula_manifest.json

Examples:
    python3 scripts/latex_to_svg.py "\\frac{a}{b}" -o out.svg
    python3 scripts/latex_to_svg.py "x^2" -o images/formula_inline_901.svg --inline --source-file slide_02.md --line-number 18 --context "Diffusion coefficient units use m^2/s"
    python3 scripts/latex_to_svg.py --manifest projects/demo/images/formula_manifest.json
    python3 scripts/latex_to_svg.py --manifest projects/demo/images/formula_manifest.json --font-size 14

Dependencies:
    latex (or xelatex/pdflatex) and dvisvgm — provided by MiKTeX or TeX Live
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ============================================================
# Constants
# ============================================================

LATEX_TEMPLATE = r"""\documentclass[preview,border={border}pt]{{standalone}}
\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage{{amsfonts}}
\usepackage{{mathtools}}
\usepackage[T1]{{fontenc}}
\begin{{document}}
{content}
\end{{document}}
"""

DISPLAY_WRAP = r"\[{formula}\]"
INLINE_WRAP = r"${formula}$"

DEFAULT_BORDER_PT = 2
DEFAULT_FONT_SIZE_PT = 12
SVG_FILENAME_PREFIX = "formula_"
FORMULA_MANIFEST_FILENAME = "formula_manifest.json"
MANIFEST_VERSION = 1


# ============================================================
# Data classes
# ============================================================

@dataclass
class FormulaEntry:
    """A single formula entry from/to the manifest."""

    id: str
    latex: str
    display: bool = True
    render: bool = False
    context: str = ""
    source_file: str = ""
    line_number: int | None = None
    source_page: int | None = None
    status: str = "pending"
    svg_path: str = ""
    svg_width: float | None = None
    svg_height: float | None = None
    error: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "latex": self.latex,
            "display": self.display,
            "render": self.render,
            "context": self.context,
            "status": self.status,
        }
        if self.source_file:
            d["source_file"] = self.source_file
        if self.line_number is not None:
            d["line_number"] = self.line_number
        if self.source_page is not None:
            d["source_page"] = self.source_page
        if self.svg_path:
            d["svg_path"] = self.svg_path
        if self.svg_width is not None:
            d["svg_width"] = self.svg_width
        if self.svg_height is not None:
            d["svg_height"] = self.svg_height
        if self.error:
            d["error"] = self.error
        if self.extra:
            d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FormulaEntry":
        known_keys = {
            "id", "latex", "display", "render", "context",
            "source_file", "line_number", "source_page",
            "status", "svg_path", "svg_width",
            "svg_height", "error",
        }
        extra = {k: v for k, v in d.items() if k not in known_keys}
        return cls(
            id=d.get("id", ""),
            latex=d.get("latex", ""),
            display=d.get("display", True),
            render=d.get("render", False),
            context=d.get("context", ""),
            source_file=d.get("source_file", ""),
            line_number=d.get("line_number"),
            source_page=d.get("source_page"),
            status=d.get("status", "pending"),
            svg_path=d.get("svg_path", ""),
            svg_width=d.get("svg_width"),
            svg_height=d.get("svg_height"),
            error=d.get("error", ""),
            extra=extra,
        )


# ============================================================
# TeX tool detection
# ============================================================

def _find_tex_compiler() -> str:
    """Return the first available TeX compiler command."""
    for cmd in ("latex", "xelatex", "pdflatex"):
        if shutil.which(cmd):
            return cmd
    raise RuntimeError(
        "No TeX compiler found. Install MiKTeX or TeX Live and ensure "
        "'latex', 'xelatex', or 'pdflatex' is on PATH."
    )


def _find_dvisvgm() -> str:
    """Return the dvisvgm path or raise."""
    path = shutil.which("dvisvgm")
    if path:
        return path
    raise RuntimeError(
        "dvisvgm not found. Install MiKTeX or TeX Live and ensure "
        "'dvisvgm' is on PATH."
    )


# ============================================================
# Core conversion
# ============================================================

# TeX internal commands that MinerU sometimes leaves in extracted formulas.
# These are low-level TeX primitives that cause compilation or dvisvgm
# failures.  We strip them silently since they carry no visual meaning.
_TEX_INTERNAL_STRIP_RE = re.compile(
    r'\\(?:aftergroup|egroup|bgroup|begingroup|endgroup'
    r'|expandafter|noexpand|relax|protect)\b\s*'
)


def _sanitize_formula(formula: str) -> str:
    """Remove TeX internal commands that break compilation."""
    return _TEX_INTERNAL_STRIP_RE.sub('', formula)


def _build_tex_source(
    formula: str,
    *,
    display: bool = True,
    border_pt: int = DEFAULT_BORDER_PT,
) -> str:
    """Build a complete .tex file for a single formula."""
    if display:
        content = DISPLAY_WRAP.format(formula=formula)
    else:
        content = INLINE_WRAP.format(formula=formula)

    return LATEX_TEMPLATE.format(content=content, border=border_pt)


def _parse_svg_dimensions(svg_path: Path) -> tuple[float | None, float | None]:
    """Extract width/height from the SVG root element (pt or px)."""
    text = svg_path.read_text(encoding="utf-8", errors="replace")
    w_match = re.search(r'width=["\']([0-9.]+)', text)
    h_match = re.search(r'height=["\']([0-9.]+)', text)
    width = float(w_match.group(1)) if w_match else None
    height = float(h_match.group(1)) if h_match else None
    return width, height


def _collapse_metadata_text(text: str, *, limit: int = 500) -> str:
    """Collapse whitespace and cap metadata text."""
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _metadata_id(value: str) -> str:
    """Return a safe XML id suffix."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "formula").strip("_")
    return safe or "formula"


def annotate_formula_svg(
    svg_path: Path,
    *,
    formula_id: str = "",
    latex: str = "",
    display: bool = True,
    source_file: str = "",
    line_number: int | None = None,
    context: str = "",
    short_alias: str = "",
) -> tuple[str, str]:
    """Embed non-visual formula metadata into a rendered SVG."""
    try:
        text = svg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""

    svg_match = re.search(r"<svg\b[^>]*>", text, re.IGNORECASE | re.DOTALL)
    if not svg_match:
        return "", ""

    label = formula_id or svg_path.stem.replace(SVG_FILENAME_PREFIX, "", 1)
    title = f"Formula {label}".strip()
    desc_parts = [
        f"latex: {_collapse_metadata_text(latex, limit=600)}",
        f"display: {'display' if display else 'inline'}",
    ]
    if source_file:
        source = source_file
        if line_number is not None:
            source = f"{source}:{line_number}"
        desc_parts.append(f"source: {_collapse_metadata_text(source, limit=180)}")
    if short_alias:
        desc_parts.append(f"alias: {_collapse_metadata_text(short_alias, limit=120)}")
    if context:
        desc_parts.append(f"context: {_collapse_metadata_text(context, limit=320)}")
    desc = " | ".join(part for part in desc_parts if part.strip())

    text = re.sub(
        r'\s*<title\s+id=["\']formula-title-[^"\']+["\']>.*?</title>\s*',
        "\n",
        text,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r'\s*<desc\s+id=["\']formula-desc-[^"\']+["\']>.*?</desc>\s*',
        "\n",
        text,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    root_tag = svg_match.group(0)
    clean_root_tag = re.sub(
        r'\s+data-formula-(?:id|display|source|line|alias)=["\'][^"\']*["\']',
        "",
        root_tag,
        flags=re.IGNORECASE,
    )
    if clean_root_tag != root_tag:
        text = text[:svg_match.start()] + clean_root_tag + text[svg_match.end():]
        svg_match = re.search(r"<svg\b[^>]*>", text, re.IGNORECASE | re.DOTALL)
        if not svg_match:
            return title, desc
        root_tag = svg_match.group(0)

    attrs: dict[str, str] = {
        "data-formula-id": label,
        "data-formula-display": "display" if display else "inline",
    }
    if source_file:
        attrs["data-formula-source"] = _collapse_metadata_text(source_file, limit=120)
    if line_number is not None:
        attrs["data-formula-line"] = str(line_number)
    if short_alias:
        attrs["data-formula-alias"] = short_alias

    attr_text = ""
    for name, value in attrs.items():
        if re.search(rf"\b{name}\s*=", root_tag):
            continue
        attr_text += f' {name}="{html.escape(value, quote=True)}"'

    if attr_text:
        insert_at = svg_match.end() - 1
        text = text[:insert_at] + attr_text + text[insert_at:]
        svg_match = re.search(r"<svg\b[^>]*>", text, re.IGNORECASE | re.DOTALL)
        if not svg_match:
            return title, desc

    safe_id = _metadata_id(label)
    metadata_xml = (
        f'\n<title id="formula-title-{safe_id}">{html.escape(title)}</title>\n'
        f'<desc id="formula-desc-{safe_id}">{html.escape(desc)}</desc>\n'
    )
    text = text[:svg_match.end()] + metadata_xml + text[svg_match.end():]
    svg_path.write_text(text, encoding="utf-8")
    return title, desc


def _run_quiet(cmd: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing output, with timeout."""
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def compile_formula_to_svg(
    formula: str,
    output_path: Path,
    *,
    display: bool = True,
    border_pt: int = DEFAULT_BORDER_PT,
    compiler: str | None = None,
    dvisvgm_path: str | None = None,
) -> Path:
    """Compile a LaTeX formula string to an SVG file.

    Args:
        formula: Raw LaTeX math code (without delimiters).
        output_path: Destination .svg file path.
        display: True for display math (\\[...\\]), False for inline ($...$).
        border_pt: Border padding in TeX points.
        compiler: TeX compiler command (auto-detected if None).
        dvisvgm_path: Path to dvisvgm (auto-detected if None).

    Returns:
        The resolved output path.

    Raises:
        RuntimeError: On compilation or conversion failure.
    """
    compiler = compiler or _find_tex_compiler()
    dvisvgm_path = dvisvgm_path or _find_dvisvgm()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    formula = _sanitize_formula(formula)
    tex_source = _build_tex_source(formula, display=display, border_pt=border_pt)

    with tempfile.TemporaryDirectory(prefix="pptmaster_latex_") as tmp:
        tmp_dir = Path(tmp)
        tex_file = tmp_dir / "formula.tex"
        tex_file.write_text(tex_source, encoding="utf-8")

        is_pdf_compiler = compiler in ("pdflatex", "xelatex", "lualatex")

        # Step 1: compile .tex → .dvi or .pdf
        compile_cmd = [compiler, "-interaction=nonstopmode", "formula.tex"]
        result = _run_quiet(compile_cmd, tmp_dir)
        if result.returncode != 0:
            log_tail = (result.stdout or "")[-800:]
            raise RuntimeError(
                f"TeX compilation failed ({compiler}):\n{log_tail}"
            )

        # Step 2: convert to SVG
        if is_pdf_compiler:
            pdf_file = tmp_dir / "formula.pdf"
            if not pdf_file.exists():
                raise RuntimeError("TeX compilation produced no PDF output.")
            svg_cmd = [
                dvisvgm_path,
                "--pdf",
                "--no-fonts",
                "--exact-bbox",
                "-o", str(output_path),
                str(pdf_file),
            ]
        else:
            dvi_file = tmp_dir / "formula.dvi"
            if not dvi_file.exists():
                raise RuntimeError("TeX compilation produced no DVI output.")
            svg_cmd = [
                dvisvgm_path,
                "--no-fonts",
                "--exact-bbox",
                "-o", str(output_path),
                str(dvi_file),
            ]

        result = _run_quiet(svg_cmd, tmp_dir)
        if result.returncode != 0:
            raise RuntimeError(
                f"dvisvgm conversion failed:\n{(result.stderr or '')[-500:]}"
            )

    if not output_path.exists():
        raise RuntimeError(f"SVG output not created: {output_path}")

    return output_path


# ============================================================
# Manifest mode
# ============================================================

def load_manifest(manifest_path: Path) -> list[FormulaEntry]:
    """Load a formula_manifest.json file."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        entries = data.get("formulas", [])
    else:
        entries = data
    return [FormulaEntry.from_dict(e) for e in entries]


def save_manifest(manifest_path: Path, entries: list[FormulaEntry]) -> None:
    """Write formula entries back to formula_manifest.json."""
    payload = {
        "version": MANIFEST_VERSION,
        "formulas": [e.to_dict() for e in entries],
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _single_formula_id(output_path: Path) -> str:
    """Return the manifest id for a single-formula SVG output."""
    stem = output_path.stem
    if stem.startswith(SVG_FILENAME_PREFIX):
        return stem[len(SVG_FILENAME_PREFIX):]
    return stem


def maybe_register_single_formula(
    output_path: Path,
    *,
    latex: str,
    display: bool,
    source_file: str | None = None,
    line_number: int | None = None,
    context: str | None = None,
    title: str = "",
    desc: str = "",
) -> Path | None:
    """Upsert a one-off formula SVG into the nearby project manifest.

    This keeps ad hoc executor-generated files like formula_inline_901.svg
    discoverable by later asset stabilization and formula QA checks.
    """
    output_path = output_path.resolve()
    if not output_path.name.lower().endswith(".svg"):
        return None
    if not output_path.stem.startswith(SVG_FILENAME_PREFIX):
        return None

    manifest_path = output_path.parent / FORMULA_MANIFEST_FILENAME
    entries: list[FormulaEntry]
    if manifest_path.is_file():
        try:
            entries = load_manifest(manifest_path)
        except Exception:
            entries = []
    else:
        entries = []

    formula_id = _single_formula_id(output_path)
    width, height = _parse_svg_dimensions(output_path)
    entry = next(
        (
            item for item in entries
            if item.id == formula_id or Path(item.svg_path).name == output_path.name
        ),
        None,
    )
    if entry is None:
        entry = FormulaEntry(
            id=formula_id,
            latex=latex,
            display=display,
            render=True,
        )
        entries.append(entry)

    entry.latex = latex
    entry.display = display
    entry.render = True
    entry.status = "rendered"
    entry.svg_path = output_path.name
    entry.svg_width = width
    entry.svg_height = height
    entry.error = ""
    if source_file is not None:
        entry.source_file = source_file
    if line_number is not None:
        entry.line_number = line_number
    if context is not None:
        entry.context = context
    if title:
        entry.extra["svg_title"] = title
    if desc:
        entry.extra["svg_desc"] = desc

    save_manifest(manifest_path, entries)
    return manifest_path


def process_manifest(
    manifest_path: Path,
    *,
    border_pt: int = DEFAULT_BORDER_PT,
    force: bool = False,
) -> tuple[int, int, int]:
    """Process all render-flagged formulas in a manifest.

    Returns:
        (rendered_count, skipped_count, error_count)
    """
    manifest_path = manifest_path.resolve()
    images_dir = manifest_path.parent
    entries = load_manifest(manifest_path)

    compiler = _find_tex_compiler()
    dvisvgm = _find_dvisvgm()

    rendered = 0
    skipped = 0
    errors = 0

    for entry in entries:
        if not entry.render:
            skipped += 1
            continue
        if entry.status == "rendered" and not force:
            skipped += 1
            continue

        svg_name = f"{SVG_FILENAME_PREFIX}{entry.id}.svg"
        svg_path = images_dir / svg_name

        try:
            compile_formula_to_svg(
                entry.latex,
                svg_path,
                display=entry.display,
                border_pt=border_pt,
                compiler=compiler,
                dvisvgm_path=dvisvgm,
            )
            w, h = _parse_svg_dimensions(svg_path)
            title, desc = annotate_formula_svg(
                svg_path,
                formula_id=entry.id,
                latex=entry.latex,
                display=entry.display,
                source_file=entry.source_file,
                line_number=entry.line_number,
                context=entry.context,
                short_alias=str(entry.extra.get("short_alias", "") or ""),
            )
            entry.svg_path = svg_name
            entry.svg_width = w
            entry.svg_height = h
            entry.status = "rendered"
            entry.error = ""
            if title:
                entry.extra["svg_title"] = title
            if desc:
                entry.extra["svg_desc"] = desc
            rendered += 1
            print(f"  [OK] {entry.id} → {svg_name}", file=sys.stderr)
        except Exception as exc:
            entry.status = "error"
            entry.error = str(exc)[:300]
            errors += 1
            print(f"  [ERROR] {entry.id}: {exc}", file=sys.stderr)

    save_manifest(manifest_path, entries)
    return rendered, skipped, errors


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Convert LaTeX formulas to SVG using latex + dvisvgm.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "formula",
        nargs="?",
        help="LaTeX formula string (without delimiters). Omit when using --manifest.",
    )
    parser.add_argument(
        "-o", "--output",
        default="formula.svg",
        help="Output SVG file path (single-formula mode). Default: formula.svg",
    )
    parser.add_argument(
        "--manifest",
        metavar="JSON",
        help="Path to formula_manifest.json. Renders all entries with render=true.",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        default=True,
        help="Render as display math (default).",
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Render as inline math instead of display.",
    )
    parser.add_argument(
        "--border",
        type=int,
        default=DEFAULT_BORDER_PT,
        help=f"Border padding in TeX points (default: {DEFAULT_BORDER_PT}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-render formulas even if already marked rendered.",
    )
    parser.add_argument(
        "--source-file",
        default=None,
        help="Source file label to store in SVG metadata and formula_manifest.json (single-formula mode only).",
    )
    parser.add_argument(
        "--line-number",
        type=int,
        default=None,
        help="1-based source line number for the formula metadata (single-formula mode only).",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Short surrounding text to store as formula context metadata (single-formula mode only).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.manifest:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            print(f"[ERROR] Manifest not found: {manifest_path}", file=sys.stderr)
            return 1
        print(f"[INFO] Processing manifest: {manifest_path}", file=sys.stderr)
        rendered, skipped, errors = process_manifest(
            manifest_path,
            border_pt=args.border,
            force=args.force,
        )
        print(
            f"[OK] Done — rendered: {rendered}, skipped: {skipped}, errors: {errors}",
            file=sys.stderr,
        )
        return 1 if errors > 0 else 0

    if not args.formula:
        parser.error("Provide a formula string or use --manifest.")

    display = not args.inline
    output_path = Path(args.output)
    try:
        result_path = compile_formula_to_svg(
            args.formula,
            output_path,
            display=display,
            border_pt=args.border,
        )
        formula_id = _single_formula_id(result_path)
        title, desc = annotate_formula_svg(
            result_path,
            formula_id=formula_id,
            latex=args.formula,
            display=display,
            source_file=args.source_file or "",
            line_number=args.line_number,
            context=args.context or "",
        )
        manifest_path = maybe_register_single_formula(
            result_path,
            latex=args.formula,
            display=display,
            source_file=args.source_file,
            line_number=args.line_number,
            context=args.context,
            title=title,
            desc=desc,
        )
        print(str(result_path))
        print(f"[OK] SVG saved to: {result_path}", file=sys.stderr)
        if manifest_path is not None:
            print(f"[OK] Manifest updated: {manifest_path}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
