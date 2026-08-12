import atexit
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config.database import (
    ARP_ACTIVE_SWEEP_INTERVAL,
    INTERFACE_SCAN_INTERVAL,
    INTERFACE_STATS_INTERVAL,
    MAC_ARP_POLL_INTERVAL,
    NMAP_SCAN_INTERVAL,
)
from services.isp_monitor_service import monitor_all_isp_connections
from services.monitor_dispatch import dispatch_monitor_due_devices
from services.monitor_service import monitor_all_devices
from services.scheduler_ownership import (
    release_scheduler_ownership,
    require_scheduler_leadership,
)
from services.settings_service import (
    get_monitor_dispatcher_interval_seconds,
    get_monitor_runtime_mode,
    get_settings,
)
from utils.monitor_logger import get_monitor_logger
from utils.utc import format_utc, utc_now

logger = get_monitor_logger("scheduler")

# Single shared scheduler instance.
# Ping, Nmap, interface discovery, interface stats, and eligibility share one
# scheduler. Eligibility runs after stats in the same job chain.
# Phase 5: only the MongoDB-elected leader executes job bodies.
scheduler = BackgroundScheduler()

# Process-level lock — prevents concurrent start_scheduler() races before
# ``scheduler.running`` becomes true.
_scheduler_init_lock = threading.Lock()

# Job IDs — kept as named constants to make reschedule helpers readable.
JOB_ID = "device_monitor_job"
NMAP_JOB_ID = "nmap_scan_job"
INTERFACE_JOB_ID = "interface_discovery_job"
INTERFACE_STATS_JOB_ID = "interface_stats_job"
RECOVERY_JOB_ID = "storm_recovery_job"
RETENTION_JOB_ID = "data_retention_job"
ISP_JOB_ID = "isp_monitor_job"
MAC_ARP_POLL_JOB_ID = "mac_arp_poll_job"
ARP_ACTIVE_SWEEP_JOB_ID = "arp_active_sweep_job"


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


def _run_mac_arp_poll() -> None:
    if not require_scheduler_leadership("mac_arp_poll_job"):
        return
    try:
        from services.interface_collection.mac_arp_collector import (  # noqa: PLC0415
            poll_all_devices_mac_and_arp,
        )
        poll_all_devices_mac_and_arp()
    except Exception as exc:  # noqa: BLE001
        logger.error("Passive MAC/ARP poll failed: %s", exc)


def _start_mac_arp_poll_job() -> None:
    """
    Register the passive MAC-table / ARP-table poll job.

    Independent of stats/discovery. Disabled when MAC_ARP_POLL_INTERVAL is 0.
    """
    try:
        interval = int(MAC_ARP_POLL_INTERVAL)
        if interval <= 0:
            logger.info(
                "MAC/ARP passive poll job disabled (MAC_ARP_POLL_INTERVAL=%s)",
                MAC_ARP_POLL_INTERVAL,
            )
            return

        interval = max(interval, 30)

        scheduler.add_job(
            func=_run_mac_arp_poll,
            trigger="interval",
            seconds=interval,
            id=MAC_ARP_POLL_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("MAC/ARP passive poll job registered | interval=%ss", interval)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MAC/ARP passive poll job could not be registered: %s", exc)


def _run_arp_active_sweep() -> None:
    if not require_scheduler_leadership("arp_active_sweep_job"):
        return
    try:
        from services.interface_collection.mac_arp_collector import (  # noqa: PLC0415
            sweep_all_device_subnets,
        )
        sweep_all_device_subnets()
    except Exception as exc:  # noqa: BLE001
        logger.error("Active ARP sweep failed: %s", exc)


def _start_arp_active_sweep_job() -> None:
    """
    Register the active ARP-forcing subnet sweep job.

    Deliberately slower / more invasive than the passive poll — pings
    unresolved addresses in each router's real connected subnets to catch
    silent devices the passive poll can never see. Disabled when
    ARP_ACTIVE_SWEEP_INTERVAL is 0.
    """
    try:
        interval = int(ARP_ACTIVE_SWEEP_INTERVAL)
        if interval <= 0:
            logger.info(
                "Active ARP sweep job disabled (ARP_ACTIVE_SWEEP_INTERVAL=%s)",
                ARP_ACTIVE_SWEEP_INTERVAL,
            )
            return

        interval = max(interval, 300)

        scheduler.add_job(
            func=_run_arp_active_sweep,
            trigger="interval",
            seconds=interval,
            id=ARP_ACTIVE_SWEEP_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Active ARP sweep job registered | interval=%ss", interval)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Active ARP sweep job could not be registered: %s", exc)


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

def _register_isp_monitor_job(interval: int) -> None:
    """Register ISP connectivity monitoring on the shared scheduler."""
    scheduler.add_job(
        func=monitor_all_isp_connections,
        trigger="interval",
        seconds=interval,
        id=ISP_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("ISP monitor job registered | interval=%ss", interval)


def _register_device_monitor_job() -> None:
    """
    Register the device ping monitor job for the active runtime mode.

    legacy  → monitor_all_devices every pingInterval
    dispatch → dispatch_monitor_due_devices every MONITOR_DISPATCHER_INTERVAL_SECONDS
    """
    mode = get_monitor_runtime_mode()
    settings = get_settings()
    ping_interval = int(settings.get("pingInterval") or 60)

    if mode == "dispatch":
        from services.monitor_runtime import start_monitor_runtime  # noqa: PLC0415

        start_monitor_runtime()
        dispatcher_interval = get_monitor_dispatcher_interval_seconds()
        scheduler.add_job(
            func=dispatch_monitor_due_devices,
            trigger="interval",
            seconds=dispatcher_interval,
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Device monitor job registered | mode=dispatch | "
            "dispatcher_interval=%ss | pingInterval=%ss (device cadence only)",
            dispatcher_interval,
            ping_interval,
        )
        return

    scheduler.add_job(
        func=monitor_all_devices,
        trigger="interval",
        seconds=ping_interval,
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "Device monitor job registered | mode=legacy | ping_interval=%ss",
        ping_interval,
    )


def start_scheduler():
    """Start automatic device monitoring using persisted settings."""
    with _scheduler_init_lock:
        if scheduler.running:
            logger.info(
                "Scheduler already running | ts=%s | skip_duplicate_start=true",
                format_utc(utc_now()),
            )
            return

        settings = get_settings()
        interval = int(settings.get("pingInterval") or 60)

        # Job 1: Device reachability monitoring (legacy wave OR dispatch).
        _register_device_monitor_job()

        # Job 1b: ISP connectivity monitoring (independent job body).
        _register_isp_monitor_job(interval)

        scheduler.start()
        logger.info(
            "Scheduler started | ts=%s | runtime_mode=%s | ping_interval=%ss",
            format_utc(utc_now()),
            get_monitor_runtime_mode(),
            interval,
        )

        # Job 2: Nmap metadata scan (independent; registered after scheduler.start).
        _start_nmap_job()

        # Job 3: SSH interface discovery (independent of ping / Nmap).
        _start_interface_job()

        # Job 4: Interface statistics (SNMP preferred, SSH fallback).
        _start_interface_stats_job()

        # Job 4b: Passive MAC/ARP table poll (port -> connected-device IP).
        _start_mac_arp_poll_job()

        # Job 4c: Active ARP-forcing subnet sweep (catches silent devices).
        _start_arp_active_sweep_job()

        # Job 5: Storm Recovery periodic auto-recovery checks (30s interval).
        _start_recovery_job()

        # Job 6: Data retention (TTL refresh + closed-incident purge) — daily.
        _start_retention_job()


def reschedule_dispatcher_job() -> None:
    """
    Re-bind the dispatch-mode device job to MONITOR_DISPATCHER_INTERVAL_SECONDS.

    Independent of ``pingInterval``. Safe to call on env reload; uses
    ``replace_existing=True`` so a second job is never created. No-op in
    legacy mode or when the scheduler is not running.
    """
    if not scheduler.running:
        return
    if get_monitor_runtime_mode() != "dispatch":
        return

    dispatcher_interval = get_monitor_dispatcher_interval_seconds()
    scheduler.add_job(
        func=dispatch_monitor_due_devices,
        trigger="interval",
        seconds=dispatcher_interval,
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "Dispatcher job rescheduled | mode=dispatch | dispatcher_interval=%ss",
        dispatcher_interval,
    )


def reschedule_monitor_job(interval_seconds: int):
    """
    React to a ``pingInterval`` settings change without restarting the server.

    Legacy mode: APScheduler device job period tracks ``pingInterval``.

    Dispatch mode: does **not** change the dispatcher period (that stays
    ``MONITOR_DISPATCHER_INTERVAL_SECONDS``). ``pingInterval`` only affects
    ``nextCheckAt`` on subsequent ``claim_device`` calls. Existing
    ``nextCheckAt`` values are not mass-rewritten here.
    """
    interval = max(int(interval_seconds), 5)

    if not scheduler.running:
        start_scheduler()
        return

    mode = get_monitor_runtime_mode()
    if mode == "legacy":
        # Legacy: device job period tracks pingInterval.
        scheduler.add_job(
            func=monitor_all_devices,
            trigger="interval",
            seconds=interval,
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Ping scheduler rescheduled | mode=legacy | interval=%ss", interval)
    else:
        # Dispatch: leave device_monitor_job period alone. Never pass
        # pingInterval into add_job for JOB_ID — that restores legacy
        # scheduler-period coupling (avoid in dispatch mode).
        dispatcher_interval = get_monitor_dispatcher_interval_seconds()
        existing = scheduler.get_job(JOB_ID)
        existing_seconds = None
        if existing is not None and getattr(existing, "trigger", None) is not None:
            existing_seconds = getattr(existing.trigger, "interval", None)
            if existing_seconds is not None:
                existing_seconds = int(existing_seconds.total_seconds())

        logger.info(
            "Ping interval updated | mode=dispatch | pingInterval=%ss | "
            "dispatcher_interval unchanged=%ss | jobSeconds=%s",
            interval,
            dispatcher_interval,
            existing_seconds if existing_seconds is not None else dispatcher_interval,
        )

    # ISP job still follows pingInterval (ISP redesign is out of scope).
    _register_isp_monitor_job(interval)


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
    """
    Graceful shutdown: stop work, then release *our* lease only.

    Order matters — stop accepting/dispatching before deleting the Mongo lock
    so we never release while still running leader job bodies.
    """
    with _scheduler_init_lock:
        if not scheduler.running:
            return

        # 1–2) Stop accepting new scheduler / runtime work.
        try:
            from services.monitor_runtime import (  # noqa: PLC0415
                signal_monitor_runtime_leadership_lost,
                stop_monitor_runtime,
            )

            signal_monitor_runtime_leadership_lost()
            stop_monitor_runtime(wait=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Monitor runtime stop during shutdown failed | ts=%s | error=%s",
                format_utc(utc_now()),
                exc,
            )

        # 3) Stop APScheduler (no new job fires after this).
        try:
            scheduler.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "APScheduler shutdown failed | ts=%s | error=%s",
                format_utc(utc_now()),
                exc,
            )

        # 4) Release Mongo ownership only if this process owns the lease.
        try:
            release_scheduler_ownership()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Ownership release during shutdown failed | ts=%s | error=%s",
                format_utc(utc_now()),
                exc,
            )

        logger.info("Scheduler shutdown | ts=%s", format_utc(utc_now()))


atexit.register(stop_scheduler)
