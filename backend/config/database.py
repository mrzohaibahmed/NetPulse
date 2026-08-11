import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

from config.nmap_validation import validate_nmap_scan_profiles

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 30))

# ── Nmap Scanner configuration ─────────────────────────────────────────────────
# How often (seconds) the background Nmap scheduler runs. Default: 1 hour.
NMAP_SCAN_INTERVAL = int(os.getenv("NMAP_SCAN_INTERVAL", 3600))

# Deep / diagnostic Nmap profile (manual Scan Details).
# -A covers OS detection (-O), version detection (-sV), script scanning (-sC),
# and traceroute. -T4 is a fast timing template suitable for LAN scanning.
# Drop -O (inside -A) by switching to "-sV -T4" if not running as administrator.
# Deep / quick profiles are validated at startup (see config/nmap_validation.py).
NMAP_QUICK_ARGUMENTS, NMAP_ARGUMENTS = validate_nmap_scan_profiles(
    quick_arguments=os.getenv("NMAP_QUICK_ARGUMENTS", "-O -sV -T4 --top-ports 100"),
    deep_arguments=os.getenv("NMAP_ARGUMENTS", "-A -T4"),
)

# Max concurrent Nmap scans (ThreadPoolExecutor workers). Keep ≤ 10 on LAN.
MAX_SCAN_THREADS = int(os.getenv("MAX_SCAN_THREADS", 5))

# Per-host timeout passed to python-nmap (seconds). Nmap aborts the host scan
# after this duration to prevent hangs on unresponsive targets.
NMAP_TIMEOUT = int(os.getenv("NMAP_TIMEOUT", 300))

# Reuse recent Nmap results when networkInfo.lastScan is younger than this (seconds).
# Set to 0 to disable TTL caching. Default: 6 hours.
NMAP_CACHE_TTL = int(os.getenv("NMAP_CACHE_TTL", 21600))

# Absolute path to the nmap binary. Empty → python-nmap auto-detects from PATH.
# Windows users typically need: C:\Program Files (x86)\Nmap\nmap.exe
NMAP_PATH = os.getenv("NMAP_PATH", "") or None

# ── Interface Discovery (SSH) ──────────────────────────────────────────────────
# How often (seconds) the background interface discovery job runs. Default: 1 hour.
# Set to 0 to disable the scheduled job (manual API discovery still works).
INTERFACE_SCAN_INTERVAL = int(os.getenv("INTERFACE_SCAN_INTERVAL", 3600))

# Max concurrent SSH interface discovery sessions.
MAX_INTERFACE_THREADS = int(os.getenv("MAX_INTERFACE_THREADS", 5))

# ── Interface Statistics ───────────────────────────────────────────────────────
# How often (seconds) to poll interface counters. Default: 60 s.
# Set to 0 to disable the scheduled job (manual API collection still works).
INTERFACE_STATS_INTERVAL = int(os.getenv("INTERFACE_STATS_INTERVAL", 60))

# Max concurrent device polls for interface statistics.
MAX_INTERFACE_STATS_THREADS = int(os.getenv("MAX_INTERFACE_STATS_THREADS", 8))

# Bulk insert chunk size for append-only historical samples.
INTERFACE_STATS_BATCH_SIZE = int(os.getenv("INTERFACE_STATS_BATCH_SIZE", 500))

# ── Port -> Connected Device IP Resolution (MAC/ARP) ──────────────────────────
# Passive poll: MAC table on every switch, ARP table on whichever ones route.
# Frequent/lightweight — keep well below INTERFACE_SCAN_INTERVAL.
MAC_ARP_POLL_INTERVAL = int(os.getenv("MAC_ARP_POLL_INTERVAL", 90))
MAX_MAC_ARP_POLL_THREADS = int(os.getenv("MAX_MAC_ARP_POLL_THREADS", 5))
MAC_ARP_POLL_MAX_THREADS = MAX_MAC_ARP_POLL_THREADS

# Active sweep: reads each router's real subnets and pings unresolved hosts
# to force ARP for devices that have never spoken to their gateway. Slower
# and more invasive than the passive poll by design — set to 0 to disable.
ARP_ACTIVE_SWEEP_INTERVAL = int(os.getenv("ARP_ACTIVE_SWEEP_INTERVAL", 1800))
# Safety cap: max host addresses probed per connected subnet per sweep
# cycle, so a large subnet can't turn one cycle into a flood of pings.
ARP_ACTIVE_SWEEP_MAX_HOSTS = int(os.getenv("ARP_ACTIVE_SWEEP_MAX_HOSTS", 512))

MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")


if not MONGO_URI:
    raise ValueError("MONGO_URI not found in .env file")

if not DATABASE_NAME:
    raise ValueError("DATABASE_NAME not found in .env file")

try:
    client = MongoClient(MONGO_URI)

    # Verify MongoDB connection
    client.admin.command("ping")

    db = client[DATABASE_NAME]

    print("MongoDB Connected Successfully!")
    print(f"Database: {DATABASE_NAME}")

except Exception as e:
    print(f"MongoDB Connection Failed: {e}")
    raise
