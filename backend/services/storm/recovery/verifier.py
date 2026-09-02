"""
Verification of interface operational status and metrics collection after recovery.
"""

from __future__ import annotations

from typing import Any

from services.storm.diagnostics.snapshots import parse_interface_snapshot
from services.storm.mitigation.strategy import NoShutdownRecoveryStrategy
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.mitigation.verifier import verify_mitigation
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.verifier")


def verify_interface_up(executor: SSHMitigationExecutor, interface: str) -> tuple[bool, str]:
    """
    Verify that the recovered interface is administratively UP.

    Uses the same running-config verification as the NO_SHUTDOWN mitigation
    strategy so recovery and shutdown paths share one reliable check.

    Returns
    -------
    (bool, str)
        Tuple of (is_up, raw_cli_output)
    """
    logger.info(
        "Verifying interface up | host=%s | interface=%s",
        executor.creds.host,
        interface,
    )
    return verify_mitigation(
        executor,
        NoShutdownRecoveryStrategy(),
        interface,
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
