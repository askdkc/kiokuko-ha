"""Transactional memory operations. Model proposals never activate entries."""
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
import json
import re
import time

from .config import load_config
from .errors import KiokukoError
from .identity import can_read, scope_values
from .models import ExplicitCommand, Identity, TurnSnapshot, canonical, digest, new_id, now
from .security import scan


def insert(db, table: str, values: dict):
    # Table/column names are internal constants, never model or CLI values.
    fields = ','.join(values)
    db.execute(f"INSERT INTO {table} ({fields}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


class Service:
    def __init__(self, store, *, host_guard=None, content_guard=None):
        self.store = store
        self.host_guard = host_guard
        self.content_guard = content_guard
        load_config(store.home)

    @property
    def config(self):
        return load_config(self.store.home)

    def validate_content(self, text):
        scan(text)
        if self.content_guard:
            self.content_guard(text)
        return text

    def check_snapshot(self, db, snap):
        if snap.profile_key != self.store.profile_key:
            raise KiokukoError("PROFILE_IDENTITY_MISMATCH")
        row = db.execute("SELECT * FROM turn_snapshots WHERE profile_key=? AND session_id=? AND turn_id=?", snap.key).fetchone()
        if row is None or any(row[key] != value for key, value in asdict(snap).items()):
            raise KiokukoError("TURN_CONTEXT_CONFLICT")
        generation = db.execute("SELECT generation FROM session_bindings WHERE session_id=?", (snap.session_id,)).fetchone()
        if generation is None or generation[0] != snap.session_generation:
            raise KiokukoError("STALE_GENERATION")

    @contextmanager
    def transaction(self, snapshot=None, *, write=False, deadline=None):
        def check(db):
            if self.host_guard:
                self.host_guard(self.store.home)
            if snapshot:
                self.check_snapshot(db, snapshot)
        with self.store.transaction(write=write, deadline=deadline, check=check) as db:
            yield db

    def snapshot(self, session_id, turn_id, user_content, identity: Identity, *, task_id="", parent_session_id="", deadline=None, workspace_root=None):
        if not all(isinstance(value, str) and 0 < len(value) <= 256 for value in (session_id, turn_id)):
            raise KiokukoError("TURN_CONTEXT_UNAVAILABLE")
        if not isinstance(user_content, str):
            raise KiokukoError("UNSUPPORTED_CONTENT")
        if not isinstance(task_id, str) or len(task_id) > 256:
            raise KiokukoError("TURN_CONTEXT_UNAVAILABLE")
        with self.transaction(write=True, deadline=deadline) as db:
            if identity.principal_id:
                db.execute("INSERT OR IGNORE INTO principals(id,kind,created_at,updated_at) VALUES (?,?,?,?)",
                           (identity.principal_id, "local" if identity.principal_id == "profile-owner" else "gateway", now(), now()))
            if identity.conversation_id:
                db.execute("INSERT OR IGNORE INTO conversations(id,platform,chat_type,conversation_hmac,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                           (identity.conversation_id, identity.platform, identity.chat_type, identity.conversation_id, now(), now()))
            workspace = identity.workspace_id
            if workspace:
                mapped = db.execute("SELECT workspace_id FROM workspace_aliases WHERE identity_hash=?", (workspace,)).fetchone()
                if mapped:
                    workspace = mapped[0]
                db.execute("INSERT OR IGNORE INTO workspaces(id,identity_kind,identity_hash,created_at,updated_at) VALUES (?,?,?,?,?)",
                           (workspace, "canonical_hash", workspace, now(), now()))
            self._bind(db, session_id, parent_session_id=parent_session_id)
            row = db.execute("SELECT * FROM session_bindings WHERE session_id=?", (session_id,)).fetchone()
            if row["conversation_id"] not in {None, identity.conversation_id}:
                raise KiokukoError("SESSION_IDENTITY_MISMATCH")
            if identity.chat_type == "dm":
                other = db.execute("SELECT 1 FROM turn_snapshots WHERE session_id=? AND chat_type='dm' AND principal_id IS NOT ? LIMIT 1",
                                   (session_id, identity.principal_id)).fetchone()
                if other:
                    raise KiokukoError("SESSION_IDENTITY_MISMATCH")
            db.execute("UPDATE session_bindings SET conversation_id=? WHERE session_id=?", (identity.conversation_id, session_id))
            snap = TurnSnapshot(self.store.profile_key, session_id, turn_id, row["generation"],
                                identity.origin, identity.principal_id, identity.conversation_id,
                                workspace, digest(user_content), task_id, identity.platform, identity.chat_type)
            existing = db.execute("SELECT * FROM turn_snapshots WHERE profile_key=? AND session_id=? AND turn_id=?", snap.key).fetchone()
            if existing:
                # CWD changes are applied only to new snapshots. Other identity changes conflict.
                snap = TurnSnapshot(**{**asdict(snap), "workspace_id": existing["workspace_id"]})
                self.check_snapshot(db, snap)
            else:
                insert(db, "turn_snapshots", {**asdict(snap), "created_at": now()})
                if workspace_root and snap.workspace_id:
                    from .facts import bind_root
                    bind_root(db, snap, workspace_root)
            return snap

    def get_snapshot(self, session_id, turn_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM turn_snapshots WHERE profile_key=? AND session_id=? AND turn_id=?",
                             (self.store.profile_key, session_id, turn_id)).fetchone()
            if row is None:
                raise KiokukoError("TURN_CONTEXT_UNAVAILABLE")
            snap = TurnSnapshot(**{key: row[key] for key in TurnSnapshot.__dataclass_fields__})
            self.check_snapshot(db, snap)
            return snap

    def _bind(self, db, session_id, *, parent_session_id="", reset=False):
        if not session_id or session_id == parent_session_id:
            raise KiokukoError("INVALID_SESSION_TRANSITION")
        parent = None
        if parent_session_id and not reset:
            if not db.execute("SELECT 1 FROM session_bindings WHERE session_id=?", (parent_session_id,)).fetchone():
                raise KiokukoError("PARENT_CONTEXT_UNAVAILABLE")
            parent = parent_session_id
            cursor = parent
            visited = {session_id}
            while cursor:
                if cursor in visited:
                    raise KiokukoError("INVALID_SESSION_TRANSITION")
                visited.add(cursor)
                cursor = db.execute("SELECT parent_session_id FROM session_bindings WHERE session_id=?", (cursor,)).fetchone()[0]
        existing = db.execute("SELECT * FROM session_bindings WHERE session_id=?", (session_id,)).fetchone()
        if existing:
            if parent and existing["parent_session_id"] != parent:
                raise KiokukoError("INVALID_SESSION_TRANSITION")
            return
        insert(db, "session_bindings", {"session_id": session_id, "parent_session_id": parent,
                "generation": 1, "history_inherited": int(bool(parent)),
                "transition_reason": "branch" if parent else "new", "created_at": now(), "updated_at": now()})

    def transition(self, session_id, *, parent_session_id="", reset=False, rewound=False):
        with self.transaction(write=True) as db:
            self._bind(db, session_id, parent_session_id=parent_session_id, reset=reset)
            if rewound:
                db.execute("UPDATE session_bindings SET generation=generation+1,updated_at=? WHERE session_id=?", (now(), session_id))
                db.execute("UPDATE memory_candidates SET state='invalidated_by_rewind',resolved_at=? WHERE session_id=? AND state='pending'", (now(), session_id))

    def _entry(self, db, entry_id, snapshot=None, *, admin=False):
        row = db.execute("SELECT * FROM memory_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None or (not admin and (snapshot is None or not can_read(row, snapshot))):
            raise KiokukoError("ENTRY_UNAVAILABLE")
        return dict(row)

    def get(self, snapshot, entry_id, *, history=False):
        with self.transaction(snapshot) as db:
            entry = self._entry(db, entry_id, snapshot)
            if history:
                revisions = []
                for row in db.execute("SELECT revision,operation,snapshot_json,created_at FROM memory_revisions WHERE entry_id=? ORDER BY revision", (entry_id,)):
                    historical = json.loads(row["snapshot_json"])
                    if can_read(historical, snapshot):
                        revisions.append(dict(row))
                return revisions
            return entry

    def _revision(self, db, entry, operation, actor):
        insert(db, "memory_revisions", {"entry_id": entry["id"], "revision": entry["current_revision"],
               "operation": operation, "snapshot_json": canonical(entry), "actor": actor, "created_at": now()})
        self._project(db, entry)

    def _project(self, db, entry):
        db.execute("DELETE FROM memory_search_documents WHERE entry_id=?", (entry["id"],))
        db.execute("DELETE FROM memory_ngrams WHERE entry_id=?", (entry["id"],))
        db.execute("DELETE FROM memory_vectors WHERE entry_id=?", (entry["id"],))
        if entry["state"] == "active":
            insert(db, "memory_search_documents", {"entry_id": entry["id"], "entry_revision": entry["current_revision"],
                   "subject_key": entry["subject_key"], "claim": entry["claim"]})
            from .retrieval import tokens
            db.executemany("INSERT OR IGNORE INTO memory_ngrams(token,entry_id,entry_revision) VALUES (?,?,?)",
                           [(token, entry["id"], entry["current_revision"]) for token in tokens(entry["claim"])])

    def _create(self, db, snapshot, body, scope, *, approved=False, kind="statement", subject=None, file_verified=False):
        p, c, w = scope_values(scope, snapshot)
        conflicting = []
        if subject:
            conflicting = db.execute("SELECT * FROM memory_entries WHERE scope_type=? AND principal_id IS ? AND conversation_id IS ? AND workspace_id IS ? AND subject_key=? AND state='active' AND claim<>?",
                                     (scope, p, c, w, subject, body)).fetchall()
        entry = dict(id=new_id("mem"), scope_type=scope, principal_id=p, conversation_id=c, workspace_id=w,
                     shared_by_admin=0, kind=kind, subject_key=subject, claim=body,
                     normalized_claim=body, state="active", epistemic_status="user_approved" if approved else "explicit_user",
                     confirmation_kind="cli_approved" if approved else "direct_verbatim", authority=90, confidence=1.0,
                     pinned=0, auto_inject=1, current_revision=1, content_sha256=digest(body), supersedes_id=None,
                     valid_from=None, valid_until=None, created_at=now(), updated_at=now(), last_verified_at=now(),
                     last_used_at=None, use_count=0)
        if conflicting:
            entry.update(state="conflicted", auto_inject=0)
            for older in conflicting:
                self._change(db, dict(older), "conflict", approved=True)
        if file_verified:
            # Verification is separate from user approval; legacy tools cannot invent it.
            entry.update(epistemic_status="file_verified", confirmation_kind=None,
                         auto_inject=0, authority=95)
        insert(db, "memory_entries", entry)
        self._revision(db, entry, "create", snapshot.principal_id or "human-cli")
        return entry

    def _invalidate(self, db, entry_id, revision, kind):
        db.execute("INSERT INTO memory_tombstones VALUES (?,?,?) ON CONFLICT(entry_id) DO UPDATE SET invalidated_through_revision=max(invalidated_through_revision,excluded.invalidated_through_revision),change_kind=excluded.change_kind",
                   (entry_id, revision, kind))
        db.execute("INSERT OR IGNORE INTO session_invalidations(session_id,entry_id) SELECT DISTINCT d.session_id,? FROM retrieval_deliveries d JOIN retrieval_delivery_entries e ON e.delivery_id=d.id WHERE e.entry_id=? AND e.entry_revision<=?",
                   (entry_id, entry_id, revision))

    def _change(self, db, entry, action, *, body=None, approved=False, scope=None, workspace=None):
        previous = entry["current_revision"]
        entry = dict(entry)
        entry.update(current_revision=previous + 1, updated_at=now())
        operation, change = "update", "corrected"
        if action == "correct":
            entry.update(claim=body, normalized_claim=body, content_sha256=digest(body), state="active", auto_inject=1,
                         epistemic_status="user_approved" if approved else "user_correction",
                         confirmation_kind="cli_approved" if approved else "direct_verbatim", authority=100, confidence=1.0)
        elif action in {"forget", "forget_request", "expire_request"}:
            state = "expired" if action == "expire_request" else "revoked"
            entry.update(state=state, auto_inject=0)
            operation, change = ("expire", "expired") if state == "expired" else ("revoke", "revoked")
        elif action in {"pin_request", "unpin_request"}:
            entry["pinned"] = int(action == "pin_request")
        elif action == "conflict":
            entry.update(state="conflicted", auto_inject=0)
        elif action == "share":
            if scope not in {"profile", "workspace"} or (scope == "workspace" and not workspace):
                raise KiokukoError("SCOPE_UNAVAILABLE")
            entry.update(scope_type=scope, principal_id=None, conversation_id=None,
                         workspace_id=workspace if scope == "workspace" else None, shared_by_admin=1)
        else:
            raise KiokukoError("INVALID_ACTION")
        db.execute("UPDATE memory_entries SET " + ','.join(f"{key}=?" for key in entry if key != "id") + " WHERE id=?",
                   (*[v for k, v in entry.items() if k != "id"], entry["id"]))
        self._revision(db, entry, operation, "human-cli" if approved else "explicit-user")
        if action in {"share", "pin_request", "unpin_request"}:
            db.execute("""INSERT INTO verified_facts
                SELECT entry_id,?,profile_key,session_id,turn_id,predicate_json,source_sha256,verified_at
                FROM verified_facts WHERE entry_id=? AND entry_revision=?""",
                       (entry["current_revision"], entry["id"], previous))
        self._invalidate(db, entry["id"], previous, change)
        return entry

    def explicit(self, snapshot, command: ExplicitCommand, *, deadline=None):
        if command.body is not None:
            self.validate_content(command.body)
        if command.action not in {"remember", "correct", "forget"}:
            raise KiokukoError("INVALID_ACTION")
        if not snapshot.immediate:
            if snapshot.origin in {"background_review", "delegation"}:
                raise KiokukoError("EXPLICIT_OPERATION_NOT_ALLOWED")
            candidate = self.propose(snapshot, {"action": "propose" if command.action == "remember" else
                 "forget_request" if command.action == "forget" else "correct", "claim": command.body,
                 "scope": command.scope, "entry_id": command.entry_id, "expected_revision": command.expected_revision},
                 idempotency="explicit-pending:" + digest(canonical(snapshot.key)))
            return {"candidate_id": candidate["id"], "state": "pending"}
        if command.action == "remember" and command.scope not in {"principal", "principal_workspace"}:
            raise KiokukoError("SCOPE_DENIED")
        key = digest(canonical([*snapshot.key, command.action]))
        request_hash = digest(canonical(asdict(command)))
        with self.transaction(snapshot, write=True, deadline=deadline) as db:
            receipt = db.execute("SELECT * FROM explicit_operation_receipts WHERE idempotency_key=?", (key,)).fetchone()
            if receipt:
                if receipt["request_sha256"] != request_hash:
                    raise KiokukoError("TURN_CONTEXT_CONFLICT")
                if receipt["state"] == "purged":
                    raise KiokukoError("PURGED_OPERATION")
                return {"entry_id": receipt["entry_id"], "revision": receipt["entry_revision"]}
            if command.action == "remember":
                entry = self._create(db, snapshot, command.body, command.scope)
            else:
                entry = self._entry(db, command.entry_id, snapshot)
                if entry["principal_id"] != snapshot.principal_id or not entry["scope_type"].startswith("principal"):
                    raise KiokukoError("ENTRY_UNAVAILABLE")
                if entry["current_revision"] != command.expected_revision:
                    raise KiokukoError("REVISION_CONFLICT")
                entry = self._change(db, entry, command.action, body=command.body)
            insert(db, "explicit_operation_receipts", {"idempotency_key": key, "request_sha256": request_hash, "profile_key": snapshot.profile_key,
                   "session_id": snapshot.session_id, "turn_id": snapshot.turn_id, "operation": command.action,
                   "state": "committed", "entry_id": entry["id"], "entry_revision": entry["current_revision"], "created_at": now()})
            if command.body:
                insert(db, "memory_evidence", {"id": new_id("ev"), "entry_id": entry["id"], "entry_revision": entry["current_revision"],
                       "source_role": "user", "source_kind": "explicit_command", "quote_verified": 1,
                       "excerpt": command.body, "excerpt_sha256": digest(command.body), "observed_at": now()})
            return {"entry_id": entry["id"], "revision": entry["current_revision"]}

    def propose(self, snapshot, args, *, idempotency=None, source="model", _db=None):
        if snapshot.origin in {"background_review", "delegation"}:
            raise KiokukoError("OPTIONAL_CAPTURE_UNAVAILABLE")
        allowed = {"action", "claim", "scope", "entry_id", "expected_revision", "evidence_quote", "kind", "subject_key"}
        if set(args) - allowed:
            raise KiokukoError("INVALID_ARGUMENTS")
        action = args.get("action", "propose")
        if action not in {"propose", "correct", "forget_request", "pin_request", "unpin_request", "expire_request"}:
            raise KiokukoError("INVALID_ACTION")
        body, quote = args.get("claim"), args.get("evidence_quote")
        if action in {"propose", "correct"}:
            self.validate_content(body)
        elif body is not None:
            raise KiokukoError("INVALID_ARGUMENTS")
        if quote is not None:
            self.validate_content(quote)
        kind = args.get("kind", "statement")
        if kind not in {"statement", "identity", "preference", "constraint", "environment_fact", "project_fact", "decision", "lesson", "milestone"}:
            raise KiokukoError("INVALID_KIND")
        subject = args.get("subject_key")
        if subject is not None:
            self.validate_content(subject)
        with (self.transaction(snapshot, write=True) if _db is None else nullcontext(_db)) as db:
            if action == "propose":
                scope = args.get("scope") or ("conversation" if snapshot.chat_type != "dm" else "principal")
                p, c, w = scope_values(scope, snapshot)
                if snapshot.chat_type != "dm" and scope.startswith("principal"):
                    raise KiokukoError("SCOPE_DENIED")
                target, revision = None, None
            else:
                target = self._entry(db, args.get("entry_id"), snapshot)
                revision = args.get("expected_revision")
                if type(revision) is not int or revision != target["current_revision"]:
                    raise KiokukoError("REVISION_CONFLICT")
                scope, p, c, w = target["scope_type"], target["principal_id"], target["conversation_id"], target["workspace_id"]
                target = target["id"]
            key = idempotency or digest(canonical([snapshot.key, args]))
            existing = db.execute("SELECT * FROM memory_candidates WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                return {"id": existing["id"], "state": existing["state"]}
            candidate = dict(id=new_id("cand"), idempotency_key=key, profile_key=snapshot.profile_key,
                session_id=snapshot.session_id, turn_id=snapshot.turn_id, session_generation=snapshot.session_generation,
                principal_id=p, conversation_id=c, workspace_id=w, proposed_scope_type=scope, proposed_kind=kind,
                proposed_subject_key=subject, proposed_claim=body, proposal_action=action, target_entry_id=target,
                expected_revision=revision, epistemic_status="legacy_import" if source == "import" else "assistant_derived",
                authority=55 if source == "model" else 90, confidence=.5, origin=snapshot.origin,
                state="pending", promoted_entry_id=None, policy_reason="HUMAN_APPROVAL_REQUIRED", created_at=now(), resolved_at=None)
            insert(db, "memory_candidates", candidate)
            if quote:
                insert(db, "memory_evidence", {"id": new_id("ev"), "candidate_id": candidate["id"], "source_role": "user",
                       "source_kind": "proposed_quote", "quote_verified": 0, "excerpt": quote,
                       "excerpt_sha256": digest(quote), "observed_at": now()})
            return {"id": candidate["id"], "state": "pending"}

    def candidate_review(self, candidate_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
            if row is None:
                raise KiokukoError("CANDIDATE_UNAVAILABLE")
            review = {"candidate": dict(row), "evidence": [dict(r) for r in db.execute("SELECT * FROM memory_evidence WHERE candidate_id=? ORDER BY id", (candidate_id,))]}
            review["target"] = self._entry(db, row["target_entry_id"], admin=True) if row["target_entry_id"] else None
            return review, digest(canonical(review))

    def approve(self, candidate_id, review_digest):
        # Only the trusted CLI adapter exposes this method, never a model tool.
        with self.transaction(write=True) as db:
            candidate = db.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None or candidate["state"] != "pending":
                raise KiokukoError("CANDIDATE_UNAVAILABLE")
            candidate = dict(candidate)
            target = self._entry(db, candidate["target_entry_id"], admin=True) if candidate["target_entry_id"] else None
            evidence = [dict(r) for r in db.execute("SELECT * FROM memory_evidence WHERE candidate_id=? ORDER BY id", (candidate_id,))]
            review = {"candidate": candidate, "evidence": evidence, "target": target}
            if digest(canonical(review)) != review_digest:
                raise KiokukoError("APPROVAL_CHANGED")
            row = db.execute("SELECT * FROM turn_snapshots WHERE profile_key=? AND session_id=? AND turn_id=?",
                             (candidate["profile_key"], candidate["session_id"], candidate["turn_id"])).fetchone()
            snap = TurnSnapshot(**{k: row[k] for k in TurnSnapshot.__dataclass_fields__})
            self.check_snapshot(db, snap)
            if candidate["proposed_claim"]:
                self.validate_content(candidate["proposed_claim"])
            if candidate["proposal_action"] == "propose":
                entry = self._create(db, snap, candidate["proposed_claim"], candidate["proposed_scope_type"],
                                     approved=True, kind=candidate["proposed_kind"], subject=candidate["proposed_subject_key"])
            else:
                if target["current_revision"] != candidate["expected_revision"]:
                    raise KiokukoError("REVISION_CONFLICT")
                entry = self._change(db, target, candidate["proposal_action"], body=candidate["proposed_claim"], approved=True)
            db.execute("UPDATE memory_candidates SET state='accepted',promoted_entry_id=?,resolved_at=? WHERE id=?",
                       (entry["id"], now(), candidate_id))
            return {"entry_id": entry["id"], "revision": entry["current_revision"]}

    def reject(self, candidate_id):
        with self.transaction(write=True) as db:
            if db.execute("UPDATE memory_candidates SET state='rejected',resolved_at=? WHERE id=? AND state='pending'", (now(), candidate_id)).rowcount != 1:
                raise KiokukoError("CANDIDATE_UNAVAILABLE")

    def search(self, snapshot, query="", *, conflicts=False):
        from .retrieval import search
        with self.transaction(snapshot, deadline=time.monotonic() + .15) as db:
            return search(db, snapshot, query, self.config, conflicts=conflicts)

    def feedback(self, snapshot, entry_id, revision, delivery_id, verdict):
        if verdict not in {"helpful", "irrelevant", "stale", "conflicting"}:
            raise KiokukoError("INVALID_VERDICT")
        with self.transaction(snapshot, write=True) as db:
            self._entry(db, entry_id, snapshot)
            delivery = db.execute("SELECT 1 FROM retrieval_deliveries d JOIN retrieval_delivery_entries e ON e.delivery_id=d.id WHERE d.id=? AND d.session_id=? AND e.entry_id=? AND e.entry_revision=?",
                                  (delivery_id, snapshot.session_id, entry_id, revision)).fetchone()
            if not delivery:
                raise KiokukoError("DELIVERY_UNAVAILABLE")
            insert(db, "retrieval_feedback", {"id": new_id("feedback"), "delivery_id": delivery_id, "entry_id": entry_id,
                   "entry_revision": revision, "verdict": verdict, "actor": snapshot.principal_id or "unknown", "created_at": now()})
