#!/usr/bin/env python3
"""
PPT Master - Pipeline State Helper

Shared helper for recording coarse workflow progress in a project-local
pipeline_state.json file.

Usage (library):
    from pipeline_state import update_pipeline_state
    update_pipeline_state(project_path, "svg_quality_check", "done")

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def update_pipeline_state(
    project_path: str | Path,
    step_name: str,
    status: str,
    *,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Write or update one step in ``pipeline_state.json``.

    Returns False when ``project_path`` is not an existing directory. Invalid
    existing state is replaced with a fresh object instead of blocking the
    caller's primary workflow.
    """
    project_dir = Path(project_path)
    if not project_dir.is_dir():
        return False

    state_path = project_dir / "pipeline_state.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError):
            state = {}

    steps = state.setdefault("steps", {})
    if not isinstance(steps, dict):
        steps = {}
        state["steps"] = steps

    payload: dict[str, Any] = {
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if detail:
        payload["detail"] = detail
    if extra:
        payload.update(extra)
    steps[step_name] = payload

    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True
