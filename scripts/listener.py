"""Global hotkey listener for Flowkey (Linux).

Port of the AutoHotkey frontend. Handles global hotkey capture via pynput
(X11) or evdev (Wayland), clipboard operations, and daemon dispatch.

See TODO.md Phase 3 for the full implementation plan.
"""

from __future__ import annotations

import sys


def main() -> int:
    print("ffp-listener: not yet implemented — see TODO.md Phase 3", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
