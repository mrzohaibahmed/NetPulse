"""
ONVIF DeviceManagement SOAP collector for IP Cameras.

Responsibilities
----------------
- Query IP Cameras over HTTP SOAP to collect ONVIF hardware & device identification.
- Support standard ONVIF DeviceManagement endpoints (/onvif/device_service).
- Extract Manufacturer, Model, FirmwareVersion, SerialNumber, HardwareId, and DeviceName.
- Never log credentials or expose passwords in tracebacks.
- Never collect video or audio streams — identification metadata ONLY.

Safety
------
- Bounded timeout (default 5 s, configurable via ``ONVIF_TIMEOUT``).
- Probes ports 80, 8000, 8080, 8899 when port is not specified.
- Uses standard Python library (urllib / xml.etree.ElementTree).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("onvif")


class OnvifCollectorError(Exception):
    """Raised when ONVIF collection fails."""


@dataclass
class OnvifDeviceInfo:
    """Normalized ONVIF identification result."""

    manufacturer: str = ""
    model: str = ""
    firmware_version: str = ""
    serial_number: str = ""
    hardware_id: str = ""
    device_name: str = ""
    onvif_port: int = 80


# ---------------------------------------------------------------------------
# SOAP Request Templates
# ---------------------------------------------------------------------------

_SOAP_GET_DEVICE_INFORMATION = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/>
  </s:Body>
</s:Envelope>"""

_SOAP_GET_SCOPES = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <GetScopes xmlns="http://www.onvif.org/ver10/device/wsdl"/>
  </s:Body>
</s:Envelope>"""

_SOAP_GET_HOSTNAME = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <GetHostname xmlns="http://www.onvif.org/ver10/device/wsdl"/>
  </s:Body>
</s:Envelope>"""


# ---------------------------------------------------------------------------
# Internal XML & SOAP helpers
# ---------------------------------------------------------------------------


def _clean_text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _parse_xml_element(xml_str: str, tag_name: str) -> str:
    """Regex / XML helper to extract text content of a tag ignoring namespace."""
    pattern = rf"<(?:[a-zA-Z0-9_-]+:)?{tag_name}>(.*?)</(?:[a-zA-Z0-9_-]+:)?{tag_name}>"
    match = re.search(pattern, xml_str, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _parse_scopes_name(xml_str: str) -> str:
    """Extract name scope from GetScopes response: onvif://www.onvif.org/name/NAME."""
    pattern = r"onvif://www\.onvif\.org/name/([^\s<\"]+)"
    match = re.search(pattern, xml_str, re.IGNORECASE)
    if match:
        return urllib.parse.unquote(match.group(1).strip())
    return ""


def _send_soap_request(
    url: str,
    soap_body: str,
    timeout: int = 5,
) -> str:
    """Send SOAP request via HTTP POST; return XML response string."""
    data = soap_body.encode("utf-8")
    headers = {
        "Content-Type": "application/soap+xml; charset=utf-8",
        "User-Agent": "NetPulse-ONVIF/1.0",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read()
            return content.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # Some ONVIF devices return HTTP 500 with a valid SOAP Fault or response
        try:
            content = exc.read()
            if content:
                return content.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        raise OnvifCollectorError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise OnvifCollectorError(f"URL Error: {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise OnvifCollectorError(f"Request failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def query_onvif_device(
    ip_address: str,
    username: str = "",
    password: str = "",
    *,
    port: int = 80,
    timeout: int = 5,
) -> OnvifDeviceInfo:
    """
    Query an IP camera via ONVIF DeviceManagement SOAP services.

    Parameters
    ----------
    ip_address : str
        Target camera IPv4 address.
    username : str
        ONVIF username (plaintext).
    password : str
        ONVIF password (plaintext).
    port : int
        ONVIF port. If 80 and unreachable, candidate ports (8000, 8080, 8899) are probed.
    timeout : int
        Request timeout in seconds.

    Returns
    -------
    OnvifDeviceInfo
        Normalized camera metadata.

    Raises
    ------
    OnvifCollectorError
        If the endpoint is unreachable or fails to return ONVIF metadata.
    """
    candidate_ports = [port]
    if port == 80:
        candidate_ports = [80, 8000, 8080, 8899]

    info = OnvifDeviceInfo()
    last_error: Exception | None = None
    successful_url: str = ""
    xml_dev_info: str = ""

    logger.info("[ONVIF] Probing camera | host=%s ports=%s", ip_address, candidate_ports)

    for p in candidate_ports:
        url = f"http://{ip_address}:{p}/onvif/device_service"
        try:
            xml_dev_info = _send_soap_request(url, _SOAP_GET_DEVICE_INFORMATION, timeout=timeout)
            if xml_dev_info and ("GetDeviceInformationResponse" in xml_dev_info or "Manufacturer" in xml_dev_info):
                successful_url = url
                info.onvif_port = p
                break
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    if not successful_url or not xml_dev_info:
        raise OnvifCollectorError(
            f"ONVIF device service unreachable on {ip_address}: {last_error}"
        )

    # ── Parse GetDeviceInformation ───────────────────────────────────
    info.manufacturer = _parse_xml_element(xml_dev_info, "Manufacturer")
    info.model = _parse_xml_element(xml_dev_info, "Model")
    info.firmware_version = _parse_xml_element(xml_dev_info, "FirmwareVersion")
    info.serial_number = _parse_xml_element(xml_dev_info, "SerialNumber")
    info.hardware_id = _parse_xml_element(xml_dev_info, "HardwareId")

    # ── Query GetScopes for Device Name ──────────────────────────────
    try:
        xml_scopes = _send_soap_request(successful_url, _SOAP_GET_SCOPES, timeout=timeout)
        if xml_scopes:
            name = _parse_scopes_name(xml_scopes)
            if name:
                info.device_name = name
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ONVIF] GetScopes failed | host=%s | %s", ip_address, exc)

    # ── Query GetHostname as fallback for Device Name ────────────────
    if not info.device_name:
        try:
            xml_hostname = _send_soap_request(successful_url, _SOAP_GET_HOSTNAME, timeout=timeout)
            if xml_hostname:
                hostname = _parse_xml_element(xml_hostname, "Name")
                if hostname:
                    info.device_name = hostname
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ONVIF] GetHostname failed | host=%s | %s", ip_address, exc)

    logger.info(
        "[ONVIF] Query complete | host=%s mfg=%s model=%s fw=%s name=%s",
        ip_address,
        info.manufacturer or "(empty)",
        info.model or "(empty)",
        info.firmware_version or "(empty)",
        info.device_name or "(empty)",
    )

    return info
