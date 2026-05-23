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
    python3 scripts/latex_to_svg.py --manifest projects/demo/images/formula_manifest.json
    python3 scripts/latex_to_svg.py --manifest projects/demo/images/formula_manifest.json --font-size 14

Dependencies:
    latex (or xelatex/pdflatex) and dvisvgm — provided by MiKTeX or TeX Live
"""

from __future__ import annotations

import argparse
import hashlib
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
            "source_page", "status", "svg_path", "svg_width",
            "svg_height", "error",
        }
        extra = {k: v for k, v in d.items() if k not in known_keys}
        return cls(
            id=d.get("id", ""),
            latex=d.get("latex", ""),
            display=d.get("display", True),
            render=d.get("render", False),
            context=d.get("context", ""),
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
            entry.svg_path = svg_name
            entry.svg_width = w
            entry.svg_height = h
            entry.status = "rendered"
            entry.error = ""
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
        print(str(result_path))
        print(f"[OK] SVG saved to: {result_path}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
