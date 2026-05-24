#!/usr/bin/env python3
"""
PPT Master - Project Preflight Check

One-shot pre-run environment and project sanity check.  Validates the Python
interpreter, required/optional dependencies, LaTeX/dvisvgm availability,
icon library integrity, live-preview port, source file paths, and output
directory permissions — catching common issues before a long generation run.

Usage:
    python3 scripts/preflight_check.py <project_path>
    python3 scripts/preflight_check.py <project_path> --preview-port 5051

Examples:
    python3 scripts/preflight_check.py projects/my_project_ppt169_20260523
    python3 scripts/preflight_check.py projects/my_project_ppt169_20260523 --strict

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _SCRIPTS_DIR.parent
_REPO_ROOT = _SKILL_DIR.parent.parent
_ICON_DIR = _SKILL_DIR / "templates" / "icons"

# Minimum Python version
MIN_PYTHON = (3, 10)

# Required and optional pip packages
REQUIRED_PACKAGES = [
    ("python-pptx", "python_pptx"),
    ("Pillow", "PIL"),
    ("requests", "requests"),
]
OPTIONAL_PACKAGES = [
    ("cairosvg", "cairosvg", "SVG → PNG fallback (Office compat)"),
    ("svglib", "svglib", "SVG → PNG fallback (lightweight)"),
    ("reportlab", "reportlab", "PDF/SVG rendering backend for svglib"),
    ("edge-tts", "edge_tts", "TTS narration generation"),
    ("curl_cffi", "curl_cffi", "TLS impersonation for WeChat URLs"),
]

# Icon sub-libraries expected to exist
EXPECTED_ICON_LIBS = [
    "chunk-filled",
    "tabler-filled",
    "tabler-outline",
    "phosphor-duotone",
    "simple-icons",
]

# Default live-preview port
DEFAULT_PREVIEW_PORT = 5050


# ------------------------------------------------------------------
# Check helpers — each returns (passed: bool, message: str)
# ------------------------------------------------------------------

def detect_python_command() -> str:
    """Detect a working Python >= 3.10 command for this system.

    Tries candidates in order: python3, python, py -3.
    On Windows the Microsoft Store ``python3.exe`` alias returns exit
    code 49 when no real install backs it, so we verify each candidate
    actually runs and reports a valid version.

    Returns the first working command string (e.g. ``python3``,
    ``python``, or ``py -3``).  Raises RuntimeError if none works.
    """
    candidates = ["python3", "python", "py -3"]
    for cmd in candidates:
        try:
            parts = cmd.split()
            result = subprocess.run(
                [*parts, "-c", "import sys; print(sys.version_info[:2])"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                continue
            version_tuple = eval(result.stdout.strip())  # e.g. (3, 11)
            if version_tuple >= MIN_PYTHON:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            continue
    raise RuntimeError(
        "No working Python >= 3.10 found. Tried: " + ", ".join(candidates)
    )


def _check_python_version() -> tuple[bool, str]:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return True, f"Python {version_str}"
    return False, f"Python {version_str} — need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}"


def _check_required_packages() -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    for pip_name, import_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
            results.append((True, f"{pip_name} — installed"))
        except ImportError:
            results.append((False, f"{pip_name} — MISSING (pip install {pip_name})"))
    return results


def _check_optional_packages() -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    for pip_name, import_name, purpose in OPTIONAL_PACKAGES:
        try:
            importlib.import_module(import_name)
            results.append((True, f"{pip_name} — installed ({purpose})"))
        except ImportError:
            results.append((True, f"{pip_name} — not installed (optional: {purpose})"))
    return results


def _check_latex() -> tuple[bool, str]:
    latex_bin = shutil.which("latex") or shutil.which("pdflatex") or shutil.which("xelatex")
    if latex_bin:
        return True, f"LaTeX — {latex_bin}"
    return False, "LaTeX — NOT FOUND (formula SVG rendering will fail)"


def _check_dvisvgm() -> tuple[bool, str]:
    dvisvgm_bin = shutil.which("dvisvgm")
    if dvisvgm_bin:
        return True, f"dvisvgm — {dvisvgm_bin}"
    return False, "dvisvgm — NOT FOUND (formula SVG rendering will fail)"


def _check_icon_library() -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    if not _ICON_DIR.is_dir():
        results.append((False, f"Icon library directory missing: {_ICON_DIR}"))
        return results
    for lib_name in EXPECTED_ICON_LIBS:
        lib_path = _ICON_DIR / lib_name
        if lib_path.is_dir():
            count = sum(1 for f in lib_path.iterdir() if f.suffix.lower() == ".svg")
            results.append((True, f"icons/{lib_name} — {count} SVGs"))
        else:
            results.append((False, f"icons/{lib_name} — MISSING"))
    return results


def _check_port_available(port: int) -> tuple[bool, str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.bind(("127.0.0.1", port))
        return True, f"Port {port} — available"
    except OSError:
        return True, f"Port {port} — in use (live preview may already be running)"


def _check_source_files(project_dir: Path) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    sources_dir = project_dir / "sources"
    if not sources_dir.is_dir():
        results.append((False, "sources/ — directory missing"))
        return results
    md_files = sorted(sources_dir.glob("*.md"))
    if md_files:
        results.append((True, f"sources/ — {len(md_files)} Markdown file(s)"))
    else:
        results.append((False, "sources/ — no Markdown files found"))
    return results


def _check_output_dirs(project_dir: Path) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    for dirname in ("svg_output", "svg_final", "images", "notes", "exports"):
        dir_path = project_dir / dirname
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            results.append((True, f"{dirname}/ — created"))
            continue
        # Verify writable
        try:
            probe = dir_path / ".preflight_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            results.append((True, f"{dirname}/ — writable"))
        except OSError as exc:
            results.append((False, f"{dirname}/ — NOT WRITABLE ({exc})"))
    return results


def _check_mineru_token() -> tuple[bool, str]:
    """Check whether a MinerU API token is configured."""
    for key in ("MINERU_API_TOKEN", "MINERU_API_KEY", "MINERU_TOKEN"):
        if os.environ.get(key, "").strip():
            return True, f"MinerU API token — set via ${key}"

    # Also check .env files
    for env_path in [Path.cwd() / ".env", _REPO_ROOT / ".env"]:
        if env_path.is_file():
            try:
                content = env_path.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[7:].lstrip()
                    key = line.split("=", 1)[0].strip()
                    if key in ("MINERU_API_TOKEN", "MINERU_API_KEY", "MINERU_TOKEN"):
                        return True, f"MinerU API token — found in {env_path}"
            except OSError:
                pass

    return False, "MinerU API token — NOT SET (PDF conversion will fail)"


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------

def run_preflight(
    project_path: str,
    *,
    preview_port: int = DEFAULT_PREVIEW_PORT,
) -> dict[str, object]:
    """Run all preflight checks and return a structured report."""
    project_dir = Path(project_path).resolve()
    checks: list[dict[str, object]] = []
    has_error = False

    def _record(category: str, passed: bool, message: str) -> None:
        nonlocal has_error
        checks.append({"category": category, "passed": passed, "message": message})
        if not passed:
            has_error = True

    # Python
    ok, msg = _check_python_version()
    _record("python", ok, msg)

    # Detect the correct Python command for this system
    try:
        python_cmd = detect_python_command()
        _record("python", True, f"Python command: {python_cmd}")
    except RuntimeError as exc:
        python_cmd = "python3"
        _record("python", False, str(exc))

    # Required packages
    for ok, msg in _check_required_packages():
        _record("dependency", ok, msg)

    # Optional packages
    for ok, msg in _check_optional_packages():
        _record("optional", ok, msg)

    # LaTeX / dvisvgm
    ok, msg = _check_latex()
    _record("latex", ok, msg)
    ok, msg = _check_dvisvgm()
    _record("dvisvgm", ok, msg)

    # MinerU token
    ok, msg = _check_mineru_token()
    _record("mineru", ok, msg)

    # Icon library
    for ok, msg in _check_icon_library():
        _record("icons", ok, msg)

    # Preview port
    ok, msg = _check_port_available(preview_port)
    _record("port", ok, msg)

    # Project-specific checks
    if project_dir.is_dir():
        for ok, msg in _check_source_files(project_dir):
            _record("sources", ok, msg)
        for ok, msg in _check_output_dirs(project_dir):
            _record("output", ok, msg)
    else:
        _record("project", False, f"Project directory not found: {project_dir}")

    return {
        "project": str(project_dir),
        "python_cmd": python_cmd,
        "all_passed": not has_error,
        "checks": checks,
    }


def print_report(report: dict[str, object]) -> None:
    """Pretty-print the preflight report to stderr."""
    checks = list(report.get("checks") or [])
    all_passed = report.get("all_passed", False)

    print("\n" + "=" * 60, file=sys.stderr)
    print("PPT Master — Preflight Check", file=sys.stderr)
    print(f"Project: {report.get('project', '?')}", file=sys.stderr)
    python_cmd = report.get("python_cmd", "python3")
    print(f"Python command: {python_cmd}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    current_category = ""
    for check in checks:
        category = str(check.get("category", ""))
        passed = check.get("passed", False)
        message = str(check.get("message", ""))
        icon = "✓" if passed else "✗"
        if category != current_category:
            print(f"\n  [{category}]", file=sys.stderr)
            current_category = category
        print(f"    {icon} {message}", file=sys.stderr)

    print("\n" + "-" * 60, file=sys.stderr)
    if all_passed:
        print("  Result: ALL CHECKS PASSED", file=sys.stderr)
    else:
        failed = [c for c in checks if not c.get("passed")]
        print(f"  Result: {len(failed)} issue(s) found", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    # Machine-readable line for AI agents to parse the Python command
    print(f"\nPYTHON_CMD={python_cmd}", file=sys.stderr)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Pre-run environment and project sanity check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", help="Project directory path.")
    parser.add_argument(
        "--preview-port", type=int, default=DEFAULT_PREVIEW_PORT,
        help=f"Live-preview port to check (default: {DEFAULT_PREVIEW_PORT}).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output report as JSON to stdout.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit with code 1 if any check fails.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    report = run_preflight(args.project, preview_port=args.preview_port)
    print_report(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.strict and not report.get("all_passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
