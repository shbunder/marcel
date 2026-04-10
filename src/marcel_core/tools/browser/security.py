"""SSRF protection for browser navigation.

Blocks navigation to private networks, localhost, and dangerous URL schemes
unless explicitly allowed via config.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_BLOCKED_SCHEMES = frozenset({'file', 'ftp', 'javascript', 'data', 'blob', 'vbscript'})

_PRIVATE_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
]


def _is_private_ip(ip_str: str) -> bool:
    """Check whether an IP address falls in a private/reserved range."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # If we can't parse it, block it
    return any(addr in net for net in _PRIVATE_NETWORKS)


def _resolve_hostname(hostname: str) -> list[str]:
    """Resolve a hostname to its IP addresses."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return [str(r[4][0]) for r in results]
    except socket.gaierror:
        return []


def is_url_allowed(url: str, allowlist: list[str] | None = None) -> tuple[bool, str]:
    """Check whether a URL is safe to navigate to.

    Args:
        url: The URL to check.
        allowlist: Optional list of allowed hostname patterns. A pattern
            starting with ``*.`` matches any subdomain. An exact string
            matches that hostname only.

    Returns:
        A (allowed, reason) tuple. When ``allowed`` is False, ``reason``
        describes why the URL was blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, 'Could not parse URL'

    scheme = (parsed.scheme or '').lower()

    if not scheme:
        return False, 'No URL scheme provided'

    if scheme in _BLOCKED_SCHEMES:
        return False, f'Blocked scheme: {scheme}'

    if scheme not in ('http', 'https'):
        return False, f'Unsupported scheme: {scheme}'

    hostname = parsed.hostname
    if not hostname:
        return False, 'No hostname in URL'

    # Check allowlist first — if a hostname is explicitly allowed, skip IP checks
    if allowlist and _hostname_matches(hostname, allowlist):
        return True, ''

    # Resolve and check IPs
    ips = _resolve_hostname(hostname)
    if not ips:
        return False, f'Could not resolve hostname: {hostname}'

    for ip in ips:
        if _is_private_ip(ip):
            return False, f'Hostname {hostname} resolves to private IP {ip}'

    return True, ''


def _hostname_matches(hostname: str, patterns: list[str]) -> bool:
    """Check whether a hostname matches any pattern in the list."""
    hostname = hostname.lower()
    for pattern in patterns:
        pattern = pattern.lower().strip()
        if pattern.startswith('*.'):
            suffix = pattern[1:]  # e.g. ".example.com"
            if hostname.endswith(suffix) or hostname == pattern[2:]:
                return True
        elif hostname == pattern:
            return True
    return False
