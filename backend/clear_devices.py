"""
Deletes ALL documents from the `devices` collection.
Run this from inside the backend/ directory (same place as app.py)
so it can import config.database correctly.

Usage:
    python clear_devices.py
"""

from config.database import db, DATABASE_NAME

count_before = db.devices.count_documents({})

confirm = input(
    f"This will permanently delete {count_before} device(s) from "
    f"'{DATABASE_NAME}.devices'. Type 'yes' to continue: "
)

if confirm.strip().lower() == "yes":
    result = db.devices.delete_many({})
    print(f"Deleted {result.deleted_count} device(s).")
else:
    print("Cancelled. No changes made.")