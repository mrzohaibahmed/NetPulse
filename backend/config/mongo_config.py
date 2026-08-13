"""
MongoDB client options for production deployments.

Conservative defaults for a single backend + scheduler process serving
~500 devices and ~40 switches. All values are overridable via environment.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse


def _int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def build_mongo_client_kwargs() -> dict[str, Any]:
    """
    PyMongo ``MongoClient`` keyword arguments.

    Defaults rationale (single-process NetPulse):
    - maxPoolSize 50: headroom for API + scheduler + storm workers without
      opening hundreds of connections.
    - minPoolSize 0: do not hold idle connections on small hosts.
    - waitQueueTimeoutMS 10000: fail fast when pool is saturated instead of
      hanging request threads indefinitely.
    - serverSelectionTimeoutMS 5000: detect Mongo outage quickly for health.
    - connectTimeoutMS / socketTimeoutMS 20000: tolerate brief network blips
      without matching worst-case SSH timeouts.
    """
    return {
        "maxPoolSize": _int_env("MONGO_MAX_POOL_SIZE", 50, minimum=10),
        "minPoolSize": _int_env("MONGO_MIN_POOL_SIZE", 0, minimum=0),
        "maxIdleTimeMS": _int_env("MONGO_MAX_IDLE_TIME_MS", 60_000, minimum=0),
        "waitQueueTimeoutMS": _int_env("MONGO_WAIT_QUEUE_TIMEOUT_MS", 10_000, minimum=1000),
        "serverSelectionTimeoutMS": _int_env(
            "MONGO_SERVER_SELECTION_TIMEOUT_MS", 5000, minimum=1000
        ),
        "connectTimeoutMS": _int_env("MONGO_CONNECT_TIMEOUT_MS", 20_000, minimum=1000),
        "socketTimeoutMS": _int_env("MONGO_SOCKET_TIMEOUT_MS", 20_000, minimum=1000),
        "retryWrites": _bool_env("MONGO_RETRY_WRITES", True),
        "retryReads": _bool_env("MONGO_RETRY_READS", True),
    }


def safe_mongo_log_summary(uri: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Log-safe Mongo configuration (never includes credentials)."""
    parsed = urlparse(uri or "")
    host = parsed.hostname or "unknown"
    port = parsed.port or 27017
    return {
        "host": host,
        "port": port,
        "maxPoolSize": kwargs.get("maxPoolSize"),
        "minPoolSize": kwargs.get("minPoolSize"),
        "waitQueueTimeoutMS": kwargs.get("waitQueueTimeoutMS"),
        "serverSelectionTimeoutMS": kwargs.get("serverSelectionTimeoutMS"),
        "connectTimeoutMS": kwargs.get("connectTimeoutMS"),
        "socketTimeoutMS": kwargs.get("socketTimeoutMS"),
        "retryWrites": kwargs.get("retryWrites"),
        "retryReads": kwargs.get("retryReads"),
    }
