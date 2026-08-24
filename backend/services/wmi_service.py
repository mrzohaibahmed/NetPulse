"""
WMI/WinRM device information collector for Windows PCs.

Responsibilities
----------------
- Query Windows PCs via WinRM to collect authoritative hardware/OS metadata.
- Return a normalized ``WmiDeviceInfo`` dataclass for the identification layer.
- Never log credentials or expose them in exceptions.

This module is called by ``WindowsIdentifier`` only when a device has been
classified as a Windows PC/Laptop/Server and has WinRM credentials configured.
It does NOT replace Nmap — it enriches identification with authoritative data
that Nmap fingerprinting cannot provide (serial number, exact model, etc.).

Safety
------
- Strict per-host timeout (default 15 s, configurable via ``WMI_TIMEOUT``).
- Single connection attempt — no retries.
- All exceptions caught and returned as error strings.
- Credentials decrypted only at connection time, never logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.monitor_logger import get_monitor_logger
from utils.secret_crypto import decrypt_secret

logger = get_monitor_logger("wmi")

# Import winrm lazily so the application starts even if pywinrm is not installed.
_winrm_lib: Any = None
_WINRM_AVAILABLE: bool = False

try:
    import winrm as _winrm_lib  # package: pywinrm

    _WINRM_AVAILABLE = True
except ImportError:
    _winrm_lib = None
    _WINRM_AVAILABLE = False


# ---------------------------------------------------------------------------
# WMI queries — each targets a single WMI class for efficiency.
# ---------------------------------------------------------------------------

_WQL_COMPUTER_SYSTEM = (
    "SELECT Manufacturer, Model, Name, TotalPhysicalMemory "
    "FROM Win32_ComputerSystem"
)
_WQL_OPERATING_SYSTEM = (
    "SELECT Caption, Version, BuildNumber FROM Win32_OperatingSystem"
)
_WQL_PRODUCT = (
    "SELECT SerialNumber, UUID FROM Win32_ComputerSystemProduct"
)
_WQL_PROCESSOR = "SELECT Name FROM Win32_Processor"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class WmiDeviceInfo:
    """Normalized WMI identification result for a Windows PC."""

    hostname: str = ""
    manufacturer: str = ""
    model: str = ""
    serial_number: str = ""
    operating_system: str = ""
    os_version: str = ""
    os_build: str = ""
    system_uuid: str = ""
    cpu: str = ""
    total_ram_gb: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_str(value: Any, default: str = "") -> str:
    """Cast a value to a stripped string; return default on None / falsy."""
    if value is None:
        return default
    return str(value).strip() or default


def _parse_wmi_output(output: str) -> list[dict[str, str]]:
    """
    Parse WinRM PowerShell table output into a list of dicts.

    WinRM ``run_ps`` returns formatted table text.  We use a simpler approach:
    run PowerShell ``Get-WmiObject`` with ``Format-List`` which produces
    ``Key : Value`` lines separated by blank lines per object.
    """
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in (output or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                rows.append(current)
                current = {}
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            current[key.strip()] = value.strip()

    if current:
        rows.append(current)

    return rows


def _run_wmi_query(session: Any, wql_class: str, properties: str) -> list[dict[str, str]]:
    """
    Execute a WMI query via PowerShell and return parsed rows.

    Uses ``Get-WmiObject`` with ``Format-List`` for reliable parsing.
    """
    ps_script = (
        f"Get-WmiObject -Query \"{wql_class}\" "
        f"| Format-List {properties}"
    )
    result = session.run_ps(ps_script)

    if result.status_code != 0:
        stderr = _safe_str(result.std_err)
        # WinRM returns UTF-16 encoded error strings sometimes
        if isinstance(result.std_err, bytes):
            try:
                stderr = result.std_err.decode("utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001
                stderr = str(result.std_err)
        logger.debug("[WMI] Query returned non-zero status | query=%s | stderr=%s",
                      wql_class[:60], stderr[:200])
        return []

    output = ""
    if isinstance(result.std_out, bytes):
        output = result.std_out.decode("utf-8", errors="replace")
    else:
        output = str(result.std_out or "")

    return _parse_wmi_output(output)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_winrm_available() -> bool:
    """True when the pywinrm library is importable."""
    return _WINRM_AVAILABLE


def has_winrm_credentials(device: dict | None) -> bool:
    """True when the device has WinRM username + password configured."""
    if not device:
        return False
    creds = device.get("credentials") or {}
    return bool(creds.get("winrmUsername") and creds.get("winrmPassword"))


def query_windows_device(
    ip_address: str,
    username: str,
    password: str,
    *,
    port: int = 5985,
    use_ssl: bool = False,
    timeout: int = 15,
) -> WmiDeviceInfo:
    """
    Connect to a Windows PC via WinRM and collect hardware/OS metadata.

    Parameters
    ----------
    ip_address : str
        IPv4 address of the target Windows PC.
    username : str
        WinRM username (plaintext, already decrypted by caller).
    password : str
        WinRM password (plaintext, already decrypted by caller).
    port : int
        WinRM port (default 5985 for HTTP, 5986 for HTTPS).
    use_ssl : bool
        Use HTTPS transport (port 5986).
    timeout : int
        Connection and operation timeout in seconds.

    Returns
    -------
    WmiDeviceInfo
        Normalized device information.

    Raises
    ------
    RuntimeError
        If pywinrm is not installed.
    ConnectionError
        If WinRM connection fails (auth, network, timeout).
    """
    if not _WINRM_AVAILABLE or _winrm_lib is None:
        raise RuntimeError(
            "pywinrm is not installed. Run: pip install pywinrm"
        )

    scheme = "https" if use_ssl else "http"
    endpoint = f"{scheme}://{ip_address}:{port}/wsman"

    logger.info("[WMI] Connecting | host=%s port=%d ssl=%s", ip_address, port, use_ssl)

    try:
        session = _winrm_lib.Session(
            endpoint,
            auth=(username, password),
            transport="ntlm",
            server_cert_validation="ignore",
            operation_timeout_sec=timeout,
            read_timeout_sec=timeout + 5,
        )
    except Exception as exc:
        raise ConnectionError(
            f"[WMI] Failed to create WinRM session for {ip_address}"
        ) from exc

    info = WmiDeviceInfo()

    # ── Win32_ComputerSystem ─────────────────────────────────────────
    try:
        rows = _run_wmi_query(
            session,
            _WQL_COMPUTER_SYSTEM,
            "Manufacturer, Model, Name, TotalPhysicalMemory",
        )
        if rows:
            row = rows[0]
            info.hostname = _safe_str(row.get("Name"))
            info.manufacturer = _safe_str(row.get("Manufacturer"))
            info.model = _safe_str(row.get("Model"))
            ram_bytes = _safe_str(row.get("TotalPhysicalMemory"))
            if ram_bytes:
                try:
                    info.total_ram_gb = round(int(ram_bytes) / (1024 ** 3), 1)
                except (ValueError, TypeError):
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("[WMI] Win32_ComputerSystem failed | host=%s | %s",
                      ip_address, exc)

    # ── Win32_OperatingSystem ────────────────────────────────────────
    try:
        rows = _run_wmi_query(
            session,
            _WQL_OPERATING_SYSTEM,
            "Caption, Version, BuildNumber",
        )
        if rows:
            row = rows[0]
            info.operating_system = _safe_str(row.get("Caption"))
            info.os_version = _safe_str(row.get("Version"))
            info.os_build = _safe_str(row.get("BuildNumber"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[WMI] Win32_OperatingSystem failed | host=%s | %s",
                      ip_address, exc)

    # ── Win32_ComputerSystemProduct ──────────────────────────────────
    try:
        rows = _run_wmi_query(
            session,
            _WQL_PRODUCT,
            "SerialNumber, UUID",
        )
        if rows:
            row = rows[0]
            info.serial_number = _safe_str(row.get("SerialNumber"))
            info.system_uuid = _safe_str(row.get("UUID"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[WMI] Win32_ComputerSystemProduct failed | host=%s | %s",
                      ip_address, exc)

    # ── Win32_Processor (optional, first row only) ───────────────────
    try:
        rows = _run_wmi_query(session, _WQL_PROCESSOR, "Name")
        if rows:
            info.cpu = _safe_str(rows[0].get("Name"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[WMI] Win32_Processor failed | host=%s | %s",
                      ip_address, exc)

    logger.info(
        "[WMI] Collection complete | host=%s hostname=%s manufacturer=%s model=%s",
        ip_address,
        info.hostname or "(empty)",
        info.manufacturer or "(empty)",
        info.model or "(empty)",
    )

    return info
