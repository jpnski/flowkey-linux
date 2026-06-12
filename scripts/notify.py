"""Desktop notifications for Flowkey (Linux).

Uses notify-send if D-Bus is available, falls back to print-to-stderr.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import xml.sax.saxutils

log = logging.getLogger("flowkey.notify")

_NOTIFY_SEND_AVAILABLE: bool = shutil.which("notify-send") is not None


def xml_escape(s: str) -> str:
    s = xml.sax.saxutils.escape(str(s or ""), {'"': "&quot;", "'": "&apos;"})
    return s.replace("\r\n", " ").replace("\n", " ")


def show_toast_async(title: str, message: str) -> None:
    """Fire-and-forget desktop notification.

    Tries notify-send first. Falls back to stderr if D-Bus is unavailable
    or notify-send is not installed.
    """
    title_t = str(title or "Flowkey")[:64]
    message_t = str(message or "")[:512]

    if _NOTIFY_SEND_AVAILABLE:
        try:
            subprocess.Popen(
                ["notify-send", title_t, message_t],
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception as exc:
            log.debug("notify-send failed: %s", exc)

    # Fallback: print to stderr (works in any terminal, no D-Bus needed).
    print(f"[Flowkey] {title_t}: {message_t}", file=sys.stderr)
