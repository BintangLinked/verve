"""Tests for the Odoo URL policy (SSRF protection), design 8.9.

Purpose: ODOO_URL is configured by an admin, but must still be prevented from pointing to dangerous internal
addresses (cloud metadata, link-local) and, by default, private/loopback addresses. The check
is applied to ALL addresses resulting from DNS resolution, not only to the URL text, so that host names
pointing to internal addresses (simple DNS rebinding) and disguised address forms are also caught.
"""
import socket

import pytest

from app.odoo.url_policy import UrlNotAllowed, validate_odoo_url


def resolving_to(*ips):
    """Fake resolver: all host names are mapped to a given list of IPs (no network)."""

    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return resolve


PUBLIC = resolving_to("93.184.216.34")


def test_public_https_url_is_allowed_and_normalised():
    assert validate_odoo_url("https://odoo.example.com/", allow_private=False, resolve=PUBLIC) == "https://odoo.example.com"
    assert validate_odoo_url(" http://odoo.example.com:8069 ", allow_private=False, resolve=PUBLIC) == "http://odoo.example.com:8069"


@pytest.mark.parametrize("url", [
    "ftp://odoo.example.com", "file:///etc/passwd", "gopher://odoo.example.com", "javascript:alert(1)",
    "odoo.example.com", "https://", "",
])
def test_non_http_or_hostless_urls_rejected(url):
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url(url, allow_private=True, resolve=PUBLIC)


def test_url_with_embedded_credentials_rejected():
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url("https://admin:secret@odoo.example.com", allow_private=False, resolve=PUBLIC)


@pytest.mark.parametrize("ip", [
    "169.254.169.254",          # cloud metadata (AWS/GCP/Azure)
    "169.254.10.10",            # other link-local
    "fe80::1",                  # IPv6 link-local
    "::ffff:169.254.169.254",   # IPv4 metadata wrapped in IPv6
    "0.0.0.0",                  # unspecified
    "224.0.0.1",                # multicast
    "100.100.100.200",          # Alibaba metadata
    "fd00:ec2::254",            # AWS IPv6 metadata
    "2002:a9fe:a9fe::",         # 6to4 wrapping 169.254.169.254
    "64:ff9b::a9fe:a9fe",       # NAT64 wrapping 169.254.169.254
])
def test_dangerous_addresses_blocked_even_when_private_hosts_allowed(ip):
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url("http://odoo.example.com", allow_private=True, resolve=resolving_to(ip))


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.5", "172.16.0.1", "::1", "100.64.0.1"])
def test_private_and_loopback_blocked_by_default_but_allowed_with_flag(ip):
    resolve = resolving_to(ip)
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url("http://odoo.internal:8069", allow_private=False, resolve=resolve)
    assert validate_odoo_url("http://odoo.internal:8069", allow_private=True, resolve=resolve) == "http://odoo.internal:8069"


def test_hostname_resolving_to_internal_address_rejected():
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url("https://looks-public.example.com", allow_private=False, resolve=resolving_to("10.1.2.3"))


def test_one_private_record_among_public_ones_rejects_the_url():
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url("https://odoo.example.com", allow_private=False, resolve=resolving_to("93.184.216.34", "10.0.0.9"))


@pytest.mark.parametrize("url", ["http://0x7f000001/", "http://2130706433/", "http://127.1/", "http://[::1]:8069"])
def test_disguised_loopback_literals_rejected_with_the_real_resolver(url):
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url(url, allow_private=False)  # real resolver: no network for literal addresses


def test_unresolvable_host_raises_gaierror():
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    with pytest.raises(socket.gaierror):
        validate_odoo_url("https://nope.invalid", allow_private=False, resolve=fail)


@pytest.mark.parametrize("ip", ["2002:7f00:0001::", "64:ff9b::7f00:1"])  # 6to4 / NAT64 wrapping 127.0.0.1
def test_embedded_loopback_is_treated_as_loopback(ip):
    resolve = resolving_to(ip)
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url("http://odoo.internal", allow_private=False, resolve=resolve)
    assert validate_odoo_url("http://odoo.internal", allow_private=True, resolve=resolve)


@pytest.mark.parametrize("url", [
    "https://odoo.example.com/?x=1", "https://odoo.example.com/#frag", "http://127.0.0.1#@evil.com",
    "https://odoo.example.com/;params",
])
def test_query_fragment_and_params_rejected(url):
    with pytest.raises(UrlNotAllowed):
        validate_odoo_url(url, allow_private=True, resolve=PUBLIC)


def test_resolution_exposes_the_validated_address_for_pinning():
    from app.odoo.url_policy import resolve_and_validate

    target = resolve_and_validate("https://odoo.example.com:8443/odoo", allow_private=False, resolve=PUBLIC)
    assert (target.scheme, target.host, target.port, target.ip) == ("https", "odoo.example.com", 8443, "93.184.216.34")
    assert target.url == "https://odoo.example.com:8443/odoo"
