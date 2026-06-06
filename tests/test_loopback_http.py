from __future__ import annotations

from loopback_http import daemon_headers


def test_daemon_headers_contains_api_version():
    hdrs = daemon_headers()
    assert hdrs["X-FFP-API"] == "1"
