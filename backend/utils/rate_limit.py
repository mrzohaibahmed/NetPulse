"""Lightweight in-process rate limiting for sensitive API endpoints."""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps
from typing import Callable

from flask import g, jsonify, request


class RateLimiter:
    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max(1, int(max_calls))
        self.window_seconds = max(1, int(window_seconds))
        self._calls: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            cutoff = now - self.window_seconds
            recent = [t for t in self._calls[key] if t > cutoff]
            if len(recent) >= self.max_calls:
                self._calls[key] = recent
                return False
            recent.append(now)
            self._calls[key] = recent
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._calls.clear()
            else:
                self._calls.pop(key, None)


def _rate_limit_key(prefix: str) -> str:
    user = getattr(g, "user", None) or {}
    username = user.get("username") or "anonymous"
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    if client_ip and "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    return f"{prefix}:{username}:{client_ip}"


def rate_limit(
    limiter: RateLimiter,
    *,
    prefix: str = "default",
    message: str = "Rate limit exceeded. Try again later.",
) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not limiter.is_allowed(_rate_limit_key(prefix)):
                return jsonify({"success": False, "message": message}), 429
            return fn(*args, **kwargs)

        return wrapper

    return decorator


EMERGENCY_SHUTDOWN_LIMITER = RateLimiter(
    max_calls=int(os.getenv("EMERGENCY_SHUTDOWN_RATE_LIMIT", "5")),
    window_seconds=int(os.getenv("EMERGENCY_SHUTDOWN_RATE_WINDOW_SECONDS", "60")),
)
