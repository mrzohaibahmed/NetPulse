"""Tests for thread-safe Nmap PortScanner usage (no shared singleton)."""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _HostStub(dict):
    def all_protocols(self):
        return []


def _scan_result(ip: str) -> dict:
    return {
        "scan": {
            ip: _HostStub({
                "addresses": {"ipv4": ip},
                "vendor": {},
                "hostnames": [],
                "osmatch": [],
            })
        }
    }


def test_create_scanner_returns_new_instance_each_call():
    import services.nmap_service as nmap_mod

    instances: list[MagicMock] = []

    def _factory(**_kwargs):
        mock = MagicMock(name=f"scanner-{len(instances)}")
        instances.append(mock)
        return mock

    with patch.object(nmap_mod.nmap_lib, "PortScanner", side_effect=_factory):
        first = nmap_mod._create_scanner()
        second = nmap_mod._create_scanner()

    assert first is not second
    assert len(instances) == 2
    assert id(first) != id(second)


def test_parallel_scans_receive_different_scanner_instances():
    import services.nmap_service as nmap_mod

    scanner_ids: list[int] = []
    lock = threading.Lock()

    def _factory(**_kwargs):
        scanner = MagicMock()
        scanner.scan.side_effect = lambda hosts, **kw: _scan_result(hosts)
        with lock:
            scanner_ids.append(id(scanner))
        return scanner

    ips = ["10.0.0.1", "10.0.0.2"]

    def _run_scan(ip: str) -> int:
        with patch.object(nmap_mod.nmap_lib, "PortScanner", side_effect=_factory):
            nmap_mod.scan_device_nmap(ip, force=True)
        return threading.get_ident()

    with ThreadPoolExecutor(max_workers=2) as executor:
        thread_ids = list(executor.map(_run_scan, ips))

    assert len(scanner_ids) == 2
    assert scanner_ids[0] != scanner_ids[1]
    assert len(set(thread_ids)) == 2


def test_no_module_level_shared_scanner():
    import services.nmap_service as nmap_mod

    assert not hasattr(nmap_mod, "_scanner")


def test_parallel_scans_complete_successfully():
    import services.nmap_service as nmap_mod

    def _factory(**_kwargs):
        scanner = MagicMock()
        scanner.scan.side_effect = lambda hosts, **kw: _scan_result(hosts)
        return scanner

    ips = ["10.0.0.10", "10.0.0.11", "10.0.0.12"]

    with patch.object(nmap_mod.nmap_lib, "PortScanner", side_effect=_factory):
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(nmap_mod.scan_device_nmap, ip, force=True)
                for ip in ips
            ]
            results = [future.result() for future in as_completed(futures)]

    assert len(results) == 3
    for info in results:
        assert "lastScan" in info
        assert "ports" in info


def test_scanner_debug_log_includes_thread_and_object_id():
    import services.nmap_service as nmap_mod

    with patch.object(nmap_mod.logger, "debug") as debug_log:
        with patch.object(nmap_mod.nmap_lib, "PortScanner", return_value=MagicMock()):
            scanner = nmap_mod._create_scanner()

    debug_log.assert_called_once()
    message, thread_id, scanner_id = debug_log.call_args[0]
    assert message == "[NMAP SCANNER] thread=%s scanner=%s"
    assert thread_id == threading.get_ident()
    assert scanner_id == id(scanner)
