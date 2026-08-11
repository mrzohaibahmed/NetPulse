import logging
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "monitor.log"


class _TimezoneAwareFormatter(logging.Formatter):
    """
    Log timestamps with an explicit UTC offset.

    Avoids ambiguous naive local wall times (e.g. Pakistan UTC+5 vs Mongo UTC).
    """

    converter = datetime.fromtimestamp  # type: ignore[assignment]

    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds")


def get_monitor_logger(name: str = "monitor") -> logging.Logger:
    """Return a logger that writes to logs/monitor.log and the console."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = _TimezoneAwareFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False

    return logger
