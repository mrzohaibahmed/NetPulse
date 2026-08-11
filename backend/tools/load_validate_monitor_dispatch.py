"""
Phase 10 — controlled load/capacity validation for dispatch-mode monitoring.

Loads the bytecode harness from ``tools/__pycache__/`` (source was not checked
in). Ensures ``backend/`` is on ``sys.path`` before import.

SAFETY
------
- Does NOT change production settings or .env.
- Does NOT ping real network devices.
- Uses an in-memory device store + mocked scan latency (no real ICMP).
- Device claim/update I/O is patched to the in-memory store.
  Note: importing monitor modules may still open a Mongo client for settings
  side-effects; the harness never writes production device documents.

Run:
  python tools/load_validate_monitor_dispatch.py
  python tools/load_validate_monitor_dispatch.py --fleets 1000
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND_ROOT = _HERE.parent
_PYC = _HERE / "__pycache__" / "load_validate_monitor_dispatch.cpython-312.pyc"
_MODULE = "load_validate_monitor_dispatch_impl"


def _load_impl():
    if not _PYC.is_file():
        raise FileNotFoundError(
            f"Missing harness bytecode: {_PYC}\n"
            "Restore tools/__pycache__/load_validate_monitor_dispatch.cpython-312.pyc"
        )
    # Harness bytecode computes _BACKEND_ROOT from its __file__ (the .pyc path),
    # which wrongly resolves to tools/. Pre-seed backend on sys.path.
    backend = str(_BACKEND_ROOT)
    if backend not in sys.path:
        sys.path.insert(0, backend)

    spec = importlib.util.spec_from_file_location(_MODULE, _PYC)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load harness from {_PYC}")
    mod = importlib.util.module_from_spec(spec)
    # dataclasses require the module to be registered before class creation.
    sys.modules[_MODULE] = mod
    spec.loader.exec_module(mod)
    # Correct the module's path constant used by any late imports.
    mod._BACKEND_ROOT = _BACKEND_ROOT
    return mod


def main(argv: list[str] | None = None) -> int:
    mod = _load_impl()
    return int(mod.main(argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
