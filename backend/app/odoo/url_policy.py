"""Odoo URL policy: SSRF protection for ODOO_URL.

Purpose: the backend makes HTTP requests to ODOO_URL carrying credentials. Even though the URL is entered by the admin,
it must not point to a dangerous internal address. Rules:
- http/https only, with no credentials, query, fragment, or parameters in the URL, and it must have a host;
- ALL addresses resulting from DNS resolution are checked (not only the URL text), including disguised forms
  (0x7f000001, 2130706433, ::ffff:169.254.169.254) and IPv4 wrapped in IPv6 (6to4, NAT64, Teredo);
- ALWAYS blocked: link-local (including cloud metadata 169.254.169.254), multicast, unspecified,
  reserved, and other cloud metadata addresses;
- private/loopback/non-global are blocked UNLESS `allow_private=True` (ODOO_ALLOW_PRIVATE_HOSTS), which is
  needed when Odoo runs on localhost or an internal network.

The TOCTOU gap (DNS rebinding) is closed in odoo/live.py: the client does NOT re-resolve the host name, but
connects to the IP address returned here (`Target.ip`) with the original Host header and SNI, and
the check is repeated on every request. Redirects are not followed and proxies from the environment are ignored.

Relations: called by odoo/live.py (every request) and check_connection.
"""
import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse


class UrlNotAllowed(Exception):
    """Odoo URL rejected by the security policy; the message is safe to display (no secrets)."""


@dataclass(frozen=True)
class Target:
    """Validated URL together with the destination IP address of the connection (to be pinned by the HTTP client)."""

    url: str  # normalised URL (without a trailing slash)
    scheme: str
    host: str
    port: int
    ip: str  # address from the first resolution; all addresses from the resolution have passed the check


# Cloud metadata addresses not covered by the link-local rules.
_METADATA = {ipaddress.ip_address("100.100.100.200"), ipaddress.ip_address("fd00:ec2::254")}
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _embedded_ipv4(ip) -> list:
    """IPv4 wrapped inside an IPv6 address (mapped, 6to4, Teredo, NAT64). The wrapper address
    itself looks 'global', so without this 169.254.169.254 could pass through 2002:a9fe:a9fe::."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return []
    found = []
    if ip.ipv4_mapped:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour:
        found.append(ip.sixtofour)
    if ip.teredo:
        found.extend(ip.teredo)  # (server address, client address)
    if ip in _NAT64:
        found.append(ipaddress.IPv4Address(ip.packed[-4:]))
    return found


def _blocked_always(ip) -> bool:
    # ::1 is recorded as "reserved" (block ::/8) although it is loopback; loopback is judged by the private rule.
    return (
        ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or (ip.is_reserved and not ip.is_loopback)
        or ip in _METADATA
    )


def resolve_and_validate(url: str, *, allow_private: bool, resolve: Callable = socket.getaddrinfo) -> Target:
    """Validate the URL and return a Target, or raise UrlNotAllowed. socket.gaierror is allowed to propagate
    (host cannot be resolved = unreachable, not a policy violation)."""
    try:
        parsed = urlparse(url.strip())
        port = parsed.port  # ValueError if the port is invalid
    except ValueError as exc:
        raise UrlNotAllowed("the URL is malformed") from exc
    if parsed.scheme not in ("http", "https"):
        raise UrlNotAllowed("only http and https URLs are allowed")
    if parsed.username or parsed.password:
        raise UrlNotAllowed("credentials inside the URL are not allowed")
    if parsed.query or parsed.fragment or parsed.params:
        raise UrlNotAllowed("query strings, fragments and parameters are not allowed in the URL")
    if not parsed.hostname:
        raise UrlNotAllowed("the URL has no host")
    port = port or (443 if parsed.scheme == "https" else 80)

    first_ip = None
    for info in resolve(parsed.hostname, port, type=socket.SOCK_STREAM):
        address = info[4][0].split("%")[0]  # strip the IPv6 zone id (fe80::1%eth0)
        ip = ipaddress.ip_address(address)
        embedded = _embedded_ipv4(ip)
        # The NAT64 prefix (64:ff9b::/96) falls in the ::/8 block that is flagged "reserved" on Python 3.10, so for
        # NAT64 only the IPv4 it wraps is evaluated; other addresses are evaluated themselves AND their wrapped address.
        for candidate in (embedded if ip in _NAT64 else [ip, *embedded]):
            if _blocked_always(candidate):
                raise UrlNotAllowed(f"the address {candidate} is never allowed")
            if not candidate.is_global and not allow_private:
                raise UrlNotAllowed(f"the address {candidate} is private; set ODOO_ALLOW_PRIVATE_HOSTS=true to allow it")
        first_ip = first_ip or address
    if first_ip is None:
        raise UrlNotAllowed("the host did not resolve to any address")
    return Target(url.strip().rstrip("/"), parsed.scheme, parsed.hostname, port, first_ip)


def validate_odoo_url(url: str, *, allow_private: bool, resolve: Callable = socket.getaddrinfo) -> str:
    """Like resolve_and_validate but returns only the normalised URL."""
    return resolve_and_validate(url, allow_private=allow_private, resolve=resolve).url
