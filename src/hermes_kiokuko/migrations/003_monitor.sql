CREATE TABLE monitor_runs (
    id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    binding_hash TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('recording','complete','incomplete','missing','purged')),
    events_hash TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    bytes INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0,
    api_requests INTEGER NOT NULL DEFAULT 0,
    error_code TEXT
);
CREATE TABLE experience_jobs (
    run_id TEXT PRIMARY KEY REFERENCES monitor_runs(id),
    state TEXT NOT NULL CHECK(state IN ('pending','running','done','failed','blocked')),
    error_code TEXT,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE experiences (
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    structure_json TEXT NOT NULL,
    PRIMARY KEY(entry_id,entry_revision),
    FOREIGN KEY(entry_id,entry_revision) REFERENCES memory_revisions(entry_id,revision) ON DELETE CASCADE
);
CREATE TABLE experience_sources (
    entry_id TEXT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES monitor_runs(id),
    evidence_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(entry_id,run_id)
);
-- Content-free hashes survive deletion and prevent replay resurrection.
CREATE TABLE experience_receipts (
    receipt_hash TEXT PRIMARY KEY,
    entry_id TEXT REFERENCES memory_entries(id) ON DELETE SET NULL
);
