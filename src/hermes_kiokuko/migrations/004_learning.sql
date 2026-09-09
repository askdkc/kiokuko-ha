-- Progress contains only validated proposals, never raw traces or model responses.
CREATE TABLE experience_windows (
    run_id TEXT NOT NULL REFERENCES monitor_runs(id),
    window_index INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    proposals_json TEXT NOT NULL,
    PRIMARY KEY(run_id, window_index)
);
CREATE TABLE experience_leases (
    run_id TEXT PRIMARY KEY REFERENCES monitor_runs(id),
    token TEXT NOT NULL
);
CREATE TABLE experience_features (
    entry_id TEXT PRIMARY KEY REFERENCES memory_entries(id) ON DELETE CASCADE,
    family_hash TEXT NOT NULL,
    features_json TEXT NOT NULL
);
CREATE INDEX experience_family ON experience_features(family_hash,entry_id);
CREATE TABLE experience_relations (
    earlier_id TEXT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
    later_id TEXT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK(relation IN ('similar','recovery')),
    PRIMARY KEY(earlier_id,later_id,relation)
);
CREATE TABLE learning_jobs (
    family_hash TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('pending','running','done','failed','blocked')),
    lease_token TEXT,
    input_hash TEXT,
    error_code TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE lesson_families (
    family_hash TEXT PRIMARY KEY,
    entry_id TEXT UNIQUE REFERENCES memory_entries(id) ON DELETE SET NULL,
    detached INTEGER NOT NULL DEFAULT 0 CHECK(detached IN (0,1))
);
CREATE TABLE lessons (
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    lifecycle TEXT NOT NULL CHECK(lifecycle IN ('candidate','active','contested','retired')),
    structure_json TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY(entry_id,entry_revision),
    FOREIGN KEY(entry_id,entry_revision) REFERENCES memory_revisions(entry_id,revision) ON DELETE CASCADE
);
CREATE TABLE lesson_sources (
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES memory_entries(id),
    source_revision INTEGER NOT NULL,
    relation TEXT NOT NULL CHECK(relation IN ('support','counterexample','excluded')),
    PRIMARY KEY(entry_id,entry_revision,source_id),
    FOREIGN KEY(entry_id,entry_revision) REFERENCES lessons(entry_id,entry_revision) ON DELETE CASCADE
);
CREATE INDEX lesson_source_lookup ON lesson_sources(source_id,entry_id);
CREATE TABLE learning_receipts (
    input_hash TEXT PRIMARY KEY,
    family_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Content-free bounds survive completion so skipped ranges remain inspectable.
CREATE TABLE experience_coverage (
    run_id TEXT PRIMARY KEY REFERENCES monitor_runs(id),
    window_count INTEGER NOT NULL,
    excluded_json TEXT NOT NULL
);
