import atexit

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config.database import (
    INTERFACE_SCAN_INTERVAL,
    INTERFACE_STATS_INTERVAL,
    NMAP_SCAN_INTERVAL,
)
from services.monitor_service import monitor_all_devices
from services.scheduler_ownership import (
    release_scheduler_ownership,
    require_scheduler_leadership,
)
from services.settings_service import get_settings
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("scheduler")

# Single shared scheduler instance.
# Ping, Nmap, interface discovery, interface stats, and eligibility share one
# scheduler. Eligibility runs after stats in the same job chain.
# Phase 5: only the MongoDB-elected leader executes job bodies.
scheduler = BackgroundScheduler()

# Job IDs — kept as named constants to make reschedule helpers readable.
JOB_ID = "device_monitor_job"
NMAP_JOB_ID = "nmap_scan_job"
INTERFACE_JOB_ID = "interface_discovery_job"
INTERFACE_STATS_JOB_ID = "interface_stats_job"
RECOVERY_JOB_ID = "storm_recovery_job"
RETENTION_JOB_ID = "data_retention_job"


def _run_interface_stats_then_eligibility() -> None:
    """
    Scheduler chain:

        Interface Statistics
        → Eligibility Engine
        → Risk Score Engine
        → Confirmation Engine
        → Safety Engine
        → Diagnostics Capture
        → Incident + Orchestrator Prepare
        → Automatic Mitigation (when mitigationMode == "automatic")

    Failures never abort stats or the scheduler.

    After prepare, if settings.mitigationMode == "automatic", READY_FOR_MITIGATION
    incidents are shutdown via execute_mitigation. Otherwise the chain stops after
    prepare and waits for an admin to trigger mitigation manually.
    """
    # Phase 5 — skip on non-leader instances.
    if not require_scheduler_leadership("interface_stats_job"):
        return

    from services.interface_collection.stats_collector import (  # noqa: PLC0415
        collect_all_interface_stats,
    )

    collect_all_interface_stats()

    try:
        from services.storm.eligibility import evaluate_all_interfaces  # noqa: PLC0415

        evaluate_all_interfaces()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Eligibility evaluation failed after interface stats: %s",
            exc,
        )

    try:
        from services.storm.risk_engine import calculate_all_risks  # noqa: PLC0415

        calculate_all_risks()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Risk scoring failed after eligibility: %s",
            exc,
        )

    try:
        from services.storm.confirmation import (  # noqa: PLC0415
            evaluate_all_confirmations,
        )

        evaluate_all_confirmations()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Confirmation evaluation failed after risk scoring: %s",
            exc,
        )

    try:
        from services.storm.safety import evaluate_all_safety  # noqa: PLC0415

        # Bulk safety may probe SSH; failures are contained per-interface.
        evaluate_all_safety(probe_ssh=True)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Safety evaluation failed after confirmation: %s",
            exc,
        )

    try:
        from services.storm.orchestrator import prepare_all_safe  # noqa: PLC0415

        # Diagnostics + incident prep — prepare itself never shuts ports down.
        prepare_all_safe(probe_ssh=True)

        # Automatic Mitigation Engine execution check
        from services.settings_service import get_settings  # noqa: PLC0415
        settings = get_settings()
        if settings.get("mitigationMode") == "automatic":
            from config.database import db  # noqa: PLC0415
            from services.storm.mitigation.engine import execute_mitigation  # noqa: PLC0415

            ready_incidents = list(db.storm_incidents.find({"status": "READY_FOR_MITIGATION"}))
            if ready_incidents:
                logger.info(
                    "[SCHEDULER] Automatic mitigation active. Found %d ready incident(s)",
                    len(ready_incidents),
                )
                for inc in ready_incidents:
                    inc_id = inc.get("incidentId")
                    logger.info("[SCHEDULER] Auto-executing mitigation for %s", inc_id)
                    res = execute_mitigation(inc_id, "SHUTDOWN", operator="SYSTEM")
                    logger.info("[SCHEDULER] Auto-mitigation status for %s: %s", inc_id, res.get("status"))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Orchestrator prepare / automatic mitigation failed after safety: %s",
            exc,
        )


def _run_nmap_job() -> None:
    if not require_scheduler_leadership("nmap_scan_job"):
        return
    from services.nmap_service import scan_all_online_devices  # noqa: PLC0415

    scan_all_online_devices()


def _run_interface_discovery_job() -> None:
    if not require_scheduler_leadership("interface_discovery_job"):
        return
    from services.interface_collection.collector import (  # noqa: PLC0415
        discover_all_switch_interfaces,
    )

    discover_all_switch_interfaces()


def _start_nmap_job() -> None:
    """
    Register the Nmap periodic scan job on the shared scheduler.

    The Nmap job is completely independent of the ping job:
    - Different job ID  (NMAP_JOB_ID vs JOB_ID)
    - Different interval (NMAP_SCAN_INTERVAL from .env, default 3600 s)
    - Different target function (scan_all_online_devices)

    Importing nmap_service here (inside the function) rather than at module
    level avoids a circular-import risk and ensures the import happens only
    after Flask's bootstrap is complete.  It also means the app starts
    normally even if python-nmap is not yet installed — the scheduler will
    log an error when the job first fires rather than crashing at startup.
    """
    try:
        interval = max(int(NMAP_SCAN_INTERVAL), 60)  # enforce minimum 60 s

        scheduler.add_job(
            func=_run_nmap_job,
            trigger="interval",
            seconds=interval,
            id=NMAP_JOB_ID,
            replace_existing=True,
        )
        logger.info(
            "Nmap scan job registered | interval=%ss (%dm)",
            interval,
            interval // 60,
        )
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: ping monitoring must not be blocked by Nmap setup issues.
        logger.warning(
            "Nmap scan job could not be registered: %s. "
            "Ensure python-nmap is installed and nmap binary is accessible.",
            exc,
        )


def _start_interface_job() -> None:
    """
    Register the SSH interface discovery job on the shared scheduler.

    Independent of ping and Nmap. Disabled when INTERFACE_SCAN_INTERVAL is 0.
    """
    try:
        interval = int(INTERFACE_SCAN_INTERVAL)
        if interval <= 0:
            logger.info(
                "Interface discovery job disabled "
                "(INTERFACE_SCAN_INTERVAL=%s)",
                INTERFACE_SCAN_INTERVAL,
            )
            return

        interval = max(interval, 60)

        scheduler.add_job(
            func=_run_interface_discovery_job,
            trigger="interval",
            seconds=interval,
            id=INTERFACE_JOB_ID,
            replace_existing=True,
        )
        logger.info(
            "Interface discovery job registered | interval=%ss (%dm)",
            interval,
            interval // 60,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Interface discovery job could not be registered: %s",
            exc,
        )


def _start_interface_stats_job() -> None:
    """
    Register periodic interface statistics collection.

    Completely independent of the ping monitor job — failures here never
    affect device reachability monitoring.
    """
    try:
        interval = int(INTERFACE_STATS_INTERVAL)
        if interval <= 0:
            logger.info(
                "Interface stats job disabled (INTERFACE_STATS_INTERVAL=%s)",
                INTERFACE_STATS_INTERVAL,
            )
            return

        interval = max(interval, 15)

        # Stats collection followed by Port Eligibility Engine (append-only).
        scheduler.add_job(
            func=_run_interface_stats_then_eligibility,
            trigger="interval",
            seconds=interval,
            id=INTERFACE_STATS_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Interface stats + eligibility job registered | interval=%ss",
            interval,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Interface stats job could not be registered: %s",
            exc,
        )


def _start_recovery_job() -> None:
    """Register storm recovery scheduler check job."""
    try:
        scheduler.add_job(
            func=_run_recovery_cycle,
            trigger="interval",
            seconds=30,
            id=RECOVERY_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Storm recovery job registered | interval=30s")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Storm recovery job could not be registered: %s", exc)


def _run_recovery_cycle() -> None:
    if not require_scheduler_leadership("storm_recovery_job"):
        return
    try:
        from services.storm.recovery import run_recovery_cycle  # noqa: PLC0415
        run_recovery_cycle()
    except Exception as exc:  # noqa: BLE001
        logger.error("Periodic recovery cycle execution failed: %s", exc)


def _run_retention_cycle() -> None:
    """Daily: refresh TTL indexes from settings + purge closed storm incidents."""
    if not require_scheduler_leadership("data_retention_job"):
        return
    try:
        from services.retention_service import (  # noqa: PLC0415
            ensure_retention_ttl_indexes,
            purge_closed_storm_incidents,
        )

        ensure_retention_ttl_indexes()
        purge_closed_storm_incidents()
    except Exception as exc:  # noqa: BLE001
        logger.error("Data retention cycle failed: %s", exc)


def _start_retention_job() -> None:
    """Register low-frequency retention job (daily; not on the stats/storm interval)."""
    try:
        scheduler.add_job(
            func=_run_retention_cycle,
            trigger=CronTrigger(hour=3, minute=15),
            id=RETENTION_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Data retention job registered | cron=03:15 daily")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Data retention job could not be registered: %s", exc)


# ---------------------------------------------------------------------------
# Public functions (unchanged signatures for existing callers)
# ---------------------------------------------------------------------------

def start_scheduler():
    """Start automatic device monitoring using persisted settings."""
    if scheduler.running:
        logger.info("Scheduler already running")
        return

    settings = get_settings()
    interval = int(settings.get("pingInterval") or 30)

    # Job 1: Ping-based online/offline monitoring (unchanged behaviour).
    # Leadership is enforced inside monitor_all_devices (Phase 5).
    scheduler.add_job(
        func=monitor_all_devices,
        trigger="interval",
        seconds=interval,
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info("Scheduler started | ping_interval=%ss", interval)

    # Job 2: Nmap metadata scan (independent; registered after scheduler.start).
    _start_nmap_job()

    # Job 3: SSH interface discovery (independent of ping / Nmap).
    _start_interface_job()

    # Job 4: Interface statistics (SNMP preferred, SSH fallback).
    _start_interface_stats_job()

    # Job 5: Storm Recovery periodic auto-recovery checks (30s interval).
    _start_recovery_job()

    # Job 6: Data retention (TTL refresh + closed-incident purge) — daily.
    _start_retention_job()


def reschedule_monitor_job(interval_seconds: int):
    """Update ping interval without restarting the server (FR8.3)."""
    interval = max(int(interval_seconds), 5)

    if not scheduler.running:
        start_scheduler()
        return

    scheduler.add_job(
        func=monitor_all_devices,
        trigger="interval",
        seconds=interval,
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Ping scheduler rescheduled | interval=%ss", interval)


def reschedule_nmap_job(interval_seconds: int) -> None:
    """
    Update the Nmap scan interval without restarting the server.

    Parameters
    ----------
    interval_seconds : int
        New interval in seconds. Enforced minimum: 60 s.
    """
    interval = max(int(interval_seconds), 60)

    if not scheduler.running:
        logger.warning("Scheduler not running; cannot reschedule Nmap job")
        return

    try:
        scheduler.add_job(
            func=_run_nmap_job,
            trigger="interval",
            seconds=interval,
            id=NMAP_JOB_ID,
            replace_existing=True,
        )
        logger.info("Nmap scan job rescheduled | interval=%ss", interval)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to reschedule Nmap job: %s", exc)


def stop_scheduler():
    if not scheduler.running:
        return

    # Phase 5 — release lease so another instance can take over immediately.
    try:
        release_scheduler_ownership()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ownership release during shutdown failed: %s", exc)

    scheduler.shutdown(wait=False)
    logger.info("Scheduler shutdown")


atexit.register(stop_scheduler)
