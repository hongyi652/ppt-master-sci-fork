from __future__ import annotations

from pathlib import Path


_PROJECT_MARKERS = ("sources", "notes")


def find_project_root(path: Path, *, treat_as_output_file: bool = False) -> Path | None:
    """Return the nearest enclosing project root, if any."""
    resolved = path.resolve()
    start = resolved.parent if treat_as_output_file or not resolved.is_dir() else resolved
    for candidate in (start, *start.parents):
        if all((candidate / marker).is_dir() for marker in _PROJECT_MARKERS):
            return candidate
    return None


def resolve_project_bound_markdown_output(
    input_path: Path,
    output_path: str | Path | None,
    *,
    default_suffix: str = ".md",
) -> Path:
    """Resolve a Markdown output path and refuse project-external outputs."""
    input_path = input_path.resolve()
    input_project = find_project_root(input_path)
    candidate = Path(output_path) if output_path else input_path.with_suffix(default_suffix)
    candidate = candidate.resolve()
    output_project = find_project_root(candidate, treat_as_output_file=True)

    if input_project is None and output_path is None:
        raise ValueError(
            "Refusing to write conversion artifacts next to the original source file. "
            "Create the project first and use project_manager.py import-sources, or pass "
            "-o <project_path>/sources/<name>.md explicitly."
        )
    if output_project is None:
        raise ValueError(
            "Refusing to write conversion artifacts outside a project tree. Pass "
            "-o <project_path>/sources/<name>.md explicitly or use project_manager.py import-sources."
        )
    if input_project is not None and output_project != input_project:
        raise ValueError(
            "Refusing to write conversion artifacts to a different project tree than the source file. "
            "Keep intermediates inside the same project."
        )
    return candidate


def resolve_project_bound_directory(
    input_path: Path,
    output_dir: str | Path | None,
) -> Path:
    """Resolve a directory output path and refuse project-external outputs."""
    input_path = input_path.resolve()
    input_project = find_project_root(input_path)
    if output_dir is None:
        if input_project is None:
            raise ValueError(
                "Refusing to write conversion artifacts next to the original source directory. "
                "Create the project first and pass -o <project_path>/sources explicitly."
            )
        return input_path

    candidate = Path(output_dir).resolve()
    output_project = find_project_root(candidate)
    if output_project is None:
        raise ValueError(
            "Refusing to write conversion artifacts outside a project tree. Pass "
            "-o <project_path>/sources or use project_manager.py import-sources."
        )
    if input_project is not None and output_project != input_project:
        raise ValueError(
            "Refusing to write conversion artifacts to a different project tree than the source directory. "
            "Keep intermediates inside the same project."
        )
    return candidate


def ensure_project_bound_output_path(output_path: str | Path) -> Path:
    """Validate that an explicit output file path lives inside a project tree."""
    candidate = Path(output_path).resolve()
    if find_project_root(candidate, treat_as_output_file=True) is None:
        raise ValueError(
            "Refusing to write conversion artifacts outside a project tree. Pass "
            "-o <project_path>/sources/<name>.md or use project_manager.py import-sources."
        )
    return candidate


def ensure_project_bound_directory(output_dir: str | Path) -> Path:
    """Validate that an explicit output directory lives inside a project tree."""
    candidate = Path(output_dir).resolve()
    if find_project_root(candidate) is None:
        raise ValueError(
            "Refusing to write conversion artifacts outside a project tree. Pass "
            "-d <project_path>/sources or use project_manager.py import-sources."
        )
    return candidate