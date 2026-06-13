"""Single top-level Flowkey CLI dispatcher."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterable

COMMANDS: dict[str, tuple[str, str, str]] = {
    "daemon": ("daemon", "main", "Start the action daemon"),
    "process": ("engine", "main", "Run the text-processing pipeline"),
    "install": ("install", "main", "Run the system/setup installer"),
    "listen": ("listener", "main", "Start global hotkey listening"),
    "tray": ("tray", "main", "Start the tray indicator"),
    "tui": ("tui.app", "main", "Launch the Textual TUI"),
}


def _top_help() -> str:
    lines = ["usage: flowkey <command> [args...]", "", "Commands:"]
    for name, (_module, _func, desc) in COMMANDS.items():
        lines.append(f"  {name:<8} {desc}")
    lines.append("")
    lines.append("Use `flowkey <command> --help` for command-specific help where available.")
    return "\n".join(lines)


def _dispatch(module_name: str, func_name: str, argv: list[str]) -> int:
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    result = func(argv)
    return int(result or 0)


def main(argv: Iterable[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(_top_help())
        return 0
    if args[0] in {"-V", "--version"}:
        try:
            from importlib.metadata import version

            print(version("flowkey"))
        except Exception:
            print("0.0.0")
        return 0

    cmd = args[0]
    entry = COMMANDS.get(cmd)
    if entry is None:
        print(f"Unknown command: {cmd}\n", file=sys.stderr)
        print(_top_help(), file=sys.stderr)
        return 2

    module_name, func_name, _desc = entry
    return _dispatch(module_name, func_name, args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
