"""
ssh_collector.py
================
SSH transport for interface discovery.

Responsibilities
----------------
- Resolve per-device / default SSH credentials
- Open an SSH session to a managed switch
- Disable paging and run vendor-specific show commands
- Return raw command outputs for the parser layer

This module does **not** parse or persist data (SOLID: single responsibility).
SNMP and future collectors should expose the same ``collect_raw_outputs`` shape.
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from typing import Any

from utils.monitor_logger import get_monitor_logger
from utils.secret_crypto import decrypt_secret

logger = get_monitor_logger("interface")

try:
    import paramiko
    _PARAMIKO_AVAILABLE = True
except ImportError:
    paramiko = None  # type: ignore[assignment]
    _PARAMIKO_AVAILABLE = False


class SSHCollectorError(Exception):
    """Raised when SSH collection fails in a recoverable, caller-visible way."""


def _ensure_paramiko() -> Any:
    """Import paramiko lazily so installs are detected without relying on stale flags."""
    global paramiko, _PARAMIKO_AVAILABLE
    if _PARAMIKO_AVAILABLE and paramiko is not None:
        return paramiko
    try:
        import paramiko as paramiko_mod
        paramiko = paramiko_mod
        _PARAMIKO_AVAILABLE = True
        return paramiko_mod
    except ImportError as exc:
        _PARAMIKO_AVAILABLE = False
        paramiko = None  # type: ignore[assignment]
        raise SSHCollectorError(
            "paramiko is not installed. Activate the backend venv and run: "
            "pip install paramiko"
        ) from exc


# Logical command keys consumed by parser.parse_interface_outputs
# Optional keys soft-fail (CDP/LLDP may be disabled on the switch).
COMMAND_SETS: dict[str, dict[str, str]] = {
    "cisco_ios": {
        "status": "show interfaces status",
        "description": "show interfaces description",
        "switchport": "show interfaces switchport",
        "vlan_brief": "show vlan brief",
        "cdp": "show cdp neighbors detail",
        "lldp": "show lldp neighbors detail",
    },
    "cisco_xe": {
        "status": "show interfaces status",
        "description": "show interfaces description",
        "switchport": "show interfaces switchport",
        "vlan_brief": "show vlan brief",
        "cdp": "show cdp neighbors detail",
        "lldp": "show lldp neighbors detail",
    },
    "cisco_nxos": {
        "status": "show interface status",
        "description": "show interface description",
        "switchport": "show interface switchport",
        "vlan_brief": "show vlan brief",
        "cdp": "show cdp neighbors detail",
        "lldp": "show lldp neighbors detail",
    },
    "juniper_junos": {
        "status": "show interfaces terse",
        "terse": "show interfaces terse",
    },
    "aruba_os": {
        "status": "show interfaces brief",
    },
    "generic": {
        "status": "show interfaces status",
        "description": "show interfaces description",
        "switchport": "show interfaces switchport",
    },
}

# Commands that must succeed for discovery; others soft-fail.
# switchport / CDP / LLDP are optional so L3-only devices and disabled
# discovery protocols never abort inventory collection.
_REQUIRED_COMMAND_KEYS = frozenset({"status", "terse"})

_OPTIONAL_COMMAND_KEYS = frozenset({
    "description", "switchport", "vlan_brief", "cdp", "lldp",
})

_VENDOR_ALIASES: dict[str, str] = {
    "cisco": "cisco_ios",
    "ios": "cisco_ios",
    "ios-xe": "cisco_xe",
    "ios_xe": "cisco_xe",
    "nxos": "cisco_nxos",
    "nx-os": "cisco_nxos",
    "juniper": "juniper_junos",
    "junos": "juniper_junos",
    "aruba": "aruba_os",
}


@dataclass(frozen=True)
class SSHCredentials:
    host: str
    username: str
    password: str
    port: int = 22
    secret: str = ""
    timeout: int = 30
    vendor: str = "cisco_ios"


def resolve_ssh_credentials(device: dict) -> SSHCredentials:
    """
    Build SSHCredentials from the device document and environment defaults.

    Device fields (optional ``credentials`` sub-document)
    -----------------------------------------------------
    sshUsername, sshPassword, sshPort, sshSecret, sshVendor

    Environment fallbacks
    ---------------------
    SSH_DEFAULT_USERNAME, SSH_DEFAULT_PASSWORD, SSH_DEFAULT_PORT,
    SSH_DEFAULT_SECRET, SSH_DEFAULT_VENDOR, SSH_TIMEOUT
    """
    creds = device.get("credentials") or {}
    host = (device.get("ipAddress") or "").strip()
    if not host:
        raise SSHCollectorError("Device has no ipAddress for SSH collection")

    username = (
        (creds.get("sshUsername") or os.getenv("SSH_DEFAULT_USERNAME") or "")
    ).strip()
    password = decrypt_secret(
        creds.get("sshPassword")
    ) if creds.get("sshPassword") else ""
    if not password:
        password = os.getenv("SSH_DEFAULT_PASSWORD") or ""
    secret_raw = creds.get("sshSecret")
    secret = decrypt_secret(secret_raw) if secret_raw else ""
    if not secret:
        secret = os.getenv("SSH_DEFAULT_SECRET") or ""

    try:
        port = int(creds.get("sshPort") or os.getenv("SSH_DEFAULT_PORT") or 22)
    except (TypeError, ValueError) as exc:
        raise SSHCollectorError(f"Invalid SSH port: {exc}") from exc

    try:
        timeout = int(os.getenv("SSH_TIMEOUT", "30"))
    except ValueError:
        timeout = 30

    vendor_raw = (
        creds.get("sshVendor")
        or device.get("sshVendor")
        or os.getenv("SSH_DEFAULT_VENDOR")
        or "cisco_ios"
    )
    vendor = _normalize_vendor(str(vendor_raw))

    if not username or password == "":
        raise SSHCollectorError(
            "SSH credentials are not configured. Set device.credentials "
            "(sshUsername / sshPassword) or SSH_DEFAULT_USERNAME / "
            "SSH_DEFAULT_PASSWORD in .env."
        )

    return SSHCredentials(
        host=host,
        username=username,
        password=password,
        port=port,
        secret=secret,
        timeout=max(timeout, 5),
        vendor=vendor,
    )


def _normalize_vendor(vendor: str) -> str:
    key = (vendor or "cisco_ios").strip().lower().replace(" ", "_")
    return _VENDOR_ALIASES.get(key, key if key in COMMAND_SETS else "cisco_ios")


def get_command_set(vendor: str) -> dict[str, str]:
    vendor_key = _normalize_vendor(vendor)
    return dict(COMMAND_SETS.get(vendor_key) or COMMAND_SETS["generic"])


def _apply_legacy_ssh_algorithms(transport: Any) -> None:
    """
    Prefer algorithms commonly required by older Cisco / Juniper SSH stacks.

    Many campus switches only advertise SHA1 DH groups and ssh-rsa host keys.
    Modern OpenSSH/Paramiko defaults reject those, which yields:
      Incompatible ssh peer (no acceptable kex algorithm)
    """
    try:
        options = transport.get_security_options()
    except Exception:  # noqa: BLE001
        return

    # Keep modern algorithms, but put legacy Cisco favourites first.
    legacy_kex = (
        "diffie-hellman-group-exchange-sha1",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
        "diffie-hellman-group-exchange-sha256",
        "diffie-hellman-group14-sha256",
        "diffie-hellman-group16-sha512",
        "ecdh-sha2-nistp256",
        "ecdh-sha2-nistp384",
        "ecdh-sha2-nistp521",
        "curve25519-sha256@libssh.org",
    )
    legacy_keys = (
        "ssh-rsa",
        "rsa-sha2-256",
        "rsa-sha2-512",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "ssh-ed25519",
    )
    legacy_ciphers = (
        "aes128-ctr",
        "aes192-ctr",
        "aes256-ctr",
        "aes128-cbc",
        "aes192-cbc",
        "aes256-cbc",
        "3des-cbc",
    )
    legacy_macs = (
        "hmac-sha2-256",
        "hmac-sha2-512",
        "hmac-sha1",
        "hmac-md5",
    )

    def _merge(preferred: tuple[str, ...], current: Any) -> tuple[str, ...]:
        existing = tuple(current) if current else ()
        seen = set()
        merged: list[str] = []
        for item in preferred + existing:
            if item not in seen:
                seen.add(item)
                merged.append(item)
        return tuple(merged)

    try:
        options.kex = _merge(legacy_kex, options.kex)
        options.key_types = _merge(legacy_keys, options.key_types)
        options.ciphers = _merge(legacy_ciphers, options.ciphers)
        options.digests = _merge(legacy_macs, options.digests)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[IFACE] Could not adjust SSH algorithms: %s", exc)


class SSHInterfaceCollector:
    """
    Collect raw show-command outputs over SSH using Paramiko.

    Usage
    -----
    collector = SSHInterfaceCollector(credentials)
    outputs = collector.collect_raw_outputs()
    # outputs == {"status": "...", "description": "...", ...}
    """

    def __init__(
        self,
        credentials: SSHCredentials,
        *,
        ssh_slot_kind: str = "collector",
        require_privileged: bool = False,
    ):
        _ensure_paramiko()
        self.credentials = credentials
        self.ssh_slot_kind = ssh_slot_kind
        self.require_privileged = require_privileged
        self._privileged_confirmed = False
        self._client: Any = None
        self._shell: Any = None
        self._slot_cm = None

    def __enter__(self) -> "SSHInterfaceCollector":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        from services.collector_concurrency import ssh_session_slot  # noqa: PLC0415

        pk = _ensure_paramiko()
        creds = self.credentials
        self._slot_cm = ssh_session_slot(
            kind=self.ssh_slot_kind,
            label=f"{creds.host}:{creds.port}",
        )
        self._slot_cm.__enter__()
        logger.info(
            "[IFACE] SSH connecting | host=%s port=%s user=%s vendor=%s",
            creds.host,
            creds.port,
            creds.username,
            creds.vendor,
        )

        client = pk.SSHClient()
        from utils.ssh_security import apply_host_key_policy  # noqa: PLC0415

        apply_host_key_policy(client)

        try:
            # Older Cisco IOS SSH stacks often only offer SHA1 KEX / ssh-rsa.
            # Prefer those algorithms first so negotiation succeeds on lab gear.
            sock = socket.create_connection(
                (creds.host, creds.port),
                timeout=creds.timeout,
            )
            transport = pk.Transport(sock)
            transport.banner_timeout = creds.timeout
            transport.auth_timeout = creds.timeout
            _apply_legacy_ssh_algorithms(transport)
            transport.start_client(timeout=creds.timeout)

            if not transport.is_authenticated():
                transport.auth_password(creds.username, creds.password)

            client._transport = transport  # noqa: SLF001 — attach for invoke_shell
        except pk.AuthenticationException as exc:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            self.close()
            raise SSHCollectorError(
                f"SSH authentication failed for {creds.host}: {exc}"
            ) from exc
        except (pk.SSHException, socket.error, OSError, TimeoutError) as exc:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            self.close()
            raise SSHCollectorError(
                f"SSH connection failed for {creds.host}: {exc}"
            ) from exc

        self._client = client
        self._shell = client.invoke_shell(width=200, height=1000)
        time.sleep(0.5)
        self._drain(timeout=1.0)
        self._prepare_session()

    def close(self) -> None:
        try:
            if self._shell is not None:
                self._shell.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._client is not None:
                self._client.close()
        except Exception:  # noqa: BLE001
            pass
        self._shell = None
        self._client = None
        if self._slot_cm is not None:
            try:
                self._slot_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._slot_cm = None

    def collect_raw_outputs(self) -> dict[str, str]:
        """
        Run the vendor command set and return ``{logical_name: output}``.

        Raises
        ------
        SSHCollectorError
            On transport or session failures.
        """
        if self._client is None or self._shell is None:
            self.connect()

        commands = get_command_set(self.credentials.vendor)
        outputs: dict[str, str] = {}

        for key, command in commands.items():
            try:
                logger.info(
                    "[IFACE] Running SSH command | host=%s | %s",
                    self.credentials.host,
                    command,
                )
                outputs[key] = self._run_command(command)
            except Exception as exc:  # noqa: BLE001
                if key in _OPTIONAL_COMMAND_KEYS or key not in _REQUIRED_COMMAND_KEYS:
                    logger.warning(
                        "[IFACE] Optional command skipped | host=%s cmd=%s | %s",
                        self.credentials.host,
                        command,
                        exc,
                    )
                    outputs[key] = ""
                    continue
                logger.exception(
                    "[IFACE] Command failed | host=%s cmd=%s | %s",
                    self.credentials.host,
                    command,
                    exc,
                )
                raise SSHCollectorError(
                    f"Failed running '{command}' on {self.credentials.host}: {exc}"
                ) from exc

        return outputs

    def run_command(self, command: str, wait: float | None = None) -> str:
        """Public helper to run a single CLI command on the open session."""
        if wait is None:
            wait = 1.5 if command.strip().lower().startswith("show ") else 0.4
        require_exec = _command_requires_exec_prompt(command)
        return self._run_command(
            command,
            wait=wait,
            require_exec_prompt=require_exec,
        )

    def mark_entering_config_mode(self) -> None:
        """Config submode invalidates cached privileged-exec confirmation."""
        self._privileged_confirmed = False

    def ensure_exec_prompt(self, *, settle_seconds: float = 0.5) -> None:
        """
        Return the session to privileged exec mode before read-only verification.

        Sends end when still in config submode, elicits the exec prompt, then
        allows a short settle window so running-config reflects recent changes.
        """
        if self._shell is None:
            raise SSHCollectorError("SSH shell is not connected")

        prompt_line = self._read_session_prompt()
        if _is_config_submode_prompt(prompt_line):
            self._run_command("end", wait=0.5, require_exec_prompt=True)

        self._drain(timeout=0.2)
        self._shell.send("\n")
        time.sleep(0.4)
        output = self._read_until_prompt(timeout=5.0, require_exec_prompt=True)
        if not _looks_like_exec_prompt(output):
            raise SSHCollectorError(
                f"Privileged exec prompt not confirmed on {self.credentials.host} "
                "before verification"
            )
        self._privileged_confirmed = True
        if settle_seconds > 0:
            time.sleep(settle_seconds)
        self._drain(timeout=0.2)

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def _prepare_session(self) -> None:
        vendor = self.credentials.vendor
        if vendor.startswith("cisco") or vendor in ("generic", "aruba_os"):
            self._run_command("terminal length 0", wait=1.0)
            self._run_command("terminal width 0", wait=0.5)
            self._ensure_privileged_session()
        elif vendor.startswith("juniper"):
            self._run_command("set cli screen-length 0", wait=1.0)
            self._run_command("set cli screen-width 0", wait=0.5)

    def assert_privileged_mode(self) -> None:
        """Raise when the session is not in privileged exec mode (# prompt)."""
        if self._privileged_confirmed:
            return
        prompt = self._read_session_prompt()
        if _is_privileged_prompt_line(prompt):
            self._privileged_confirmed = True
            return
        host = self.credentials.host
        raise SSHCollectorError(
            f"Unable to enter privileged mode on {host}: "
            "enable password missing or rejected."
        )

    def _ensure_privileged_session(self) -> None:
        """Enter enable when needed; fail when privileged mode is required."""
        host = self.credentials.host
        prompt = self._read_session_prompt()

        if _is_privileged_prompt_line(prompt):
            self._privileged_confirmed = True
            return

        if _is_user_prompt_line(prompt):
            secret = self.credentials.secret
            if secret:
                output = self._enter_enable(secret)
                if _enable_output_indicates_failure(output):
                    if self.require_privileged:
                        raise SSHCollectorError(
                            f"Unable to enter privileged mode on {host}: "
                            "enable password rejected."
                        )
                    return
                prompt = self._read_session_prompt()
                if _is_privileged_prompt_line(prompt):
                    self._privileged_confirmed = True
                    return
                if self.require_privileged:
                    raise SSHCollectorError(
                        f"Unable to enter privileged mode on {host}: "
                        "enable password rejected."
                    )
                return

            if self.require_privileged:
                raise SSHCollectorError(
                    f"Unable to enter privileged mode on {host}: "
                    "enable password not configured."
                )
            return

        if self.require_privileged:
            raise SSHCollectorError(
                f"Unable to enter privileged mode on {host}: "
                "privileged prompt not detected."
            )

    def _read_session_prompt(self) -> str | None:
        """Elicit and return the current CLI prompt line, if detectable."""
        if self._shell is None:
            return None
        self._drain(timeout=0.2)
        self._shell.send("\n")
        time.sleep(0.4)
        buffer = self._read_until_prompt(timeout=2.0)
        return _extract_prompt_line(buffer)

    def _enter_enable(self, secret: str) -> str:
        if not self._shell:
            return ""
        self._shell.send("enable\n")
        time.sleep(0.8)
        buffer = self._drain(timeout=1.5)
        if "password" in buffer.lower() or "assword" in buffer:
            self._shell.send(secret + "\n")
            time.sleep(0.8)
            buffer += self._drain(timeout=1.5)
        return buffer

    def _run_command(
        self,
        command: str,
        wait: float = 0.4,
        *,
        require_exec_prompt: bool = False,
    ) -> str:
        if self._shell is None:
            raise SSHCollectorError("SSH shell is not connected")

        # Clear any pending data
        self._drain(timeout=0.2)
        self._shell.send(command + "\n")
        time.sleep(wait)

        output = self._read_until_prompt(
            timeout=self.credentials.timeout,
            require_exec_prompt=require_exec_prompt,
        )
        return _strip_command_echo(output, command)

    def _read_until_prompt(
        self,
        timeout: float,
        *,
        require_exec_prompt: bool = False,
    ) -> str:
        """Read shell output until an IOS/Junos-like prompt or timeout."""
        if self._shell is None:
            return ""

        deadline = time.monotonic() + timeout
        chunks: list[str] = []

        while time.monotonic() < deadline:
            if self._shell.recv_ready():
                data = self._shell.recv(65535)
                try:
                    text = data.decode("utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    text = data.decode("latin-1", errors="ignore")
                chunks.append(text)
                combined = "".join(chunks)
                prompt_seen = (
                    _looks_like_exec_prompt(combined)
                    if require_exec_prompt
                    else _looks_like_prompt(combined)
                )
                if prompt_seen:
                    # Small grace read for trailing bytes
                    time.sleep(0.15)
                    if self._shell.recv_ready():
                        continue
                    break
            else:
                time.sleep(0.2)
                # Do not terminate on idle alone — wait for prompt or hard timeout.
                # Premature idle exit caused incomplete show output during verification.

        return "".join(chunks)

    def _drain(self, timeout: float = 0.5) -> str:
        if self._shell is None:
            return ""
        deadline = time.monotonic() + timeout
        chunks: list[str] = []
        while time.monotonic() < deadline:
            if self._shell.recv_ready():
                data = self._shell.recv(65535)
                chunks.append(data.decode("utf-8", errors="ignore"))
            else:
                time.sleep(0.05)
        return "".join(chunks)


def _is_config_submode_prompt(line: str | None) -> bool:
    """True for Cisco configuration-mode prompts such as (config)# or (config-if)#."""
    if not line:
        return False
    return "(config" in line.lower()


def _is_exec_mode_prompt_line(line: str | None) -> bool:
    """True when the prompt is outside configuration submode."""
    if not line:
        return False
    if line.lower().endswith("password:"):
        return False
    if _is_config_submode_prompt(line):
        return False
    return line.endswith("#") or line.endswith(">")


def _looks_like_exec_prompt(text: str) -> bool:
    """Heuristic: last line is a non-config CLI prompt."""
    return _is_exec_mode_prompt_line(_extract_prompt_line(text))


def _command_requires_exec_prompt(command: str) -> bool:
    """Commands that must finish at privileged exec, not config submode."""
    cmd = command.strip().lower()
    if cmd.startswith("show "):
        return True
    return cmd in {"end", "exit"}


def _extract_prompt_line(text: str) -> str | None:
    """Return the last CLI prompt line from shell output, or None."""
    lines = [ln.strip() for ln in text.replace("\r", "").splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    if last.endswith("#") or last.endswith(">"):
        if last.lower().endswith("password:"):
            return None
        return last
    return None


def _is_privileged_prompt_line(line: str | None) -> bool:
    """True when the prompt line indicates privileged exec mode (ends with #)."""
    return bool(line and line.endswith("#") and not line.lower().endswith("password:"))


def _is_user_prompt_line(line: str | None) -> bool:
    """True when the prompt line indicates user exec mode (ends with >)."""
    return bool(line and line.endswith(">"))


_ENABLE_FAILURE_MARKERS = (
    "% bad secrets",
    "% bad passwords",
    "% access denied",
    "password incorrect",
    "% authentication failed",
)


def _enable_output_indicates_failure(output: str) -> bool:
    """Detect enable-password rejection in CLI output."""
    text = (output or "").lower()
    return any(marker in text for marker in _ENABLE_FAILURE_MARKERS)


def _looks_like_prompt(text: str) -> bool:
    """Heuristic: last non-empty line ends with # or > (Cisco / Junos)."""
    return _extract_prompt_line(text) is not None


def _strip_command_echo(output: str, command: str) -> str:
    """Remove the echoed command and trailing prompt from shell output."""
    lines = output.replace("\r", "").splitlines()
    cleaned: list[str] = []
    cmd_lower = command.strip().lower()

    for line in lines:
        stripped = line.strip()
        if stripped.lower() == cmd_lower:
            continue
        if stripped.lower().endswith(cmd_lower) and (
            stripped.endswith("#" + command) is False
        ):
            # Line like "switch#show interfaces status"
            if cmd_lower in stripped.lower() and (
                stripped.endswith("#") is False and stripped.endswith(">") is False
            ):
                # Keep only content after the command echo when line is echo-only
                idx = stripped.lower().rfind(cmd_lower)
                if idx >= 0 and idx + len(cmd_lower) >= len(stripped) - 1:
                    continue
        cleaned.append(line)

    # Drop trailing prompt line
    while cleaned and _looks_like_prompt("\n".join(cleaned[-1:])):
        last = cleaned[-1].strip()
        if last.endswith("#") or last.endswith(">"):
            cleaned.pop()
        else:
            break

    return "\n".join(cleaned).strip()


def collect_raw_outputs_for_device(device: dict) -> tuple[dict[str, str], str]:
    """
    Convenience helper: resolve credentials, collect outputs, return
    ``(outputs, vendor)``. Always closes the SSH session.
    """
    credentials = resolve_ssh_credentials(device)
    with SSHInterfaceCollector(credentials) as collector:
        outputs = collector.collect_raw_outputs()
    return outputs, credentials.vendor
