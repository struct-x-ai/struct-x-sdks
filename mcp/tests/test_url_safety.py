"""Focused tests for the URL-safety module — SSRF defense rules."""
from __future__ import annotations

import pytest

from structx_mcp._url_safety import UrlSafetyError, validate_fetch_url


@pytest.mark.parametrize(
    "url, match",
    [
        ("", "empty"),
        ("file:///etc/passwd", "https"),
        ("http://example.com", "https"),
        ("ftp://example.com/", "https"),
        ("gopher://example.com/", "https"),
        ("https://", "hostname"),
        ("https://169.254.169.254/", "blocked"),
        ("https://metadata.google.internal/", "internal"),
        ("https://kube-master.cluster.local/", "internal"),
        # Port allowlist (Phase 5.5 / chi): only port 443 (or default) is
        # allowed. Non-HTTPS ports on a public host can scan internal
        # services via timing oracles or reach internal services via
        # a public-IP proxy.
        ("https://example.com:25/", "port"),
        ("https://example.com:6379/", "port"),
        ("https://example.com:11211/", "port"),
        ("https://example.com:8080/", "port"),
    ],
)
def test_rejected_urls(url, match):
    with pytest.raises(UrlSafetyError, match=match):
        validate_fetch_url(url)


def test_accepts_public_https():
    """A clearly public URL is fine. (Uses example.com which is RFC-2606
    reserved but resolves to a real public IP for the DNS check.)"""
    url, host = validate_fetch_url("https://example.com/foo?bar=baz")
    assert host == "example.com"
    assert url.endswith("?bar=baz")


def test_accepts_explicit_port_443():
    """https://host:443/path is the same as https://host/path — must
    be allowed (Phase 5.5 / chi port allowlist regression guard)."""
    url, host = validate_fetch_url("https://example.com:443/foo")
    assert host == "example.com"
