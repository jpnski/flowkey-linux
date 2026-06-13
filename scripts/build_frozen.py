"""Build the frozen Flowkey binary via PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    _ = argv  # reserved for future build flags
    root = Path(__file__).resolve().parent.parent
    spec = root / "flowkey.spec"

    try:
        from PyInstaller.__main__ import run as pyinstaller_run
    except ImportError as exc:
        print("PyInstaller is required. Install `.[dev]` or `pyinstaller>=6`.", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 1

    pyinstaller_run(["--noconfirm", "--clean", str(spec)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
