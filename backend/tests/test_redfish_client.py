"""
Unit and Security Tests for Phase 3A Redfish Client and SSRF Protection.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from utils.redfish_client import (
    PinnedIPSSLAdapter,
    RedfishAuthenticationError,
    RedfishClient,
    RedfishConnectionError,
    RedfishError,
    RedfishRedirectError,
    RedfishResponseTooLargeError,
    RedfishSSRFError,
    RedfishTLSVerificationError,
    RedfishTimeoutError,
)
from utils.ssrf_protection import (
    is_prohibited_ip,
    resolve_and_validate_destination,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "redfish"


# ---------------------------------------------------------------------------
# 1. SSRF & Address Validation Tests
# ---------------------------------------------------------------------------

def test_ssrf_prohibited_ips():
    """Verify loopback, link-local, multicast, unspecified, and mapped IPs are rejected."""
    prohibited = [
        "127.0.0.1",
        "127.0.0.254",
        "0.0.0.0",
        "::1",
        "::",
        "169.254.1.1",
        "169.254.254.254",
        "fe80::1",
        "224.0.0.1",
        "239.255.255.250",
        "ff02::1",
        "::ffff:127.0.0.1",
    ]
    for ip in prohibited:
        assert is_prohibited_ip(ip) is True, f"Expected {ip} to be prohibited"


def test_ssrf_permitted_ips():
    """Verify private RFC 1918 subnets and valid public IPs are allowed."""
    permitted = [
        "10.0.0.1",
        "10.254.0.100",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "192.168.100.50",
        "8.8.8.8",
        "1.1.1.1",
    ]
    for ip in permitted:
        assert is_prohibited_ip(ip) is False, f"Expected {ip} to be permitted"


def test_resolve_destination_ipv4_literal():
    """IPv4 literals return directly when permitted, or raise RedfishSSRFError when prohibited."""
    pinned, all_ips = resolve_and_validate_destination("192.168.1.50")
    assert pinned == "192.168.1.50"
    assert all_ips == ["192.168.1.50"]

    with pytest.raises(RedfishSSRFError) as exc_info:
        resolve_and_validate_destination("127.0.0.1")
    assert "Prohibited destination" in str(exc_info.value)


def test_resolve_destination_hostname_filtering():
    """DNS resolution resolves once, discards prohibited IPs, and returns candidate set."""
    mock_getaddrinfo = MagicMock(return_value=[
        (2, 1, 6, "", ("127.0.0.1", 443)),
        (2, 1, 6, "", ("10.10.10.5", 443)),
        (2, 1, 6, "", ("10.10.10.6", 443)),
    ])

    pinned, all_ips = resolve_and_validate_destination(
        "ilo.example.local",
        dns_resolver=mock_getaddrinfo,
    )
    assert pinned == "10.10.10.5"
    assert all_ips == ["10.10.10.5", "10.10.10.6"]
    mock_getaddrinfo.assert_called_once_with(
        "ilo.example.local", 443, socket.AF_INET, socket.SOCK_STREAM
    )


def test_resolve_destination_all_prohibited():
    """If all DNS results resolve to prohibited IPs, raise RedfishSSRFError."""
    mock_getaddrinfo = MagicMock(return_value=[
        (2, 1, 6, "", ("127.0.0.1", 443)),
        (2, 1, 6, "", ("169.254.10.10", 443)),
    ])
    with pytest.raises(RedfishSSRFError) as exc_info:
        resolve_and_validate_destination(
            "bad-ilo.local",
            dns_resolver=mock_getaddrinfo,
        )
    assert "prohibited SSRF destinations" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. PinnedIPSSLAdapter & TLS/SNI Tests
# ---------------------------------------------------------------------------

def test_pinned_ip_adapter_sni_preservation():
    """Verify HTTPAdapter cert_verify preserves target_hostname on connection object."""
    adapter = PinnedIPSSLAdapter(
        target_hostname="ilo.example.local",
        pinned_ip="10.10.10.5",
    )
    mock_conn = MagicMock()
    adapter.cert_verify(mock_conn, "https://10.10.10.5/redfish/v1/", verify=True, cert=None)
    assert mock_conn.server_hostname == "ilo.example.local"
    assert mock_conn.assert_hostname == "ilo.example.local"


# ---------------------------------------------------------------------------
# 3. RedfishClient Initialization & Proxy Isolation Tests
# ---------------------------------------------------------------------------

@patch("utils.redfish_client.resolve_and_validate_destination")
def test_client_proxy_isolation_and_headers(mock_resolve):
    """Verify session.trust_env is False (ignoring ambient proxies)."""
    mock_resolve.return_value = ("10.10.10.5", ["10.10.10.5"])
    client = RedfishClient(
        ilo_address="ilo.example.local",
        username="admin",
        password="secretpassword",
    )
    assert client.session.trust_env is False
    assert client.pinned_ip == "10.10.10.5"
    assert client.verify_setting is True

    headers = client._build_headers()
    assert headers["Host"] == "ilo.example.local"
    assert headers["Accept"] == "application/json"
    client.close()


@patch("utils.redfish_client.logger.warning")
@patch("utils.redfish_client.resolve_and_validate_destination")
def test_client_insecure_tls_opt_in_warning(mock_resolve, mock_warn):
    """Explicit verify_tls=False produces logger warning and sets verify_setting=False."""
    mock_resolve.return_value = ("10.10.10.5", ["10.10.10.5"])
    client = RedfishClient(
        ilo_address="ilo.example.local",
        username="admin",
        password="secretpassword",
        verify_tls=False,
    )
    assert client.verify_setting is False
    mock_warn.assert_called_once()
    assert "TLS verification explicitly disabled for iLO host" in mock_warn.call_args[0][0]
    client.close()


# ---------------------------------------------------------------------------
# 4. Redirect & Timeout & Size Limit Tests
# ---------------------------------------------------------------------------

@patch("utils.redfish_client.resolve_and_validate_destination")
def test_client_redirect_rejection(mock_resolve):
    """HTTP 301/302 redirects raise RedfishRedirectError."""
    mock_resolve.return_value = ("10.10.10.5", ["10.10.10.5"])
    client = RedfishClient(
        ilo_address="ilo.example.local",
        username="admin",
        password="secretpassword",
        auth_mode="basic",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": "http://127.0.0.1/malicious"}

    with patch.object(client.session, "request", return_value=mock_resp):
        with pytest.raises(RedfishRedirectError) as exc_info:
            client.get("/redfish/v1/Systems")
        assert "HTTP redirect (302) to location" in str(exc_info.value)
    client.close()


@patch("utils.redfish_client.resolve_and_validate_destination")
def test_client_response_size_limit(mock_resolve):
    """Responses exceeding max_response_bytes raise RedfishResponseTooLargeError."""
    mock_resolve.return_value = ("10.10.10.5", ["10.10.10.5"])
    client = RedfishClient(
        ilo_address="ilo.example.local",
        username="admin",
        password="secretpassword",
        auth_mode="basic",
        max_response_bytes=100,  # Tiny limit for testing
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"A" * 60, b"B" * 60]  # Total 120 bytes

    with patch.object(client.session, "request", return_value=mock_resp):
        with pytest.raises(RedfishResponseTooLargeError) as exc_info:
            client.get("/redfish/v1/Systems")
        assert "exceeded size limit" in str(exc_info.value)
    client.close()


# ---------------------------------------------------------------------------
# 5. Authentication & Session Teardown Tests
# ---------------------------------------------------------------------------

@patch("utils.redfish_client.resolve_and_validate_destination")
def test_session_login_and_mandatory_logout(mock_resolve):
    """Session login captures X-Auth-Token and context manager executes DELETE teardown."""
    mock_resolve.return_value = ("10.10.10.5", ["10.10.10.5"])

    login_resp = MagicMock()
    login_resp.status_code = 201
    login_resp.headers = {
        "X-Auth-Token": "test-session-token-123",
        "Location": "/redfish/v1/SessionService/Sessions/100",
    }

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.iter_content.return_value = [b'{"RedfishVersion": "1.8.0"}']

    logout_resp = MagicMock()
    logout_resp.status_code = 200

    def mock_request(method, url, **kwargs):
        if method == "POST" and "Sessions" in url:
            return login_resp
        if method == "GET":
            return get_resp
        if method == "DELETE":
            return logout_resp
        return MagicMock()

    client = RedfishClient(
        ilo_address="ilo.example.local",
        username="admin",
        password="secretpassword",
    )

    with patch.object(client.session, "request", side_effect=mock_request) as request_spy:
        with client:
            res = client.get("/redfish/v1/")
            assert res["RedfishVersion"] == "1.8.0"
            assert client.session_token == "test-session-token-123"

        # Teardown should have executed DELETE
        delete_calls = [
            call for call in request_spy.call_args_list if call.kwargs.get("method") == "DELETE"
        ]
        assert len(delete_calls) == 1
        assert "/redfish/v1/SessionService/Sessions/100" in delete_calls[0].kwargs["url"]
        assert client.session_token is None


@patch("utils.redfish_client.resolve_and_validate_destination")
def test_session_cleanup_on_request_error(mock_resolve):
    """Session DELETE teardown runs even if GET request raises an exception."""
    mock_resolve.return_value = ("10.10.10.5", ["10.10.10.5"])

    login_resp = MagicMock()
    login_resp.status_code = 201
    login_resp.headers = {
        "X-Auth-Token": "test-session-token-456",
        "Location": "/redfish/v1/SessionService/Sessions/200",
    }

    logout_resp = MagicMock()
    logout_resp.status_code = 204

    def mock_request(method, url, **kwargs):
        if method == "POST":
            return login_resp
        if method == "GET":
            raise requests.exceptions.ReadTimeout("Read timeout")
        if method == "DELETE":
            return logout_resp
        return MagicMock()

    client = RedfishClient(
        ilo_address="ilo.example.local",
        username="admin",
        password="secretpassword",
    )

    with patch.object(client.session, "request", side_effect=mock_request) as request_spy:
        with pytest.raises(RedfishTimeoutError):
            with client:
                client.get("/redfish/v1/Systems")

        # Verify DELETE was called during context exit despite GET exception
        delete_calls = [
            call for call in request_spy.call_args_list if call.kwargs.get("method") == "DELETE"
        ]
        assert len(delete_calls) == 1


@patch("utils.redfish_client.resolve_and_validate_destination")
def test_auth_failure_no_retries(mock_resolve):
    """HTTP 401/403 raises RedfishAuthenticationError with zero retries."""
    mock_resolve.return_value = ("10.10.10.5", ["10.10.10.5"])

    auth_401_resp = MagicMock()
    auth_401_resp.status_code = 401

    client = RedfishClient(
        ilo_address="ilo.example.local",
        username="admin",
        password="wrongpassword",
    )

    with patch.object(client.session, "request", return_value=auth_401_resp) as request_spy:
        with pytest.raises(RedfishAuthenticationError) as exc_info:
            client.login()

        assert "HTTP 401" in str(exc_info.value)
        # Verify single request, zero retries
        assert request_spy.call_count == 1
    client.close()


# ---------------------------------------------------------------------------
# 6. Fixture Consumption Tests (iLO 4 / 5 / 6)
# ---------------------------------------------------------------------------

def test_ilo4_fixture_structure():
    """Verify iLO 4 mock JSON fixture structure."""
    fixture_path = FIXTURES_DIR / "ilo4_dl380.json"
    assert fixture_path.exists()
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert data["RedfishVersion"] == "1.0.0"
    assert data["Oem"]["Hp"]["Type"] == "iLO 4"


def test_ilo5_fixture_structure():
    """Verify iLO 5 mock JSON fixture structure."""
    fixture_path = FIXTURES_DIR / "ilo5_dl380.json"
    assert fixture_path.exists()
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert data["RedfishVersion"] == "1.8.0"
    assert data["Oem"]["Hpe"]["Manager"][0]["ManagerType"] == "iLO 5"


def test_ilo6_fixture_structure():
    """Verify iLO 6 mock JSON fixture structure."""
    fixture_path = FIXTURES_DIR / "ilo6_dl380.json"
    assert fixture_path.exists()
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert data["RedfishVersion"] == "1.14.0"
    assert data["Oem"]["Hpe"]["Manager"][0]["ManagerType"] == "iLO 6"
