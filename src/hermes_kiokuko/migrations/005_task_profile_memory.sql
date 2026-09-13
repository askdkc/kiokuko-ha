CREATE INDEX memory_recall_pinned ON memory_entries(scope_type,principal_id,conversation_id,workspace_id,id)
    WHERE state='active' AND pinned=1;
CREATE TABLE task_profiles (
    id TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    session_generation INTEGER NOT NULL,
    principal_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    excerpt TEXT NOT NULL CHECK(length(excerpt) BETWEEN 1 AND 600),
    targets_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(profile_key,session_id,turn_id),
    FOREIGN KEY(profile_key,session_id,turn_id)
        REFERENCES turn_snapshots(profile_key,session_id,turn_id)
);
CREATE INDEX task_profile_scope ON task_profiles(profile_key,principal_id,workspace_id,created_at,id);
CREATE INDEX task_profile_expiry ON task_profiles(expires_at,id);
CREATE TABLE task_profile_receipts (
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    PRIMARY KEY(profile_key,session_id,turn_id),
    FOREIGN KEY(profile_key,session_id,turn_id)
        REFERENCES turn_snapshots(profile_key,session_id,turn_id)
);
CREATE TABLE task_profile_signals (
    profile_id TEXT NOT NULL REFERENCES task_profiles(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('exact','ngram')),
    value TEXT NOT NULL,
    PRIMARY KEY(kind,value,profile_id)
);
CREATE INDEX task_profile_signal_owner ON task_profile_signals(profile_id);
CREATE TABLE task_profile_documents (
    profile_id TEXT PRIMARY KEY REFERENCES task_profiles(id) ON DELETE CASCADE,
    text TEXT NOT NULL
);
CREATE TABLE task_profile_resolutions (
    id TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    session_generation INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('shadow','suggest','resolve')),
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('skipped','complete','incomplete','unavailable')),
    reason TEXT NOT NULL,
    scanned INTEGER NOT NULL CHECK(scanned BETWEEN 0 AND 64),
    created_at TEXT NOT NULL,
    UNIQUE(profile_key,session_id,turn_id),
    FOREIGN KEY(profile_key,session_id,turn_id)
        REFERENCES turn_snapshots(profile_key,session_id,turn_id)
);
CREATE TABLE task_profile_candidates (
    resolution_id TEXT NOT NULL REFERENCES task_profile_resolutions(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL CHECK(rank BETWEEN 0 AND 2),
    profile_id TEXT REFERENCES task_profiles(id) ON DELETE SET NULL,
    target_index INTEGER NOT NULL CHECK(target_index BETWEEN 0 AND 7),
    decision TEXT NOT NULL CHECK(decision IN ('suggest','adopt')),
    PRIMARY KEY(resolution_id,rank)
);
-- References survive source deletion; no source content is copied here.
CREATE TABLE task_profile_deliveries (
    delivery_id TEXT NOT NULL REFERENCES retrieval_deliveries(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    PRIMARY KEY(delivery_id,profile_id)
);
INSERT INTO store_metadata VALUES ('task_profile_projection_version','task-profile-v1');
