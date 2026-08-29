"""Bootstrap Flask app for route tests when config.database was stubbed."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def _clear_poisoned_database_stub() -> None:
    """Remove MagicMock config.database stubs left by other test modules."""
    mod = sys.modules.get("config.database")
    if mod is None:
        return
    db = getattr(mod, "db", None)
    if isinstance(mod, MagicMock) or isinstance(db, MagicMock):
        for name in ("app", "config.database"):
            sys.modules.pop(name, None)


def load_test_app():
    """Import ``app`` after clearing poisoned database stubs from other tests."""
    _clear_poisoned_database_stub()
    from app import app as flask_app

    return flask_app
