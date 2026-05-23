#!/usr/bin/env python3
"""Removed native PDF parser shim.

PPT Master now uses MinerU as the only supported PDF parser.
This entry point is kept only to provide a clear migration error.
"""

from __future__ import annotations

import sys


MESSAGE = """[ERROR] Native PDF parsing has been removed from PPT Master.
Use MinerU instead:
  python3 scripts/source_to_md/mineru_to_md.py <file.pdf>
or import through the project manager:
  python3 scripts/project_manager.py import-sources <project_path> <file.pdf> --move
"""


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
