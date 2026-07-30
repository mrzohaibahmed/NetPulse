#!/usr/bin/env python3
"""
One-time migration: encrypt plaintext SSH/SMTP secrets already stored in MongoDB.

DO NOT run automatically. Review, backup Mongo first, then execute manually:

    cd backend
    python migrate_encrypt_secrets.py           # dry-run (default)
    python migrate_encrypt_secrets.py --apply # write changes

Requires SECRETS_ENCRYPTION_KEY in backend/.env (Fernet key).
FLASK_DEBUG passthrough mode is refused — a real key is mandatory for migration.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Force a real key even if FLASK_DEBUG=true — migration must produce ciphertext.
if not (os.getenv("SECRETS_ENCRYPTION_KEY") or "").strip():
    print(
        "ERROR: SECRETS_ENCRYPTION_KEY must be set in .env before running this migration.",
        file=sys.stderr,
    )
    sys.exit(1)

from config.database import db  # noqa: E402
from utils.secret_crypto import (  # noqa: E402
    CIPHER_PREFIX,
    encrypt_secret,
    is_encrypted,
)


def _encrypt_field(value: str | None) -> tuple[str | None, bool]:
    """Return (new_value, changed)."""
    if value is None or value == "":
        return value, False
    if is_encrypted(value):
        return value, False
    return encrypt_secret(str(value)), True


def migrate_smtp(*, apply: bool) -> int:
    doc = db.settings.find_one({"_id": "global"})
    if not doc:
        print("settings: no global document — skip")
        return 0
    smtp = dict(doc.get("smtp") or {})
    new_pw, changed = _encrypt_field(smtp.get("password"))
    if not changed:
        print("settings.smtp.password: already encrypted or empty — skip")
        return 0
    print(f"settings.smtp.password: would encrypt ({CIPHER_PREFIX}…)")
    if apply:
        smtp["password"] = new_pw
        db.settings.update_one({"_id": "global"}, {"$set": {"smtp": smtp}})
        print("settings.smtp.password: UPDATED")
    return 1


def migrate_device_credentials(*, apply: bool) -> int:
    updated = 0
    cursor = db.devices.find(
        {
            "$or": [
                {"credentials.sshPassword": {"$exists": True, "$nin": [None, ""]}},
                {"credentials.sshSecret": {"$exists": True, "$nin": [None, ""]}},
            ]
        }
    )
    for device in cursor:
        creds = dict(device.get("credentials") or {})
        changes: dict = {}
        for field in ("sshPassword", "sshSecret"):
            new_val, changed = _encrypt_field(creds.get(field))
            if changed:
                changes[field] = new_val
        if not changes:
            continue
        print(
            f"device {device.get('_id')} ({device.get('ipAddress')}): "
            f"would encrypt {', '.join(changes)}"
        )
        if apply:
            for field, val in changes.items():
                creds[field] = val
            db.devices.update_one(
                {"_id": device["_id"]},
                {"$set": {"credentials": creds}},
            )
            print(f"device {device.get('_id')}: UPDATED")
        updated += 1
    if updated == 0:
        print("devices: no plaintext SSH secrets found — skip")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist encryption (default is dry-run only)",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== migrate_encrypt_secrets ({mode}) ===")
    smtp_n = migrate_smtp(apply=args.apply)
    device_n = migrate_device_credentials(apply=args.apply)
    print(f"Done. smtp_changes={smtp_n} device_changes={device_n}")
    if not args.apply:
        print("Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
