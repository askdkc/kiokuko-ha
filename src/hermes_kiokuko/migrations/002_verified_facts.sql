CREATE TABLE snapshot_roots (
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    canonical_root TEXT NOT NULL,
    identity_hash TEXT NOT NULL,
    PRIMARY KEY (profile_key,session_id,turn_id),
    FOREIGN KEY (profile_key,session_id,turn_id)
        REFERENCES turn_snapshots(profile_key,session_id,turn_id)
);
CREATE TRIGGER immutable_snapshot_root BEFORE UPDATE ON snapshot_roots
BEGIN SELECT RAISE(ABORT, 'immutable root'); END;

CREATE TABLE verified_facts (
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    predicate_json TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (entry_id,entry_revision),
    FOREIGN KEY (entry_id,entry_revision) REFERENCES memory_revisions(entry_id,revision) ON DELETE CASCADE,
    FOREIGN KEY (profile_key,session_id,turn_id) REFERENCES snapshot_roots(profile_key,session_id,turn_id)
);

-- Content-free receipt survives purge, preventing replay from resurrecting an entry.
CREATE TABLE fact_receipts (
    receipt_hash TEXT PRIMARY KEY,
    entry_id TEXT REFERENCES memory_entries(id) ON DELETE SET NULL
);
CREATE TABLE compaction_receipts (
    receipt_hash TEXT PRIMARY KEY,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
