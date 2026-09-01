"""
Verification of interface operational status and metrics collection after recovery.
"""

from __future__ import annotations

from typing import Any

from services.storm.diagnostics.snapshots import parse_interface_snapshot
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.ssh_verification_retry import verify_with_bounded_retry
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.verifier")


def verify_interface_up(executor: SSHMitigationExecutor, interface: str) -> tuple[bool, str]:
    """
    Verify that the recovered interface is administratively UP.

    Returns
    -------
    (bool, str)
        Tuple of (is_up, raw_cli_output)
    """
    vendor = (executor.creds.vendor or "cisco_ios").lower()
    from utils.ssh_security import assert_safe_interface_name  # noqa: PLC0415

    safe_iface = assert_safe_interface_name(interface)
    if "juniper" in vendor or "junos" in vendor:
        cmd = f"show interfaces {safe_iface}"
    else:
        cmd = f"show interfaces {safe_iface}"

    logger.info("Verifying interface up | host=%s | command=%s", executor.creds.host, cmd)

    def _single_attempt() -> tuple[bool, str]:
        try:
            collector = executor.collector
            if collector is None:
                raise RuntimeError("SSH executor is not connected")
            output = collector.run_command(cmd)
            snapshot = parse_interface_snapshot(output, safe_iface)

            # Check if interface is administratively UP (not down)
            is_up = bool(snapshot.get("available") and snapshot.get("adminStatus") == "up")
            return is_up, output
        except Exception as exc:
            logger.error("Verification command failed | %s | %s", interface, exc)
            return False, f"CLI command error: {exc}"

    return verify_with_bounded_retry(
        label="recovery:interface_up",
        attempt_fn=_single_attempt,
    )


def collect_post_recovery_stats(device: dict, interface: str) -> dict[str, Any]:
    """
    Collect post-recovery validation statistics from switch over SSH.
    Gathers: status, broadcast rate, multicast rate, utilization, errors, CRC, discards.
    """
    from services.storm.diagnostics.ssh_capture import capture_show_outputs  # noqa: PLC0415
    from services.storm.diagnostics.snapshots import (  # noqa: PLC0415
        parse_interface_snapshot,
        parse_switchport_snapshot,
    )

    stats = {
        "adminStatus": "unknown",
        "operStatus": "unknown",
        "broadcastRate": 0,
        "multicastRate": 0,
        "utilization": 0.0,
        "inputErrors": 0,
        "outputErrors": 0,
        "crc": 0,
        "discards": 0,
    }

    try:
        ssh_res = capture_show_outputs(device, interface)
        if not ssh_res.get("success"):
            return stats

        outputs = ssh_res.get("outputs") or {}
        if "interface" in outputs:
            snap = parse_interface_snapshot(outputs["interface"], interface)
            stats["adminStatus"] = snap.get("adminStatus") or "unknown"
            stats["operStatus"] = snap.get("operStatus") or "unknown"

            errs = snap.get("errors") or {}
            stats["inputErrors"] = errs.get("input") or 0
            stats["outputErrors"] = errs.get("output") or 0
            stats["crc"] = snap.get("crc") or 0

            disc = snap.get("discards") or {}
            stats["discards"] = (disc.get("input") or 0) + (disc.get("output") or 0)

        # Retrieve risk traffic metrics from database for rates
        # (Since rates require delta over time, latest database risk results provide them)
        from config.database import db  # noqa: PLC0415
        latest_risk = db.storm_risk_history.find_one(
            {"deviceId": device.get("_id"), "interface": interface},
            sort=[("timestamp", -1)],
        )
        if latest_risk:
            # Flattened rates are inside latest_risk
            stats["broadcastRate"] = latest_risk.get("broadcastRate") or 0
            stats["multicastRate"] = latest_risk.get("multicastRate") or 0
            stats["utilization"] = latest_risk.get("utilization") or 0.0

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to collect post-recovery statistics | %s | %s", interface, exc)

    return stats
