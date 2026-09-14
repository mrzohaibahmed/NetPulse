"""Subscribe to ping monitor events for outage incident lifecycle."""

from __future__ import annotations

from bson import ObjectId

from config.database import db
from services.monitor_events import EVENT_DEVICE_STATUS_CHANGED, subscribe
from services.switch_hardware import config as hw_config

STATUS_ONLINE = "Online"
from services.switch_hardware.outage_analyzer import (
    finalize_outage_incident,
    is_offline_status,
    is_online_status,
    start_outage_incident,
)
from services.switch_hardware.vendor_detection import is_eligible_switch
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("switch_hardware.ping_hooks")

_registered = False


def _device_id_from_payload(payload: dict):
    raw = payload.get("deviceId")
    if raw is None:
        return None
    if isinstance(raw, ObjectId):
        return raw
    if isinstance(raw, str) and ObjectId.is_valid(raw):
        return ObjectId(raw)
    return None


def _handle_status_changed(event_type: str, payload: dict) -> None:
    if not hw_config.is_hardware_monitoring_enabled():
        return

    device_id = _device_id_from_payload(payload)
    if device_id is None:
        return

    device = db.devices.find_one({"_id": device_id})
    if not device or not is_eligible_switch(device):
        return

    previous = payload.get("previousStatus")
    new_status = payload.get("newStatus") or payload.get("status")

    if is_online_status(previous) and is_offline_status(new_status):
        hardware = db.switch_hardware_current.find_one({"deviceId": device_id})
        incident = start_outage_incident(
            device_id,
            started_at=utc_now(),
            last_known_hardware=hardware,
        )
        incident["timeline"] = [
            {
                "at": incident["startedAt"],
                "event": "offline",
                "source": "ping",
                "previousStatus": previous,
                "newStatus": new_status,
            }
        ]
        db.switch_outage_incidents.insert_one(incident)
        logger.info(
            "Switch outage incident opened | deviceId=%s | status=%s",
            device_id,
            new_status,
        )

    if is_offline_status(previous) and is_online_status(new_status):
        active = db.switch_outage_incidents.find_one(
            {"deviceId": device_id, "status": "active"},
            sort=[("startedAt", -1)],
        )
        if not active:
            return
        recovery_hardware = db.switch_hardware_current.find_one({"deviceId": device_id})
        log_evidence = ((recovery_hardware or {}).get("evidence") or {}).get("logEvidence")
        finalized = finalize_outage_incident(
            active,
            ended_at=utc_now(),
            recovery_hardware=recovery_hardware,
            log_evidence=log_evidence,
            ping_evidence={"previousStatus": previous, "newStatus": new_status},
        )
        db.switch_outage_incidents.update_one(
            {"_id": active["_id"]},
            {"$set": finalized},
        )
        logger.info(
            "Switch outage incident closed | deviceId=%s | rootCause=%s confidence=%s",
            device_id,
            finalized.get("rootCause"),
            finalized.get("confidence"),
        )


def register_ping_hooks() -> None:
    global _registered
    if _registered:
        return

    def handler(event_type: str, payload: dict) -> None:
        try:
            if event_type == EVENT_DEVICE_STATUS_CHANGED:
                _handle_status_changed(event_type, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Hardware ping hook failed | %s", exc)

    subscribe(handler)
    _registered = True
