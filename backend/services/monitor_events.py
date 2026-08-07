"""
Internal event publisher for future WebSocket broadcasting (Phase 14).

Events are published in-process only. No frontend or REST contract changes.
Subscribers can be registered later (e.g. a WebSocket hub) without touching
the monitoring pipeline.
"""

from __future__ import annotations

from typing import Any, Callable

from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("monitor_events")

# Event type constants — stable names for future WS payloads.
EVENT_DEVICE_STATUS_CHANGED = "device.status_changed"
EVENT_DEVICE_RECOVERED = "device.recovered"
EVENT_PING_HISTORY_ADDED = "ping.history_added"
EVENT_ALERT_CREATED = "alert.created"
EVENT_ALERT_RESOLVED = "alert.resolved"
EVENT_DASHBOARD_METRICS_CHANGED = "dashboard.metrics_changed"
EVENT_COLLECTOR_HEALTH = "collector.health"

EventHandler = Callable[[str, dict[str, Any]], None]

_handlers: list[EventHandler] = []


def subscribe(handler: EventHandler) -> None:
    """Register a listener. Safe to call at import / startup time."""
    if handler not in _handlers:
        _handlers.append(handler)


def unsubscribe(handler: EventHandler) -> None:
    try:
        _handlers.remove(handler)
    except ValueError:
        pass


def publish(event_type: str, payload: dict[str, Any] | None = None) -> None:
    """
    Publish a monitoring event to all subscribers.

    Failures in handlers never interrupt monitoring.
    """
    body = dict(payload or {})
    body.setdefault("eventType", event_type)
    body.setdefault("publishedAt", utc_now().isoformat().replace("+00:00", "Z"))

    logger.debug("Event published | type=%s | keys=%s", event_type, list(body.keys()))

    for handler in list(_handlers):
        try:
            handler(event_type, body)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Event handler failed | type=%s | error=%s",
                event_type,
                exc,
            )


def _log_subscriber(event_type: str, payload: dict[str, Any]) -> None:
    """Default subscriber — structured log for production troubleshooting."""
    logger.info(
        "Monitor event | type=%s | deviceId=%s | hostname=%s | ip=%s | status=%s",
        event_type,
        payload.get("deviceId"),
        payload.get("hostname"),
        payload.get("ipAddress"),
        payload.get("status") or payload.get("newStatus"),
    )


# Always keep a log subscriber so events are visible without WS.
subscribe(_log_subscriber)
