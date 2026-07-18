import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 30))

# ── Nmap Scanner configuration ─────────────────────────────────────────────────
# How often (seconds) the background Nmap scheduler runs. Default: 1 hour.
NMAP_SCAN_INTERVAL = int(os.getenv("NMAP_SCAN_INTERVAL", 3600))

# CLI flags passed to every nmap invocation.
# -A covers OS detection (-O), version detection (-sV), script scanning (-sC),
# and traceroute. -T4 is a fast timing template suitable for LAN scanning.
# Drop -O (inside -A) by switching to "-sV -T4" if not running as administrator.
NMAP_ARGUMENTS = os.getenv("NMAP_ARGUMENTS", "-A -T4")

# Max concurrent Nmap scans (ThreadPoolExecutor workers). Keep ≤ 10 on LAN.
MAX_SCAN_THREADS = int(os.getenv("MAX_SCAN_THREADS", 5))

# Per-host timeout passed to python-nmap (seconds). Nmap aborts the host scan
# after this duration to prevent hangs on unresponsive targets.
NMAP_TIMEOUT = int(os.getenv("NMAP_TIMEOUT", 300))

# Absolute path to the nmap binary. Empty → python-nmap auto-detects from PATH.
# Windows users typically need: C:\Program Files (x86)\Nmap\nmap.exe
NMAP_PATH = os.getenv("NMAP_PATH", "") or None

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
