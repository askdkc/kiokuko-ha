CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE principals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE principal_aliases (
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    external_id_hmac TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (namespace, external_id_hmac)
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    principal_id TEXT REFERENCES principals(id),
    platform TEXT NOT NULL,
    chat_type TEXT NOT NULL,
    conversation_hmac TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    identity_kind TEXT NOT NULL,
    identity_hash TEXT NOT NULL UNIQUE,
    canonical_root TEXT,
    git_remote_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE session_bindings (
    session_id TEXT PRIMARY KEY,
    parent_session_id TEXT REFERENCES session_bindings(session_id),
    conversation_id TEXT REFERENCES conversations(id),
    generation INTEGER NOT NULL DEFAULT 1 CHECK (generation > 0),
    history_inherited INTEGER NOT NULL DEFAULT 0 CHECK (history_inherited IN (0, 1)),
    transition_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE turn_snapshots (
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES session_bindings(session_id),
    turn_id TEXT NOT NULL,
    session_generation INTEGER NOT NULL CHECK (session_generation > 0),
    task_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    platform TEXT NOT NULL,
    chat_type TEXT NOT NULL,
    principal_id TEXT REFERENCES principals(id),
    conversation_id TEXT REFERENCES conversations(id),
    workspace_id TEXT REFERENCES workspaces(id),
    user_content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (profile_key, session_id, turn_id)
);

CREATE TABLE memory_entries (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN (
        'profile', 'principal', 'conversation', 'workspace',
        'principal_workspace', 'conversation_workspace'
    )),
    principal_id TEXT REFERENCES principals(id),
    conversation_id TEXT REFERENCES conversations(id),
    workspace_id TEXT REFERENCES workspaces(id),
    shared_by_admin INTEGER NOT NULL DEFAULT 0 CHECK (shared_by_admin IN (0, 1)),
    kind TEXT NOT NULL,
    subject_key TEXT,
    claim TEXT NOT NULL,
    normalized_claim TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'active', 'superseded', 'revoked', 'expired', 'conflicted'
    )),
    epistemic_status TEXT NOT NULL,
    confirmation_kind TEXT CHECK (confirmation_kind IN ('direct_verbatim', 'cli_approved')),
    authority INTEGER NOT NULL CHECK (authority BETWEEN 0 AND 100),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    auto_inject INTEGER NOT NULL DEFAULT 0 CHECK (auto_inject IN (0, 1)),
    current_revision INTEGER NOT NULL DEFAULT 1 CHECK (current_revision > 0),
    content_sha256 TEXT NOT NULL,
    supersedes_id TEXT REFERENCES memory_entries(id) ON DELETE SET NULL,
    valid_from TEXT,
    valid_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_verified_at TEXT,
    last_used_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count >= 0),
    CHECK (auto_inject = 0 OR (state = 'active' AND confirmation_kind IS NOT NULL)),
    CHECK (shared_by_admin = 0 OR scope_type IN ('profile', 'workspace')),
    CHECK (
        (scope_type = 'profile' AND principal_id IS NULL
            AND conversation_id IS NULL AND workspace_id IS NULL)
        OR (scope_type = 'principal' AND principal_id IS NOT NULL
            AND conversation_id IS NULL AND workspace_id IS NULL)
        OR (scope_type = 'conversation' AND principal_id IS NULL
            AND conversation_id IS NOT NULL AND workspace_id IS NULL)
        OR (scope_type = 'workspace' AND principal_id IS NULL
            AND conversation_id IS NULL AND workspace_id IS NOT NULL)
        OR (scope_type = 'principal_workspace' AND principal_id IS NOT NULL
            AND conversation_id IS NULL AND workspace_id IS NOT NULL)
        OR (scope_type = 'conversation_workspace' AND principal_id IS NULL
            AND conversation_id IS NOT NULL AND workspace_id IS NOT NULL)
    )
);

CREATE TABLE memory_revisions (
    entry_id TEXT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    operation TEXT NOT NULL CHECK (operation IN (
        'create', 'update', 'supersede', 'revoke', 'expire', 'merge'
    )),
    snapshot_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (entry_id, revision)
);

CREATE TABLE memory_candidates (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    session_generation INTEGER NOT NULL CHECK (session_generation > 0),
    principal_id TEXT REFERENCES principals(id),
    conversation_id TEXT REFERENCES conversations(id),
    workspace_id TEXT REFERENCES workspaces(id),
    proposed_scope_type TEXT NOT NULL,
    proposed_kind TEXT NOT NULL,
    proposed_subject_key TEXT,
    proposed_claim TEXT,
    proposal_action TEXT NOT NULL CHECK (proposal_action IN (
        'propose', 'correct', 'forget_request', 'pin_request', 'unpin_request', 'expire_request'
    )),
    target_entry_id TEXT REFERENCES memory_entries(id) ON DELETE CASCADE,
    expected_revision INTEGER CHECK (expected_revision > 0),
    epistemic_status TEXT NOT NULL,
    authority INTEGER NOT NULL CHECK (authority BETWEEN 0 AND 100),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    origin TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'pending', 'accepted', 'rejected', 'merged', 'invalid', 'invalidated_by_rewind'
    )),
    promoted_entry_id TEXT REFERENCES memory_entries(id) ON DELETE CASCADE,
    policy_reason TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (profile_key, session_id, turn_id)
        REFERENCES turn_snapshots(profile_key, session_id, turn_id),
    CHECK (
        (proposal_action = 'propose' AND proposed_claim IS NOT NULL
            AND target_entry_id IS NULL AND expected_revision IS NULL)
        OR (proposal_action <> 'propose' AND target_entry_id IS NOT NULL
            AND expected_revision IS NOT NULL)
    ),
    CHECK (proposal_action <> 'correct' OR proposed_claim IS NOT NULL)
);

CREATE TABLE memory_evidence (
    id TEXT PRIMARY KEY,
    entry_id TEXT,
    entry_revision INTEGER,
    candidate_id TEXT REFERENCES memory_candidates(id) ON DELETE CASCADE,
    source_role TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    quote_verified INTEGER NOT NULL DEFAULT 0 CHECK (quote_verified IN (0, 1)),
    excerpt TEXT,
    excerpt_sha256 TEXT,
    source_ref TEXT,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (entry_id, entry_revision)
        REFERENCES memory_revisions(entry_id, revision) ON DELETE CASCADE,
    CHECK (
        (entry_id IS NOT NULL AND entry_revision IS NOT NULL AND candidate_id IS NULL)
        OR (entry_id IS NULL AND entry_revision IS NULL AND candidate_id IS NOT NULL)
    )
);

CREATE TABLE explicit_operation_receipts (
    idempotency_key TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('remember', 'correct', 'forget')),
    state TEXT NOT NULL CHECK (state IN ('committed', 'purged')),
    entry_id TEXT REFERENCES memory_entries(id) ON DELETE SET NULL,
    entry_revision INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_key, session_id, turn_id)
        REFERENCES turn_snapshots(profile_key, session_id, turn_id)
);

CREATE TABLE turn_syncs (
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    FOREIGN KEY (profile_key, session_id, turn_id)
        REFERENCES turn_snapshots(profile_key, session_id, turn_id),
    PRIMARY KEY (profile_key, session_id, turn_id)
);

CREATE TABLE retrieval_deliveries (
    id TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('prepared', 'observed_in_history')),
    rendered_sha256 TEXT,
    character_count INTEGER NOT NULL CHECK (character_count >= 0),
    invalidates_prior_context INTEGER NOT NULL DEFAULT 0
        CHECK (invalidates_prior_context IN (0, 1)),
    created_at TEXT NOT NULL,
    observed_at TEXT,
    FOREIGN KEY (profile_key, session_id, turn_id)
        REFERENCES turn_snapshots(profile_key, session_id, turn_id)
);

CREATE TABLE retrieval_delivery_entries (
    delivery_id TEXT NOT NULL REFERENCES retrieval_deliveries(id) ON DELETE CASCADE,
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    FOREIGN KEY (entry_id, entry_revision)
        REFERENCES memory_revisions(entry_id, revision) ON DELETE CASCADE,
    PRIMARY KEY (delivery_id, entry_id, entry_revision)
);

CREATE TABLE retrieval_feedback (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('helpful', 'irrelevant', 'stale', 'conflicting')),
    comment TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (delivery_id, entry_id, entry_revision)
        REFERENCES retrieval_delivery_entries(delivery_id, entry_id, entry_revision)
        ON DELETE CASCADE
);

CREATE TABLE memory_tombstones (
    entry_id TEXT PRIMARY KEY,
    invalidated_through_revision INTEGER NOT NULL CHECK (invalidated_through_revision > 0),
    change_kind TEXT NOT NULL CHECK (change_kind IN (
        'corrected', 'superseded', 'revoked', 'expired', 'purged'
    ))
);

CREATE TABLE session_invalidations (
    session_id TEXT NOT NULL REFERENCES session_bindings(session_id),
    entry_id TEXT NOT NULL REFERENCES memory_tombstones(entry_id),
    observed_through_revision INTEGER NOT NULL DEFAULT 0 CHECK (observed_through_revision >= 0),
    PRIMARY KEY (session_id, entry_id)
);

CREATE TABLE retrieval_delivery_invalidations (
    delivery_id TEXT NOT NULL REFERENCES retrieval_deliveries(id) ON DELETE CASCADE,
    entry_id TEXT NOT NULL REFERENCES memory_tombstones(entry_id),
    invalidated_through_revision INTEGER NOT NULL,
    PRIMARY KEY (delivery_id, entry_id)
);

CREATE TABLE memory_search_documents (
    entry_id TEXT PRIMARY KEY,
    entry_revision INTEGER NOT NULL,
    subject_key TEXT,
    claim TEXT NOT NULL,
    FOREIGN KEY (entry_id, entry_revision)
        REFERENCES memory_revisions(entry_id, revision) ON DELETE CASCADE
);

CREATE TABLE memory_ngrams (
    token TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    FOREIGN KEY (entry_id, entry_revision)
        REFERENCES memory_revisions(entry_id, revision) ON DELETE CASCADE,
    PRIMARY KEY (token, entry_id, entry_revision)
);

CREATE TABLE embedding_profiles (
    id TEXT PRIMARY KEY,
    model_sha256 TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0)
);

CREATE TABLE memory_vectors (
    entry_id TEXT NOT NULL,
    entry_revision INTEGER NOT NULL,
    profile_id TEXT NOT NULL REFERENCES embedding_profiles(id),
    vector BLOB NOT NULL,
    FOREIGN KEY (entry_id, entry_revision)
        REFERENCES memory_revisions(entry_id, revision) ON DELETE CASCADE,
    PRIMARY KEY (entry_id, entry_revision, profile_id)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    entry_id TEXT,
    entry_revision INTEGER,
    candidate_id TEXT REFERENCES memory_candidates(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('pending', 'leased', 'completed', 'failed', 'blocked')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TEXT NOT NULL,
    lease_id TEXT,
    lease_expires_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (entry_id, entry_revision)
        REFERENCES memory_revisions(entry_id, revision) ON DELETE CASCADE,
    CHECK (
        (entry_id IS NOT NULL AND entry_revision IS NOT NULL AND candidate_id IS NULL)
        OR (entry_id IS NULL AND entry_revision IS NULL AND candidate_id IS NOT NULL)
    ),
    CHECK (state <> 'leased' OR (lease_id IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX memory_scope_state ON memory_entries(scope_type, principal_id, conversation_id, workspace_id, state);
CREATE INDEX memory_subject ON memory_entries(subject_key, state);
CREATE INDEX candidates_session_generation ON memory_candidates(session_id, session_generation, state);
CREATE INDEX snapshots_session_generation ON turn_snapshots(session_id, session_generation);
CREATE INDEX delivery_entry_reverse ON retrieval_delivery_entries(entry_id, entry_revision);
CREATE INDEX invalidations_entry_reverse ON session_invalidations(entry_id);
CREATE INDEX candidates_promoted_entry ON memory_candidates(promoted_entry_id);
CREATE INDEX jobs_available ON jobs(state, available_at);

CREATE TABLE store_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE workspace_aliases (
    identity_hash TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id)
);
CREATE TABLE status_events (
    code TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TRIGGER immutable_turn_snapshot BEFORE UPDATE ON turn_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
