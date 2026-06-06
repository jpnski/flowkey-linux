from __future__ import annotations

import ffp_notify


def test_xml_escape_neutralizes_injection():
    out = ffp_notify.xml_escape("a'@\n<b>&\"x")
    assert "'" not in out
    assert "\n" not in out
    assert "<" not in out and ">" not in out
    assert "&apos;" in out and "&lt;" in out and "&quot;" in out
