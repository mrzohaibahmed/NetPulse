import atexit

from apscheduler.schedulers.background import BackgroundScheduler

from config.database import NMAP_SCAN_INTERVAL
from services.monitor_service import monitor_all_devices
from services.settings_service import get_settings
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("scheduler")

# Single shared scheduler instance.
# Both the ping job and the nmap job run on this same scheduler so they share
# one background thread pool and one lifecycle (start / shutdown).
scheduler = BackgroundScheduler()

# Job IDs — kept as named constants to make reschedule helpers readable.
JOB_ID = "device_monitor_job"
NMAP_JOB_ID = "nmap_scan_job"


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
        # Deferred import: python-nmap is optional at startup.
        from services.nmap_service import scan_all_online_devices  # noqa: PLC0415

        interval = max(int(NMAP_SCAN_INTERVAL), 60)  # enforce minimum 60 s

        scheduler.add_job(
            func=scan_all_online_devices,
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
    scheduler.add_job(
        func=monitor_all_devices,
        trigger="interval",
        seconds=interval,
        id=JOB_ID,
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started | ping_interval=%ss", interval)

    # Job 2: Nmap metadata scan (independent; registered after scheduler.start).
    _start_nmap_job()


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
        from services.nmap_service import scan_all_online_devices  # noqa: PLC0415

        scheduler.add_job(
            func=scan_all_online_devices,
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

    scheduler.shutdown(wait=False)
    logger.info("Scheduler shutdown")


atexit.register(stop_scheduler)

