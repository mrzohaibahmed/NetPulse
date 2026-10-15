"""Read-only SSH hardware collector for supported switches."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from services.interface_collection.ssh_collector import (
    SSHCollectorError,
    SSHInterfaceCollector,
    resolve_ssh_credentials,
)
from services.storm.diagnostics.ssh_capture import assert_read_only_command
from services.switch_hardware.parsers import parse_platform_outputs
from services.switch_hardware.vendor_detection import detect_platform
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("switch_hardware.ssh")

_CLI_ERROR_MARKERS = (
    "% invalid input", "% ambiguous command", "% incomplete command",
    "syntax error", "unknown command", "error: command is not valid",
)


def _cli_command_error(output: str) -> bool:
    return any(marker in output.lower() for marker in _CLI_ERROR_MARKERS)

HARDWARE_COMMANDS: dict[str, dict[str, str]] = {
    "cisco_ios": {
        "version": "show version",
        "inventory": "show inventory",
        "environment": "show environment",
        "environment_all": "show environment all",
        "processes_cpu": "show processes cpu",
        "processes_memory": "show processes memory",
        "logging": "show logging | tail 100",
    },
    "cisco_xe": {
        "version": "show version",
        "inventory": "show inventory",
        "environment": "show environment all",
        "processes_cpu": "show processes cpu",
        "processes_memory": "show processes memory",
        "logging": "show logging last 100",
    },
    "cisco_nxos": {
        "version": "show version",
        "inventory": "show inventory",
        "environment": "show environment",
        "processes_cpu": "show processes cpu",
        "processes_memory": "show system resources",
        "logging": "show logging logfile | last 100",
    },
    "juniper_junos": {
        "version": "show version",
        "inventory": "show chassis hardware",
        "environment": "show chassis environment",
        "routing_engine": "show chassis routing-engine",
        "alarms": "show chassis alarms",
    },
}

INVENTORY_COMMAND_KEYS = frozenset({"version", "inventory"})
DIAGNOSTIC_COMMAND_KEYS = frozenset(
    {"environment", "environment_all", "processes_cpu", "processes_memory", "routing_engine", "alarms", "logging"}
)


def get_hardware_commands(platform: str, *, include_inventory: bool = True) -> dict[str, str]:
    base = dict(HARDWARE_COMMANDS.get(platform) or HARDWARE_COMMANDS["cisco_ios"])
    if include_inventory:
        return base
    return {k: v for k, v in base.items() if k not in INVENTORY_COMMAND_KEYS}


def collect_ssh_hardware(
    device: dict,
    *,
    platform: str | None = None,
    include_inventory: bool = True,
    timeout: float | None = None,
) -> dict[str, Any]:
    creds = resolve_ssh_credentials(device)
    if timeout is not None:
        from services.interface_collection.ssh_collector import SSHCredentials

        creds = SSHCredentials(
            host=creds.host,
            username=creds.username,
            password=creds.password,
            port=creds.port,
            secret=creds.secret,
            vendor=creds.vendor,
            timeout=timeout,
        )

    detected_platform = platform or detect_platform(device)
    if detected_platform == "juniper_junos" and creds.vendor != detected_platform:
        # The interface collector defaults to Cisco; Junos needs its own CLI setup.
        creds = replace(creds, vendor=detected_platform)
    command_platform = detected_platform or creds.vendor or "cisco_ios"
    commands = get_hardware_commands(command_platform, include_inventory=include_inventory)

    outputs: dict[str, str | None] = {}
    errors: dict[str, str] = {}

    try:
        with SSHInterfaceCollector(creds) as collector:
            for key, command in commands.items():
                try:
                    safe_cmd = assert_read_only_command(command)
                    output = collector.run_command(safe_cmd)
                    if _cli_command_error(output):
                        raise SSHCollectorError(f"Device rejected '{command}'")
                    outputs[key] = output
                except Exception as exc:  # noqa: BLE001
                    outputs[key] = None
                    errors[key] = str(exc)
                    logger.debug(
                        "SSH hardware soft-fail | host=%s | key=%s | %s",
                        creds.host,
                        key,
                        exc,
                    )
    except SSHCollectorError as exc:
        logger.info("SSH hardware collection failed | host=%s | %s", creds.host, exc)
        return {
            "parsed": {},
            "outputs": outputs,
            "errors": {"connection": str(exc)},
            "platform": detected_platform,
            "availability": {"ssh": "unavailable"},
        }

    platform = detect_platform(
        device,
        show_version_output=outputs.get("version"),
    ) or command_platform

    parsed = parse_platform_outputs(platform, outputs)
    successful = {key for key, output in outputs.items() if output is not None}
    availability = "available" if not errors else ("partial" if successful else "unavailable")
    parsed["availability"] = {"ssh": availability}
    return {
        "parsed": parsed,
        "outputs": {},
        "errors": errors,
        "observed": [key for key, output in outputs.items() if output],
        "platform": platform,
        "availability": parsed["availability"],
    }
