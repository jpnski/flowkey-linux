"""Shared JSON HTTP helpers for local Flowkey clients."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("flowkey.http")

DAEMON_API_HEADER = "X-FFP-API"
DAEMON_API_VERSION = "1"
DAEMON_BASE_URL = "http://127.0.0.1:52650"


def daemon_headers() -> dict[str, str]:
    """Headers required by daemon for state-changing POST requests."""
    return {DAEMON_API_HEADER: DAEMON_API_VERSION}


def json_get(url: str, *, headers: dict[str, str] | None = None, timeout: float = 3.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        log.warning("GET %s failed: HTTP %s", url, exc.code)
        raise
    except urllib.error.URLError as exc:
        log.warning("GET %s failed: %s", url, exc)
        raise


def json_post(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        log.warning("POST %s failed: HTTP %s", url, exc.code)
        raise
    except urllib.error.URLError as exc:
        log.warning("POST %s failed: %s", url, exc)
        raise


def daemon_post(action: str, args: dict[str, Any] | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    """POST to a daemon action endpoint and return the parsed JSON response."""
    try:
        return json_post(
            f"{DAEMON_BASE_URL}/action/{action}",
            {"args": args or {}},
            headers=daemon_headers(),
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
