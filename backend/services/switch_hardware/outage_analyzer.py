"""Evidence-based switch outage root cause analysis."""

from __future__ import annotations

import re
from typing import Any

from utils.utc import utc_now

STATUS_ONLINE = "Online"
OFFLINE_STATUSES = frozenset({"Not Reachable", "Offline (Critical)"})

THERMAL_PATTERNS = re.compile(
    r"thermal|over\s*temperature|overheat|temperature shutdown",
    re.IGNORECASE,
)
POWER_PATTERNS = re.compile(
    r"power\s*(fail|loss|supply)|psu|supply fail",
    re.IGNORECASE,
)
REBOOT_PATTERNS = re.compile(
    r"reload|reboot|watchdog|crash|software forced|system restarted",
    re.IGNORECASE,
)
HARDWARE_FAIL_PATTERNS = re.compile(
    r"hardware|asic|memory error|parity|fault",
    re.IGNORECASE,
)


def is_offline_status(status: str | None) -> bool:
    return (status or "") in OFFLINE_STATUSES


def is_online_status(status: str | None) -> bool:
    return (status or "") == STATUS_ONLINE


def analyze_outage(
    *,
    last_known_hardware: dict[str, Any] | None,
    recovery_hardware: dict[str, Any] | None,
    log_evidence: list[dict[str, Any]] | None,
    ping_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence: list[str] = []
    root_cause = "unknown"
    confidence = "unknown"
    confirmed = False

    pre = last_known_hardware or {}
    post = recovery_hardware or {}
    logs = [e.get("message", "") for e in (log_evidence or []) if e.get("message")]

    pre_temp = pre.get("temperature", {})
    pre_fans = pre.get("fans", {})
    pre_psus = pre.get("powerSupplies", {})
    pre_alarms = pre.get("hardwareAlarms") or []

    if pre_temp.get("status") == "critical":
        evidence.append("Critical temperature reported before outage")
    if (pre_fans.get("failedCount") or 0) > 0:
        evidence.append("Fan failure reported before outage")
    if (pre_psus.get("failedCount") or 0) > 0:
        evidence.append("PSU failure reported before outage")
    for alarm in pre_alarms:
        evidence.append(f"Pre-outage alarm: {alarm.get('message', 'hardware alarm')}")

    post_inventory = post.get("inventory") or {}
    pre_inventory = pre.get("inventory") or {}
    pre_uptime = pre_inventory.get("uptime")
    post_uptime = post_inventory.get("uptime")
    boot_reason = post_inventory.get("bootReason")

    if boot_reason:
        evidence.append(f"Boot reason after recovery: {boot_reason}")

    reboot_detected = False
    if pre_uptime and post_uptime and pre_uptime != post_uptime:
        evidence.append("Uptime reset detected after recovery")
        reboot_detected = True

    log_text = "\n".join(logs)
    if THERMAL_PATTERNS.search(log_text):
        evidence.append("Thermal-related log evidence found")
        root_cause = "thermal_shutdown"
        confidence = "confirmed" if THERMAL_PATTERNS.search(boot_reason or "") else "suspected"
        confirmed = confidence == "confirmed"

    if POWER_PATTERNS.search(log_text) or (
        (pre_psus.get("failedCount") or 0) > 0 and is_offline_status((ping_evidence or {}).get("previousStatus"))
    ):
        if root_cause == "unknown":
            root_cause = "power_failure"
            confidence = "suspected" if not POWER_PATTERNS.search(log_text) else "confirmed"
            confirmed = confidence == "confirmed"
        evidence.append("Power-related evidence found")

    if REBOOT_PATTERNS.search(log_text) or reboot_detected:
        if root_cause == "unknown":
            root_cause = "software_crash_or_reload"
            confidence = "suspected"
        evidence.append("Reboot/reload evidence found")

    if HARDWARE_FAIL_PATTERNS.search(log_text):
        if root_cause == "unknown":
            root_cause = "hardware_failure"
            confidence = "suspected"
        evidence.append("Hardware failure log evidence found")

    if not evidence:
        evidence.append("Switch became unreachable without verified hardware evidence")

    explanation = _build_explanation(root_cause, confidence, evidence)

    return {
        "rootCause": root_cause,
        "confidence": confidence,
        "confirmed": confirmed,
        "evidence": evidence,
        "explanation": explanation,
        "analyzedAt": utc_now(),
    }


def _build_explanation(root_cause: str, confidence: str, evidence: list[str]) -> str:
    if root_cause == "unknown":
        return (
            "Switch became unreachable, but no verified power, thermal, or reboot "
            "evidence was available."
        )
    return (
        f"Root cause classified as {root_cause} ({confidence}) based on "
        f"{len(evidence)} evidence item(s)."
    )


def start_outage_incident(device_id, *, started_at, last_known_hardware: dict | None) -> dict[str, Any]:
    from services.switch_hardware.models import build_outage_document

    return build_outage_document(
        device_id,
        started_at=started_at,
        evidence={"trigger": "ping_offline_transition"},
        last_known_hardware=last_known_hardware or {},
    )


def finalize_outage_incident(
    incident: dict[str, Any],
    *,
    ended_at,
    recovery_hardware: dict | None,
    log_evidence: list[dict[str, Any]] | None,
    ping_evidence: dict | None,
) -> dict[str, Any]:
    started = incident.get("startedAt")
    duration = None
    if started and ended_at:
        duration = int((ended_at - started).total_seconds())

    analysis = analyze_outage(
        last_known_hardware=incident.get("lastKnownHardware"),
        recovery_hardware=recovery_hardware,
        log_evidence=log_evidence,
        ping_evidence=ping_evidence,
    )

    timeline = list(incident.get("timeline") or [])
    timeline.append({"at": ended_at, "event": "recovery", "source": "ping"})

    incident.update(
        {
            "endedAt": ended_at,
            "durationSeconds": duration,
            "status": "resolved",
            "rootCause": analysis["rootCause"],
            "confidence": analysis["confidence"],
            "confirmed": analysis["confirmed"],
            "evidence": {
                **(incident.get("evidence") or {}),
                "items": analysis["evidence"],
                "explanation": analysis["explanation"],
            },
            "recoveryHardware": recovery_hardware or {},
            "timeline": timeline,
            "updatedAt": utc_now(),
        }
    )
    return incident
