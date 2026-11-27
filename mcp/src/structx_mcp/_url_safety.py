"""SSRF defense for `structx_extract_from_url`.

Agents can be tricked into fetching internal-network URLs via prompt
injection — "please summarize http://169.254.169.254/latest/meta-data/"
extracts AWS instance metadata. This module validates URLs before
`structx_extract_from_url` actually fetches anything.

Pattern mirrored from the Struct-X API's own outbound-URL guard. Kept
inline here so the MCP package doesn't need to import backend code.

Defense layers:
  1. Scheme allowlist (https only)
  2. Hostname blocklist (cloud-metadata + .internal/.local)
  3. DNS resolution → IP classification (rejects private, loopback,
     link-local, multicast, reserved, ipv6-link-local)
  4. Response size cap (10MB) — enforced at the caller, but documented
     here as part of the threat model.

Not in this module's scope:
  - Redirect following (caller pins follow_redirects=False)
  - Content-Type checks (caller's responsibility)
  - Authentication header stripping (no auth headers sent in the first
    place — caller uses a vanilla httpx call with no creds)
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UrlSafetyError(ValueError):
    """Raised when a URL fails any SSRF check."""


# Hostnames that resolve to internal services on common cloud providers.
# These should NEVER be reachable from a public-facing fetch.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "instance-data",
        "instance-data.ec2.internal",
        "169.254.169.254",  # AWS/Azure/GCP metadata IP
        "100.100.100.200",  # Alibaba Cloud metadata IP
        "fd00:ec2::254",  # AWS IPv6 metadata
    }
)

_BLOCKED_HOSTNAME_SUFFIXES = (
    ".internal",
    ".local",
    ".cluster.local",
    ".consul",
    ".lan",
)


def validate_fetch_url(url: str) -> tuple[str, str]:
    """Validate `url` is safe to fetch. Returns (normalized_url, hostname).
    Raises UrlSafetyError on any rule break."""
    if not url:
        raise UrlSafetyError("URL is empty")

    parsed = urlparse(url)

    # Scheme allowlist — https only. Blocks http (cleartext + downgrade
    # risk), file:// (local file read), gopher:// (smuggling), data://
    # (no fetch needed), and any other esoteric scheme.
    if parsed.scheme != "https":
        raise UrlSafetyError(
            f"Only https URLs are allowed (got scheme={parsed.scheme!r})"
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise UrlSafetyError("URL has no hostname")

    # Port allowlist (Phase 5.5 / chi). The scheme check above only
    # rejects non-https; an attacker can still target arbitrary TCP
    # ports on a public host via https://public-relay.example:25/ etc.
    # That enables (a) using the agent as a network-position scanner
    # of memcached/redis/elasticsearch via timing oracles, (b) reaching
    # internal services via a public hostname that resolves to a
    # public IP fronting an internal proxy. Allowlist: standard HTTPS
    # only. Set port to None or 443; reject everything else.
    if parsed.port is not None and parsed.port != 443:
        raise UrlSafetyError(
            f"Only standard HTTPS port (443) is allowed (got port={parsed.port})"
        )

    if host in _BLOCKED_HOSTNAMES:
        raise UrlSafetyError(f"Hostname {host!r} is blocked (cloud-metadata target)")

    if any(host.endswith(s) for s in _BLOCKED_HOSTNAME_SUFFIXES):
        raise UrlSafetyError(
            f"Hostname {host!r} matches a blocked internal-network suffix"
        )

    # Resolve DNS and reject any IP that classifies as non-public.
    # This catches DNS-rebinding (private IPs hidden behind public-
    # looking hostnames) and IPv6 link-local / unique-local.
    try:
        addrinfo = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UrlSafetyError(f"DNS resolution failed for {host!r}: {e}") from e

    seen: set[str] = set()
    for entry in addrinfo:
        ip_str = entry[4][0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UrlSafetyError(
                f"Hostname {host!r} resolves to non-public IP {ip_str} — refusing fetch"
            )

    return url, host
