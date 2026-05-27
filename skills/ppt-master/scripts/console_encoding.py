#!/usr/bin/env python3
"""
PPT Master - Console Encoding Helpers

Normalize stdout/stderr encoding for Windows console sessions so Unicode
status text does not crash CLI tools.

Usage:
    from console_encoding import configure_utf8_stdio
    configure_utf8_stdio()

Examples:
    python3 scripts/convert_pdf.py paper.pdf -o projects/demo/sources/paper.md

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

import io
import os
import sys


def configure_utf8_stdio() -> None:
    """Force UTF-8 stdout/stderr on Windows and mark child processes likewise."""
    if sys.platform != "win32":
        return

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
                continue
            except Exception:
                pass
        if hasattr(stream, "buffer"):
            wrapped = io.TextIOWrapper(
                stream.buffer,
                encoding="utf-8",
                errors="replace",
            )
            setattr(sys, stream_name, wrapped)

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
