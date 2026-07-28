"""
Drops the entire MongoDB database used by NetPulse (all collections).
Run from inside the backend/ directory (same place as app.py).

Usage:
    python clear_database.py
"""

from config.database import db, DATABASE_NAME, client

collections = db.list_collection_names()
counts = {name: db[name].count_documents({}) for name in collections}
total_docs = sum(counts.values())

print(f"Database: {DATABASE_NAME}")
if not collections:
    print("No collections found. Nothing to delete.")
    raise SystemExit(0)

for name, count in sorted(counts.items()):
    print(f"  - {name}: {count} document(s)")

confirm = input(
    f"\nThis will PERMANENTLY drop database '{DATABASE_NAME}' "
    f"({total_docs} document(s) across {len(collections)} collection(s)). "
    f"Type 'yes' to continue: "
)

if confirm.strip().lower() == "yes":
    client.drop_database(DATABASE_NAME)
    print(f"Dropped database '{DATABASE_NAME}'.")
    print("Restart the backend (python app.py) to recreate default admin/viewer users.")
else:
    print("Cancelled. No changes made.")
