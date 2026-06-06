"""System tray icon for Flowkey (Linux).

Port of the AHK tray menu. Uses pystray (X11) or dasbus StatusNotifierItem
(Wayland). Falls back gracefully if neither is available.

See TODO.md Phase 4 for the full implementation plan.
"""

from __future__ import annotations

import sys


def main() -> int:
    print("ffp-tray: not yet implemented — see TODO.md Phase 4", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
