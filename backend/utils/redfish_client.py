"""
Secure Redfish HTTPS Client for NetPulse (Phase 3A).

Provides HTTPS connection management to HPE iLO 4/5/6 endpoints with:
  - SSRF protection & single-point DNS resolution with destination IP pinning
  - TLS certificate & hostname verification (SNI preserved)
  - Custom CA bundle support (REDFISH_CA_BUNDLE env or parameter)
  - Explicit per-device insecure TLS opt-in with security warning logging
  - Strict ambient proxy isolation (session.trust_env = False)
  - Explicit redirect rejection (allow_redirects = False)
  - SessionService authentication (X-Auth-Token) with mandatory DELETE teardown
  - Per-request connect/read timeouts and streaming payload size caps
  - Zero credential/token exposure in logs or exception tracebacks
"""

from __future__ import annotations

import json
import os
import re
import socket
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import ssl_

from utils.monitor_logger import get_monitor_logger
from utils.ssrf_protection import RedfishSSRFError, resolve_and_validate_destination

logger = get_monitor_logger("redfish_client")

# Sanitization regexes to strip tokens/passwords from error strings
_TOKEN_RE = re.compile(r"(X-Auth-Token['\":\s=]+)[^\s'\",&]+", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"(['\"]?Password['\"]?\s*:\s*['\"])[^'\"]+(['\"])", re.IGNORECASE)


def _sanitize_log_text(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _TOKEN_RE.sub(r"\1[REDACTED]", str(text))
    cleaned = _PASSWORD_RE.sub(r"\1[REDACTED]\2", cleaned)
    return cleaned


class RedfishError(Exception):
    """Base exception for all Redfish operations (credentials sanitized)."""

    def __init__(self, message: str):
        super().__init__(_sanitize_log_text(message))


class RedfishConnectionError(RedfishError):
    """Raised on socket connection, DNS, or network failure."""


class RedfishAuthenticationError(RedfishError):
    """Raised on HTTP 401/403 or session creation failure."""


class RedfishTLSVerificationError(RedfishError):
    """Raised on TLS certificate validation failure."""


class RedfishRedirectError(RedfishError):
    """Raised when a Redfish endpoint attempts an HTTP redirect."""


class RedfishResponseTooLargeError(RedfishError):
    """Raised when a Redfish response body exceeds max_response_bytes."""


class RedfishTimeoutError(RedfishError):
    """Raised when a connection or read timeout expires."""


class PinnedIPSSLAdapter(HTTPAdapter):
    """
    HTTPAdapter that forces TCP connection to a validated IP address
    while preserving the original target hostname for TLS SNI and certificate verification.
    """

    def __init__(self, target_hostname: str, pinned_ip: str, **kwargs: Any):
        self.target_hostname = target_hostname
        self.pinned_ip = pinned_ip
        super().__init__(**kwargs)

    def cert_verify(self, conn: Any, url: str, verify: Any, cert: Any) -> None:
        super().cert_verify(conn, url, verify, cert)
        if self.target_hostname and self.target_hostname != self.pinned_ip:
            conn.server_hostname = self.target_hostname
            conn.assert_hostname = self.target_hostname
            if hasattr(conn, "conn_kw") and isinstance(conn.conn_kw, dict):
                conn.conn_kw["server_hostname"] = self.target_hostname


class RedfishClient:
    """
    Secure client for HPE iLO Redfish APIs.

    Supports context management for automatic session authentication and teardown.
    """

    def __init__(
        self,
        ilo_address: str,
        username: str,
        password: str,
        *,
        port: int = 443,
        auth_mode: str = "session",
        verify_tls: bool = True,
        ca_bundle: str | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        max_response_bytes: int = 5 * 1024 * 1024,
        device_id: str | None = None,
    ):
        if not ilo_address or not isinstance(ilo_address, str):
            raise RedfishError("ilo_address is required")
        if not username or not password:
            raise RedfishAuthenticationError("iLO username and password are required")

        self.ilo_address = ilo_address.strip()
        self.username = str(username).strip()
        self.password = str(password)
        self.port = int(port) if port else 443
        self.auth_mode = str(auth_mode).lower().strip()
        self.verify_tls = bool(verify_tls)
        self.ca_bundle = ca_bundle or os.getenv("REDFISH_CA_BUNDLE")
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.max_response_bytes = int(max_response_bytes)
        self.device_id = str(device_id) if device_id else None

        # Session state
        self.session_token: str | None = None
        self.session_uri: str | None = None

        # Perform single-point DNS resolution and SSRF destination validation
        try:
            self.pinned_ip, self.validated_ips = resolve_and_validate_destination(self.ilo_address)
        except RedfishSSRFError as exc:
            logger.warning(
                "[REDFISH_SSRF_REJECTED] Destination address rejected | address=%s | error=%s",
                self.ilo_address,
                exc,
            )
            raise RedfishConnectionError(f"SSRF destination check failed: {exc}") from exc

        # Construct requests Session
        self.session = requests.Session()

        # MANDATORY: Strict ambient proxy isolation
        self.session.trust_env = False

        # Configure TLS Verification
        if not self.verify_tls:
            logger.warning(
                "[SECURITY_WARNING] TLS verification explicitly disabled for iLO host | "
                "address=%s | pinnedIp=%s | deviceId=%s",
                self.ilo_address,
                self.pinned_ip,
                self.device_id,
            )
            self.verify_setting: bool | str = False
        elif self.ca_bundle:
            self.verify_setting = self.ca_bundle
        else:
            self.verify_setting = True

        # Mount PinnedIPSSLAdapter for HTTPS requests
        adapter = PinnedIPSSLAdapter(
            target_hostname=self.ilo_address,
            pinned_ip=self.pinned_ip,
        )
        self.session.mount("https://", adapter)

    def __enter__(self) -> RedfishClient:
        self.login()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.logout()
        self.close()

    def close(self) -> None:
        """Close the underlying requests HTTP session."""
        try:
            self.session.close()
        except Exception:  # noqa: S110
            pass

    def _build_url(self, endpoint: str) -> str:
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"https://{self.pinned_ip}:{self.port}{path}"

    def _build_headers(self, custom_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Host": self.ilo_address,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.session_token:
            headers["X-Auth-Token"] = self.session_token
        if custom_headers:
            headers.update(custom_headers)
        return headers

    def login(self) -> None:
        """Establish session or configure basic authentication."""
        if self.auth_mode == "basic":
            self.session.auth = (self.username, self.password)
            logger.info(
                "Configured Redfish basic authentication mode | host=%s | deviceId=%s",
                self.ilo_address,
                self.device_id,
            )
            return

        if self.auth_mode != "session":
            raise RedfishError(f"Unsupported authentication mode: {self.auth_mode}")

        session_url = self._build_url("/redfish/v1/SessionService/Sessions")
        payload = {
            "UserName": self.username,
            "Password": self.password,
        }
        headers = self._build_headers()

        try:
            resp = self._raw_request(
                method="POST",
                url=session_url,
                json=payload,
                headers=headers,
            )
        except (RedfishConnectionError, RedfishTimeoutError, RedfishTLSVerificationError):
            raise
        except Exception as exc:
            raise RedfishAuthenticationError(f"Session login request failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise RedfishAuthenticationError(
                f"Redfish authentication failed (HTTP {resp.status_code})"
            )

        if resp.status_code not in (200, 201):
            raise RedfishAuthenticationError(
                f"Session creation failed (HTTP {resp.status_code})"
            )

        # Extract X-Auth-Token from response headers
        token = resp.headers.get("X-Auth-Token") or resp.headers.get("x-auth-token")
        if not token:
            # Try reading token from body if present
            try:
                body = resp.json()
                token = body.get("Token") or body.get("token")
            except Exception:  # noqa: S110
                pass

        if not token:
            raise RedfishAuthenticationError(
                "Redfish session creation response missing X-Auth-Token header"
            )

        self.session_token = token
        self.session_uri = resp.headers.get("Location") or resp.headers.get("location")

        logger.info(
            "Redfish session established successfully | host=%s | pinnedIp=%s | sessionUri=%s",
            self.ilo_address,
            self.pinned_ip,
            self.session_uri,
        )

    def logout(self) -> None:
        """Teardown active Redfish session (mandatory cleanup)."""
        if not self.session_token or not self.session_uri:
            return

        token_to_delete = self.session_token
        uri_to_delete = self.session_uri
        self.session_token = None
        self.session_uri = None

        delete_url = uri_to_delete if uri_to_delete.startswith("http") else self._build_url(uri_to_delete)
        headers = self._build_headers({"X-Auth-Token": token_to_delete})

        try:
            resp = self._raw_request(
                method="DELETE",
                url=delete_url,
                headers=headers,
                retry_allowed=False,
            )
            logger.info(
                "Redfish session torn down | host=%s | status=%s",
                self.ilo_address,
                resp.status_code,
            )
        except Exception as exc:  # noqa: BLE001
            # Logging failure safely without propagating teardown error
            logger.warning(
                "Redfish session teardown warning (non-fatal) | host=%s | error=%s",
                self.ilo_address,
                exc,
            )

    def _raw_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        retry_allowed: bool = True,
    ) -> requests.Response:
        """Execute a raw HTTPS request with timeout, redirect rejection, and single retry."""
        attempt = 0
        max_attempts = 2 if retry_allowed else 1

        while attempt < max_attempts:
            attempt += 1
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json,
                    verify=self.verify_setting,
                    timeout=(self.connect_timeout, self.read_timeout),
                    allow_redirects=False,
                    stream=True,
                )

                # Check for HTTP redirect
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location") or ""
                    resp.close()
                    raise RedfishRedirectError(
                        f"HTTP redirect ({resp.status_code}) to location '{_sanitize_log_text(location)}' rejected"
                    )

                return resp

            except (RedfishRedirectError, RedfishAuthenticationError):
                raise
            except requests.exceptions.SSLError as exc:
                raise RedfishTLSVerificationError(
                    f"TLS certificate verification failed for iLO host '{self.ilo_address}': {exc}"
                ) from exc
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as exc:
                if attempt < max_attempts and retry_allowed:
                    logger.warning(
                        "Redfish connection attempt %d failed (%s); retrying | host=%s",
                        attempt,
                        exc,
                        self.ilo_address,
                    )
                    continue
                raise RedfishConnectionError(
                    f"Connection to iLO host '{self.ilo_address}' failed: {exc}"
                ) from exc
            except requests.exceptions.ReadTimeout as exc:
                raise RedfishTimeoutError(
                    f"Read timeout ({self.read_timeout}s) expired for iLO host '{self.ilo_address}'"
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise RedfishConnectionError(
                    f"Request failed for iLO host '{self.ilo_address}': {exc}"
                ) from exc

        raise RedfishConnectionError(f"Request failed after {max_attempts} attempts")

    def get(self, endpoint: str) -> dict[str, Any]:
        """
        Perform an authenticated GET request for a Redfish endpoint.

        Enforces response size limits and returns parsed JSON.
        """
        url = self._build_url(endpoint)
        headers = self._build_headers()

        resp = self._raw_request("GET", url, headers=headers)

        if resp.status_code in (401, 403):
            resp.close()
            raise RedfishAuthenticationError(
                f"Redfish authentication failed for endpoint '{endpoint}' (HTTP {resp.status_code})"
            )

        if resp.status_code != 200:
            resp.close()
            raise RedfishConnectionError(
                f"Redfish request for endpoint '{endpoint}' returned HTTP {resp.status_code}"
            )

        # Enforce streaming payload size limit
        content = bytearray()
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                content.extend(chunk)
                if len(content) > self.max_response_bytes:
                    resp.close()
                    raise RedfishResponseTooLargeError(
                        f"Response body for endpoint '{endpoint}' exceeded size limit ({self.max_response_bytes} bytes)"
                    )
        finally:
            resp.close()

        try:
            return json.loads(content.decode("utf-8"))
        except Exception as exc:
            raise RedfishError(
                f"Failed to parse Redfish JSON response for endpoint '{endpoint}': {exc}"
            ) from exc
