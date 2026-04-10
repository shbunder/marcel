"""Tests for browser SSRF protection module."""

from __future__ import annotations

from unittest.mock import patch

from marcel_core.browser.security import _hostname_matches, _is_private_ip, is_url_allowed


class TestIsPrivateIp:
    def test_localhost_ipv4(self):
        assert _is_private_ip('127.0.0.1') is True

    def test_localhost_ipv6(self):
        assert _is_private_ip('::1') is True

    def test_private_10(self):
        assert _is_private_ip('10.0.0.1') is True

    def test_private_172(self):
        assert _is_private_ip('172.16.0.1') is True

    def test_private_192(self):
        assert _is_private_ip('192.168.1.1') is True

    def test_link_local(self):
        assert _is_private_ip('169.254.1.1') is True

    def test_public_ip(self):
        assert _is_private_ip('8.8.8.8') is False

    def test_public_ip_2(self):
        assert _is_private_ip('93.184.216.34') is False

    def test_invalid_ip_blocked(self):
        assert _is_private_ip('not-an-ip') is True


class TestHostnameMatches:
    def test_exact_match(self):
        assert _hostname_matches('example.com', ['example.com']) is True

    def test_exact_no_match(self):
        assert _hostname_matches('other.com', ['example.com']) is False

    def test_wildcard_subdomain(self):
        assert _hostname_matches('sub.example.com', ['*.example.com']) is True

    def test_wildcard_base_domain(self):
        assert _hostname_matches('example.com', ['*.example.com']) is True

    def test_wildcard_no_match(self):
        assert _hostname_matches('other.com', ['*.example.com']) is False

    def test_case_insensitive(self):
        assert _hostname_matches('Example.COM', ['example.com']) is True

    def test_empty_patterns(self):
        assert _hostname_matches('example.com', []) is False


class TestIsUrlAllowed:
    def test_http_public(self):
        with patch('marcel_core.browser.security._resolve_hostname', return_value=['93.184.216.34']):
            allowed, reason = is_url_allowed('https://example.com')
            assert allowed is True

    def test_file_scheme_blocked(self):
        allowed, reason = is_url_allowed('file:///etc/passwd')
        assert allowed is False
        assert 'Blocked scheme' in reason

    def test_javascript_scheme_blocked(self):
        allowed, reason = is_url_allowed('javascript:alert(1)')
        assert allowed is False
        assert 'Blocked scheme' in reason

    def test_data_scheme_blocked(self):
        allowed, reason = is_url_allowed('data:text/html,<h1>hi</h1>')
        assert allowed is False
        assert 'Blocked scheme' in reason

    def test_ftp_scheme_blocked(self):
        allowed, reason = is_url_allowed('ftp://files.example.com')
        assert allowed is False
        assert 'Blocked scheme' in reason

    def test_no_scheme(self):
        allowed, reason = is_url_allowed('example.com')
        assert allowed is False

    def test_unsupported_scheme(self):
        allowed, reason = is_url_allowed('ssh://host')
        assert allowed is False
        assert 'Unsupported scheme' in reason

    def test_private_ip_blocked(self):
        with patch('marcel_core.browser.security._resolve_hostname', return_value=['192.168.1.1']):
            allowed, reason = is_url_allowed('http://internal.local')
            assert allowed is False
            assert 'private IP' in reason

    def test_localhost_blocked(self):
        with patch('marcel_core.browser.security._resolve_hostname', return_value=['127.0.0.1']):
            allowed, reason = is_url_allowed('http://localhost:8080')
            assert allowed is False
            assert 'private IP' in reason

    def test_allowlist_bypasses_ip_check(self):
        allowed, reason = is_url_allowed('http://internal.local', allowlist=['internal.local'])
        assert allowed is True

    def test_allowlist_wildcard(self):
        allowed, reason = is_url_allowed('http://app.internal.corp', allowlist=['*.internal.corp'])
        assert allowed is True

    def test_unresolvable_host_blocked(self):
        with patch('marcel_core.browser.security._resolve_hostname', return_value=[]):
            allowed, reason = is_url_allowed('http://nonexistent.invalid')
            assert allowed is False
            assert 'Could not resolve' in reason

    def test_no_hostname(self):
        allowed, reason = is_url_allowed('http://')
        assert allowed is False

    def test_empty_url(self):
        allowed, reason = is_url_allowed('')
        assert allowed is False
