#!/usr/bin/env python3
"""
PPT Master - Python Environment Detection

Shared helper for detecting a working Python >= 3.10 command on the current
system.  Used by preflight_check.py, start_live_preview.py, and any script
that needs to shell out to the Python interpreter by name.

Usage (library):
    from python_env import detect_python_command
    cmd = detect_python_command()  # e.g. "python3", "python", "py -3"

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

import subprocess
from ast import literal_eval

# Minimum Python version to accept
MIN_PYTHON = (3, 10)


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
            version_tuple = literal_eval(result.stdout.strip())  # e.g. (3, 11)
            if version_tuple >= MIN_PYTHON:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            continue
    raise RuntimeError(
        "No working Python >= 3.10 found. Tried: " + ", ".join(candidates)
    )
