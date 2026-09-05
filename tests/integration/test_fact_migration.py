import sqlite3

import pytest

from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.models import ExplicitCommand
from hermes_kiokuko.store import Store, schema_digest


def make_v1(service):
    home = service.store.home
    service.store.close()
    with sqlite3.connect(service.store.path) as db:
        for table in ("verified_facts", "snapshot_roots", "fact_receipts", "compaction_receipts"):
            db.execute(f"DROP TABLE {table}")
        db.execute("DELETE FROM schema_migrations WHERE version=2")
        db.execute("PRAGMA user_version=1")
        db.execute("UPDATE store_metadata SET value=? WHERE key='schema_hash'", (schema_digest(db),))
    return home


def test_v1_upgrade_preserves_memory_and_key(service, make_turn):
    snap = make_turn()
    entry = service.explicit(snap, ExplicitCommand("remember", "keep this memory", "principal"))
    key = service.store.key
    home = make_v1(service)
    upgraded = Store(home)
    try:
        assert upgraded.key == key
        with upgraded.transaction() as db:
            assert db.execute("SELECT claim FROM memory_entries WHERE id=?", (entry["entry_id"],)).fetchone()[0] == "keep this memory"
            assert db.execute("PRAGMA user_version").fetchone()[0] == 2
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        upgraded.close()


def test_bad_v1_checksum_never_migrates(service):
    home = make_v1(service)
    with sqlite3.connect(service.store.path) as db:
        db.execute("UPDATE schema_migrations SET checksum='bad'")
    with pytest.raises(KiokukoError, match="CHECKSUM_MISMATCH"):
        Store(home)
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='verified_facts'").fetchone()
