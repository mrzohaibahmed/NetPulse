"""
Delete all pingHistory documents from NetPulse MongoDB.

Usage (from backend/):
    python delete_ping_history.py

Connects using the same MongoDB configuration as the NetPulse application
(config/database.py — MONGO_URI, DATABASE_NAME, mongo_config pool options).

WARNING:
    Permanently deletes all pingHistory records and resets maintained global
    response-time statistics (ping_statistics.global_response_time).
    Does not drop collections, remove indexes, or modify other data.
"""

from __future__ import annotations

import sys

PING_HISTORY_COLLECTION = "pingHistory"
CONFIRMATION_PHRASE = "DELETE-PING-HISTORY"


def _load_database():
    """Import NetPulse MongoDB configuration (same path as the application)."""
    try:
        from config.database import DATABASE_NAME, db

        return db, DATABASE_NAME
    except ValueError as exc:
        print(f"[ERROR] Missing configuration: {exc}")
        print("Ensure backend/.env defines MONGO_URI and DATABASE_NAME.")
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"[ERROR] MongoDB connection failed: {exc}")
        raise SystemExit(1) from exc


def main() -> int:
    db, database_name = _load_database()

    collection_names = db.list_collection_names()
    if PING_HISTORY_COLLECTION not in collection_names:
        print(f"[INFO] Collection '{PING_HISTORY_COLLECTION}' does not exist yet.")
        ping_history = db[PING_HISTORY_COLLECTION]
        document_count = 0
        index_count = 0
    else:
        ping_history = db[PING_HISTORY_COLLECTION]
        try:
            document_count = ping_history.count_documents({})
        except Exception as exc:
            print(
                f"[ERROR] Failed to count '{PING_HISTORY_COLLECTION}' documents: {exc}"
            )
            return 1

        try:
            index_count = len(list(ping_history.list_indexes()))
        except Exception as exc:
            print(
                f"[ERROR] Failed to read '{PING_HISTORY_COLLECTION}' indexes: {exc}"
            )
            return 1

    print("=" * 60)
    print("NetPulse - Delete Ping History")
    print("=" * 60)
    print()
    print(f"Database:   {database_name}")
    print(f"Collection: {PING_HISTORY_COLLECTION}")
    print(f"Documents:  {document_count:,}")
    if index_count:
        print(f"Indexes:    {index_count} (preserved - collection is not dropped)")
    print()

    if document_count == 0:
        print("[INFO] pingHistory is already empty. No deletion performed.")
        return 0

    print("[WARNING] This will permanently delete ALL pingHistory records.")
    print(
        "Global response-time statistics will be reset to zero "
        "(ping_statistics.global_response_time)."
    )
    print("This operation cannot be undone.")
    print()

    confirmation = input(f"Type {CONFIRMATION_PHRASE} to continue: ").strip()
    if confirmation != CONFIRMATION_PHRASE:
        print()
        print("[CANCELLED] No data was deleted.")
        return 0

    print()
    print(f"[INFO] Deleting {PING_HISTORY_COLLECTION} documents...")

    try:
        result = ping_history.delete_many({})
    except Exception as exc:
        print(f"[ERROR] Deletion failed: {exc}")
        return 1

    try:
        remaining = ping_history.count_documents({})
    except Exception as exc:
        print(
            f"[ERROR] Deletion may have completed, but verification failed: {exc}"
        )
        return 1

    if remaining != 0:
        print(
            f"[ERROR] Deletion incomplete: {remaining:,} document(s) remain "
            f"in '{PING_HISTORY_COLLECTION}'."
        )
        return 1

    print()
    print("[INFO] Resetting global ping response-time statistics...")

    try:
        from services.ping_response_stats import rebuild_ping_response_stats_from_history

        stats_summary = rebuild_ping_response_stats_from_history()
    except Exception as exc:
        print(
            "[ERROR] pingHistory was deleted, but statistics reset failed: "
            f"{exc}"
        )
        return 1

    print()
    print("=" * 60)
    print("Deletion completed")
    print("=" * 60)
    print(f"Deleted:   {result.deleted_count:,} documents")
    print(f"Remaining: {remaining:,} documents")
    print()
    print("Statistics reset (ping_statistics.global_response_time):")
    print(f"  responseTimeSum:   {stats_summary['responseTimeSum']}")
    print(f"  responseTimeCount: {stats_summary['responseTimeCount']}")
    print(f"  initialized:       {stats_summary['initialized']}")
    if index_count:
        print()
        print(
            f"Indexes on '{PING_HISTORY_COLLECTION}' remain intact "
            f"({index_count} index definition(s))."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
