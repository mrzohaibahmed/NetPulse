"""
Pytest bootstrap — keep APScheduler off during the test suite.

Must run before ``app`` / ``scheduler`` imports. Forces API-only role so
importing Flask blueprints does not start background jobs (avoids
interpreter-shutdown / executor noise and dual-leader races in CI).
"""

from __future__ import annotations

import os

# Force (do not setdefault) so local .env / shell cannot enable the scheduler
# under pytest. Individual tests can still patch deployment helpers.
os.environ["NETPULSE_ROLE"] = "api"
os.environ["NETPULSE_ENABLE_SCHEDULER"] = "false"
os.environ.setdefault("NETPULSE_ENV", "development")
os.environ.setdefault(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5000",
)
# Strong test JWT so importing utils.auth succeeds when FLASK_DEBUG is false.
os.environ.setdefault(
    "JWT_SECRET",
    "netpulse-test-jwt-secret-do-not-use-in-production-32c+",
)
os.environ.setdefault("FLASK_DEBUG", "true")
