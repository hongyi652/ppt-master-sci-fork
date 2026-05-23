#!/usr/bin/env python3
"""
PPT Master - LaTeX Formula Extractor

Scan a MinerU-generated Markdown file for LaTeX formulas and produce a
formula_manifest.json for AI review and downstream SVG rendering.

Usage:
    python3 scripts/extract_formulas.py <markdown_file> -o <project_path>/images/formula_manifest.json
    python3 scripts/extract_formulas.py <project_path>/sources/paper.md

Examples:
    python3 scripts/extract_formulas.py projects/demo/sources/paper.md
    python3 scripts/extract_formulas.py paper.md -o projects/demo/images/formula_manifest.json
    python3 scripts/extract_formulas.py paper.md --min-length 8

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ============================================================
# Constants
# ============================================================

MANIFEST_VERSION = 1

# Regex patterns for LaTeX formulas in Markdown
# Display math: $$ ... $$ (possibly multiline)
DISPLAY_MATH_RE = re.compile(
    r"\$\$\s*\n?(.*?)\n?\s*\$\$",
    re.DOTALL,
)
# Inline math: $ ... $ (single line, non-greedy)
INLINE_MATH_RE = re.compile(
    r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
)
# LaTeX environments: \begin{equation}...\end{equation}, \begin{align}...\end{align}, etc.
ENVIRONMENT_RE = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|"
    r"aligned|cases|pmatrix|bmatrix|vmatrix|Vmatrix|matrix)\}"
    r"(.*?)"
    r"\\end\{\1\}",
    re.DOTALL,
)

# Minimum formula length to include (skip trivial single-character formulas)
DEFAULT_MIN_LENGTH = 4

# Trivial formula patterns to skip (single variables, plain numbers, etc.)
TRIVIAL_PATTERNS = [
    re.compile(r"^[a-zA-Z]$"),                  # single letter
    re.compile(r"^[0-9]+\.?[0-9]*$"),            # plain number
    re.compile(r"^[a-zA-Z]_?[0-9]?$"),           # x, x_1
    re.compile(r"^\\?(alpha|beta|gamma|delta|epsilon|theta|lambda|mu|pi|sigma|omega)$"),
]


# ============================================================
# Data structures
# ============================================================

@dataclass
class ExtractedFormula:
    """A formula extracted from Markdown source."""

    id: str
    latex: str
    display: bool
    context: str
    line_number: int
    source_file: str


# ============================================================
# Extraction logic
# ============================================================

def _is_trivial(formula: str) -> bool:
    """Check if a formula is too trivial to be worth rendering."""
    stripped = formula.strip()
    for pat in TRIVIAL_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def _make_formula_id(index: int, latex: str) -> str:
    """Generate a stable formula ID from index and content hash."""
    short_hash = hashlib.md5(latex.encode("utf-8")).hexdigest()[:6]
    return f"{index:03d}_{short_hash}"


def _get_context(lines: list[str], line_idx: int, radius: int = 1) -> str:
    """Extract surrounding context lines for a formula."""
    start = max(0, line_idx - radius)
    end = min(len(lines), line_idx + radius + 1)
    context_lines = []
    for i in range(start, end):
        text = lines[i].strip()
        # Skip the formula itself and empty lines
        if text and not text.startswith("$$") and not text.startswith("\\begin{"):
            context_lines.append(text)
    return " ".join(context_lines)[:200]


def extract_formulas_from_markdown(
    content: str,
    *,
    source_file: str = "",
    min_length: int = DEFAULT_MIN_LENGTH,
) -> list[ExtractedFormula]:
    """Extract all LaTeX formulas from Markdown content.

    Args:
        content: Markdown text content.
        source_file: Source filename for provenance.
        min_length: Minimum formula string length to include.

    Returns:
        List of extracted formulas.
    """
    lines = content.splitlines()
    results: list[ExtractedFormula] = []
    seen_hashes: set[str] = set()
    index = 0

    def _add(latex: str, display: bool, line_num: int) -> None:
        nonlocal index
        stripped = latex.strip()
        if len(stripped) < min_length:
            return
        if _is_trivial(stripped):
            return
        content_hash = hashlib.md5(stripped.encode("utf-8")).hexdigest()
        if content_hash in seen_hashes:
            return
        seen_hashes.add(content_hash)
        index += 1
        results.append(ExtractedFormula(
            id=_make_formula_id(index, stripped),
            latex=stripped,
            display=display,
            context=_get_context(lines, line_num),
            line_number=line_num + 1,
            source_file=source_file,
        ))

    # Pass 1: display math $$...$$
    for match in DISPLAY_MATH_RE.finditer(content):
        formula_text = match.group(1).strip()
        line_num = content[:match.start()].count("\n")
        _add(formula_text, display=True, line_num=line_num)

    # Pass 2: LaTeX environments
    for match in ENVIRONMENT_RE.finditer(content):
        env_name = match.group(1)
        inner = match.group(2).strip()
        full_formula = f"\\begin{{{env_name}}}{inner}\\end{{{env_name}}}"
        line_num = content[:match.start()].count("\n")
        _add(full_formula, display=True, line_num=line_num)

    # Pass 3: inline math $...$
    for match in INLINE_MATH_RE.finditer(content):
        formula_text = match.group(1).strip()
        line_num = content[:match.start()].count("\n")
        _add(formula_text, display=False, line_num=line_num)

    return results


# ============================================================
# Manifest I/O
# ============================================================

def build_manifest(
    formulas: list[ExtractedFormula],
    source_file: str = "",
) -> dict:
    """Build a formula_manifest.json structure."""
    entries = []
    for f in formulas:
        entries.append({
            "id": f.id,
            "latex": f.latex,
            "display": f.display,
            "render": False,
            "context": f.context,
            "line_number": f.line_number,
            "source_file": f.source_file or source_file,
            "status": "pending",
        })
    return {
        "version": MANIFEST_VERSION,
        "source_file": source_file,
        "total_formulas": len(entries),
        "formulas": entries,
    }


def save_manifest(manifest: dict, output_path: Path) -> None:
    """Write formula manifest to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Extract LaTeX formulas from Markdown and produce formula_manifest.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        help="Markdown file to scan for LaTeX formulas.",
    )
    parser.add_argument(
        "-o", "--output",
        help=(
            "Output formula_manifest.json path. Default: "
            "<input_dir>/formula_manifest.json or <project>/images/formula_manifest.json"
        ),
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=DEFAULT_MIN_LENGTH,
        help=f"Minimum formula length to include (default: {DEFAULT_MIN_LENGTH}).",
    )
    parser.add_argument(
        "--include-inline",
        action="store_true",
        default=True,
        help="Include inline ($...$) formulas (default: yes).",
    )
    parser.add_argument(
        "--display-only",
        action="store_true",
        help="Extract only display ($$...$$) formulas, skip inline.",
    )
    return parser


def _guess_output_path(input_path: Path) -> Path:
    """Infer the output manifest path from the input file location."""
    # If inside a project sources/ dir, put manifest in sibling images/
    if input_path.parent.name == "sources":
        project_dir = input_path.parent.parent
        return project_dir / "images" / "formula_manifest.json"
    return input_path.parent / "formula_manifest.json"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        return 1

    content = input_path.read_text(encoding="utf-8", errors="replace")
    output_path = Path(args.output) if args.output else _guess_output_path(input_path)

    formulas = extract_formulas_from_markdown(
        content,
        source_file=input_path.name,
        min_length=args.min_length,
    )

    if args.display_only:
        formulas = [f for f in formulas if f.display]

    if not formulas:
        print("[INFO] No LaTeX formulas found in the input file.", file=sys.stderr)
        return 0

    manifest = build_manifest(formulas, source_file=input_path.name)
    save_manifest(manifest, output_path)

    display_count = sum(1 for f in formulas if f.display)
    inline_count = sum(1 for f in formulas if not f.display)

    print(str(output_path))
    print(
        f"[OK] Extracted {len(formulas)} formula(s) "
        f"({display_count} display, {inline_count} inline) → {output_path}",
        file=sys.stderr,
    )
    print(
        "[INFO] Review formula_manifest.json and set \"render\": true for "
        "formulas to include in the presentation. Then run:\n"
        f"  python3 scripts/latex_to_svg.py --manifest {output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
