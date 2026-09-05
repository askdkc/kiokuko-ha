"""Human CLI operations, projection repair, logical purge, backup and restore."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile

from .errors import KiokukoError
from .filesystem import atomic_write, checked_file, file_lock, private_directory, sync_directory
from .models import canonical, digest, now
from .store import CHECKSUM, INITIAL_CHECKSUM, SCHEMA_VERSION, Store

PURGE_SCOPE = ("Logical deletion from the live Kiokuko database only. Minimal tombstones, "
               "retry receipts and non-content metadata remain. Hermes history, existing "
               "backups/exports and physical disk erasure are outside this operation.")


def entry_review(service, entry_id):
    with service.transaction() as db:
        entry = service._entry(db, entry_id, admin=True)
        return entry, digest(canonical(entry))


def share(service, entry_id, expected_revision, review_digest, scope, workspace_id=None):
    with service.transaction(write=True) as db:
        entry = service._entry(db, entry_id, admin=True)
        if digest(canonical(entry)) != review_digest or entry["current_revision"] != expected_revision:
            raise KiokukoError("APPROVAL_CHANGED")
        entry = service._change(db, entry, "share", scope=scope, workspace=workspace_id, approved=True)
        return {"entry_id": entry["id"], "revision": entry["current_revision"]}


def purge(service, entry_id, review_digest):
    with service.transaction(write=True) as db:
        entry = service._entry(db, entry_id, admin=True)
        if digest(canonical(entry)) != review_digest:
            raise KiokukoError("APPROVAL_CHANGED")
        db.execute("UPDATE jobs SET state='blocked',lease_id=NULL,lease_expires_at=NULL WHERE entry_id=? OR candidate_id IN (SELECT id FROM memory_candidates WHERE target_entry_id=? OR promoted_entry_id=?)", (entry_id, entry_id, entry_id))
        service._invalidate(db, entry_id, entry["current_revision"], "purged")
        # Clear body-dependent digests before the ownership cascade removes the join rows.
        db.execute("UPDATE retrieval_deliveries SET rendered_sha256=NULL WHERE id IN (SELECT delivery_id FROM retrieval_delivery_entries WHERE entry_id=?)", (entry_id,))
        db.execute("UPDATE explicit_operation_receipts SET state='purged',entry_id=NULL,entry_revision=NULL WHERE entry_id=?", (entry_id,))
        db.execute("DELETE FROM memory_entries WHERE id=?", (entry_id,))
        for table in ("memory_revisions", "memory_evidence", "memory_search_documents", "memory_ngrams",
                      "memory_vectors", "retrieval_delivery_entries", "retrieval_feedback", "jobs", "verified_facts"):
            if db.execute(f"SELECT 1 FROM {table} WHERE entry_id=? LIMIT 1", (entry_id,)).fetchone():
                raise KiokukoError("PURGE_INCOMPLETE")
        if db.execute("SELECT 1 FROM memory_candidates WHERE target_entry_id=? OR promoted_entry_id=?", (entry_id, entry_id)).fetchone():
            raise KiokukoError("PURGE_INCOMPLETE")
        if dict(db.execute("SELECT key,value FROM store_metadata"))["fts"] == "1" and db.execute("SELECT 1 FROM memory_fts WHERE entry_id=?", (entry_id,)).fetchone():
            raise KiokukoError("PURGE_INCOMPLETE")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise KiokukoError("PURGE_INCOMPLETE")
    return {"entry_id": entry_id, "purged": True, "scope": PURGE_SCOPE}


def purge_candidate(service, candidate_id, review_digest):
    with service.transaction(write=True) as db:
        candidate = db.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
        if candidate is None or candidate["promoted_entry_id"]:
            raise KiokukoError("PURGE_ENTRY_INSTEAD")
        target = service._entry(db, candidate["target_entry_id"], admin=True) if candidate["target_entry_id"] else None
        review = {"candidate": dict(candidate), "target": target,
                  "evidence": [dict(r) for r in db.execute("SELECT * FROM memory_evidence WHERE candidate_id=? ORDER BY id", (candidate_id,))]}
        if digest(canonical(review)) != review_digest:
            raise KiokukoError("APPROVAL_CHANGED")
        db.execute("DELETE FROM memory_candidates WHERE id=?", (candidate_id,))
    return {"candidate_id": candidate_id, "purged": True, "scope": PURGE_SCOPE}


def verify(service):
    with service.transaction() as db:
        integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
        foreign = db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != ["ok"] or foreign:
            raise KiokukoError("DATABASE_INTEGRITY_FAILED")
        return {"ok": True, "schema": SCHEMA_VERSION, "checksum": CHECKSUM}


def reindex(service):
    with service.transaction(write=True) as db:
        db.execute("DELETE FROM memory_search_documents")
        db.execute("DELETE FROM memory_ngrams")
        db.execute("DELETE FROM memory_vectors")
        if dict(db.execute("SELECT key,value FROM store_metadata"))["fts"] == "1":
            db.execute("DELETE FROM memory_fts")
        entries = db.execute("SELECT * FROM memory_entries WHERE state='active'").fetchall()
        for entry in entries:
            service._project(db, dict(entry))
    return {"reindexed": len(entries)}


def backup(service, destination: Path):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise KiokukoError("DESTINATION_EXISTS")
    # The backup is private; its existing parent belongs to the caller.
    # Do not chmod an arbitrary shared directory such as /tmp or a project root.
    if destination.parent.is_symlink():
        raise KiokukoError("UNSAFE_PATH")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(prefix=".kiokuko-backup-", dir=destination.parent))
    try:
        target = partial / "kiokuko.db"
        atomic_write(target, b"")
        with service.transaction() as source:
            out = sqlite3.connect(target)
            try:
                source.backup(out)
                if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise KiokukoError("DATABASE_INTEGRITY_FAILED")
            finally:
                out.close()
        atomic_write(partial / "identity.key", service.store.key)
        with target.open("rb") as stream:
            os.fsync(stream.fileno())
        manifest = {"schema": SCHEMA_VERSION, "checksum": CHECKSUM, "profile_key": service.store.profile_key,
                    "db_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "key_sha256": hashlib.sha256(service.store.key).hexdigest()}
        atomic_write(partial / "manifest.json", canonical(manifest).encode())
        sync_directory(partial)
        os.rename(partial, destination)
        sync_directory(destination.parent)
    finally:
        if partial.exists():
            shutil.rmtree(partial)
    return {"backup": str(destination)}


def restore(home: Path, source: Path):
    """Same-profile restore only; callers close their own Store before entering."""
    home, source = Path(home).resolve(), Path(source).resolve()
    directory = home / "kiokuko"
    if not directory.is_dir() or directory.is_symlink():
        raise KiokukoError("STORE_NOT_INITIALIZED")
    for name in ("kiokuko.db", "identity.key", "manifest.json"):
        checked_file(source / name)
    try:
        manifest = json.loads((source / "manifest.json").read_text())
        key = (source / "identity.key").read_bytes()
        data = (source / "kiokuko.db").read_bytes()
    except (OSError, ValueError):
        raise KiokukoError("INVALID_BACKUP") from None
    if (manifest.get("schema"), manifest.get("checksum")) not in {(1, INITIAL_CHECKSUM), (SCHEMA_VERSION, CHECKSUM)} or \
            manifest.get("key_sha256") != hashlib.sha256(key).hexdigest() or \
            manifest.get("db_sha256") != hashlib.sha256(data).hexdigest():
        raise KiokukoError("INVALID_BACKUP")
    with file_lock(directory / "holders.lock", exclusive=True, timeout=0):
        checked_file(directory / "identity.key")
        if key != (directory / "identity.key").read_bytes():
            raise KiokukoError("PROFILE_IDENTITY_MISMATCH")
        # Verify a private copy using the same schema guard; never mutate the backup.
        temporary = Path(tempfile.mkdtemp(prefix=".restore-", dir=directory))
        try:
            private_directory(temporary / "kiokuko")
            atomic_write(temporary / "kiokuko" / "kiokuko.db", data)
            atomic_write(temporary / "kiokuko" / "identity.key", key)
            candidate = Store(temporary)
            try:
                if manifest.get("profile_key") != candidate.profile_key:
                    raise KiokukoError("PROFILE_IDENTITY_MISMATCH")
                with candidate.transaction(write=True) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                        raise KiokukoError("INVALID_BACKUP")
                    db.execute("UPDATE jobs SET state='blocked',lease_id=NULL,lease_expires_at=NULL WHERE state='leased'")
                    db.execute("UPDATE store_metadata SET value=? WHERE key='db_id'", (secrets.token_hex(24),))
                    # Guard expects the old identity until this deliberate replacement completes.
                    del candidate.db_id
                compact = sqlite3.connect(candidate.path)
                try:
                    compact.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    compact.execute("PRAGMA journal_mode=DELETE")
                finally:
                    compact.close()
            finally:
                candidate.close()
            live = directory / "kiokuko.db"
            checked_file(live)
            # Do not detach old WAL contents from their database. Refuse nonempty WAL;
            # operator must close/checkpoint the live database first.
            wal = Path(str(live) + "-wal")
            if wal.exists() and wal.stat().st_size:
                raise KiokukoError("LIVE_WAL_PRESENT")
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(live) + suffix)
                if sidecar.is_symlink():
                    raise KiokukoError("UNSAFE_PATH")
                sidecar.unlink(missing_ok=True)
            atomic_write(live, (temporary / "kiokuko" / "kiokuko.db").read_bytes())
        finally:
            shutil.rmtree(temporary)
    return {"restored": True}
