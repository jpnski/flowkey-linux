"""ffchat — single-command TUI chat frontend for FastFlowLM.

Usage:
    ffchat [--parent-pid N] [--ingest-file PATH] [--log-level LEVEL]

    -h, --help            show this help
    -V, --version         show version
    --parent-pid N        exit when this PID disappears
    --ingest-file PATH    ingest a JSON payload on TUI startup
    --log-level LEVEL     set log level (DEBUG, INFO, WARNING, ERROR)
"""

from __future__ import annotations

import sys

import version
from tui.app import main as tui_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return tui_main()
    if args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] in ("-V", "--version"):
        print(version.APP_VERSION)
        return 0
    # Everything else passed through to the TUI.
    return tui_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
