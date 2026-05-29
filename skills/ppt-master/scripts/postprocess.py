#!/usr/bin/env python3
"""
PPT Master - Post-processing Pipeline Runner

Runs the three post-processing steps sequentially:
  1. total_md_split  — split speaker notes
  2. finalize_svg    — SVG post-processing (icon embed, image align, etc.)
  3. svg_to_pptx     — export to PPTX

Stops on the first failure and reports which step failed.

Usage:
    python3 scripts/postprocess.py <project_path>
    python3 scripts/postprocess.py <project_path> --merge-paragraphs

Examples:
    python3 scripts/postprocess.py projects/my_project_ppt169_20260528
    python3 scripts/postprocess.py projects/my_project_ppt169_20260528 --merge-paragraphs

Dependencies:
    None (only uses standard library; delegates to sibling scripts)
"""

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from pipeline_state import update_pipeline_state  # noqa: E402


def _update_pipeline_state(
    project_path: Path, step_name: str, status: str,
    *, detail: str | None = None,
) -> None:
    """Write or update pipeline_state.json with the given step status."""
    update_pipeline_state(project_path, step_name, status, detail=detail)


def _run_step(
    step_name: str,
    script: str,
    argv: list[str],
    *,
    project_path: Path,
) -> int:
    """Run a single pipeline step as a subprocess.

    Returns the process exit code (0 = success).
    """
    cmd = [sys.executable, str(_SCRIPTS_DIR / script), *argv]
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Step: {step_name}", file=sys.stderr)
    print(f"  Command: {' '.join(cmd)}", file=sys.stderr)
    print(f"{'=' * 50}\n", file=sys.stderr)

    _update_pipeline_state(project_path, step_name, "running")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        _update_pipeline_state(project_path, step_name, "done")
    else:
        _update_pipeline_state(
            project_path, step_name, "failed",
            detail=f"exit code {result.returncode}",
        )
        print(
            f"\n[ERROR] Step '{step_name}' failed (exit code {result.returncode}). "
            f"Pipeline stopped.",
            file=sys.stderr,
        )
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="PPT Master - Post-processing Pipeline Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Runs sequentially: total_md_split → finalize_svg → svg_to_pptx.
Stops on the first failure.

Examples:
  %(prog)s projects/my_project_ppt169_20260528
  %(prog)s projects/my_project_ppt169_20260528 --merge-paragraphs
""",
    )
    parser.add_argument(
        "project_path", type=Path, help="Project directory path",
    )
    parser.add_argument(
        "--merge-paragraphs", action="store_true",
        help="Pass --merge-paragraphs to svg_to_pptx for paragraph-level "
             "editable text frames",
    )
    parser.add_argument(
        "--skip-notes", action="store_true",
        help="Skip the total_md_split step (useful when speaker notes were "
             "not generated)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the post-processing pipeline. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    project_path = args.project_path.resolve()
    if not project_path.is_dir():
        print(
            f"Error: project directory does not exist: {project_path}",
            file=sys.stderr,
        )
        return 1

    project_str = str(project_path)
    _update_pipeline_state(project_path, "postprocess", "running")

    # Step 1: total_md_split (speaker notes)
    total_md = project_path / "notes" / "total.md"
    if args.skip_notes:
        print("[SKIP] total_md_split — skipped via --skip-notes",
              file=sys.stderr)
        _update_pipeline_state(project_path, "total_md_split", "skipped")
    elif not total_md.exists():
        print(
            "[SKIP] total_md_split — notes/total.md not found "
            "(speaker notes were not generated; use --skip-notes to suppress "
            "this message)",
            file=sys.stderr,
        )
        _update_pipeline_state(project_path, "total_md_split", "skipped")
    else:
        rc = _run_step(
            "total_md_split", "total_md_split.py", [project_str],
            project_path=project_path,
        )
        if rc != 0:
            _update_pipeline_state(
                project_path, "postprocess", "failed",
                detail="total_md_split failed",
            )
            return rc

    # Step 2: finalize_svg
    rc = _run_step(
        "finalize_svg", "finalize_svg.py", [project_str],
        project_path=project_path,
    )
    if rc != 0:
        _update_pipeline_state(
            project_path, "postprocess", "failed",
            detail="finalize_svg failed",
        )
        return rc

    # Step 3: svg_to_pptx
    pptx_argv = [project_str]
    if args.merge_paragraphs:
        pptx_argv.append("--merge-paragraphs")
    rc = _run_step(
        "svg_to_pptx", "svg_to_pptx.py", pptx_argv,
        project_path=project_path,
    )
    if rc != 0:
        _update_pipeline_state(
            project_path, "postprocess", "failed",
            detail="svg_to_pptx failed",
        )
        return rc

    _update_pipeline_state(project_path, "postprocess", "done")
    print(
        f"\n[OK] All 3 post-processing steps completed successfully.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
