import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .compatibility import active_home, check_host
from .config import load_config, setup
from .errors import KiokukoError, public_error
from .models import ExplicitCommand, Identity, canonical, new_id
from .operations import (PURGE_SCOPE, backup, entry_review, purge, purge_candidate,
                         reindex, restore, share, verify)
from .security import host_scan
from .service import Service
from .store import SCHEMA_VERSION, Store
from .workspace import resolve_workspace


def setup_parser(parser):
    sub = parser.add_subparsers(dest="kiokuko_action", required=True)
    for command in ("setup", "status", "doctor", "config", "verify", "reindex", "pending", "conflicts", "export", "principals", "workspaces", "curation"):
        sub.add_parser(command)
    search = sub.add_parser("search")
    search.add_argument("query")
    for command in ("show", "history", "approve", "reject", "purge", "purge-candidate"):
        child = sub.add_parser(command)
        child.add_argument("id")
    for command in ("remember", "correct", "forget"):
        child = sub.add_parser(command)
        child.add_argument("--operation-id", help="Reuse this ID to safely retry the exact same operation")
        if command == "remember":
            child.add_argument("--scope", required=True, choices=["principal", "principal_workspace"])
        else:
            child.add_argument("id")
            child.add_argument("--expected-revision", type=int, required=True)
        if command != "forget":
            child.add_argument("--text", required=True)
    child = sub.add_parser("share")
    child.add_argument("id")
    child.add_argument("--expected-revision", type=int, required=True)
    child.add_argument("--scope", choices=["profile", "workspace"], required=True)
    for command in ("backup", "restore"):
        child = sub.add_parser(command)
        child.add_argument("path", type=Path)
    child = sub.add_parser("import-native-user-profile")
    child.add_argument("path", type=Path)
    child.add_argument("--principal", required=True)
    child = sub.add_parser("workspace-link")
    child.add_argument("path", type=Path)
    child.add_argument("--workspace-id", required=True)


def confirm(payload, expected, *, input_fn=input, output=print):
    output(json.dumps(payload, ensure_ascii=False, indent=2))
    # Approval is tied to this displayed object/digest, rechecked under a write lock.
    if input_fn(f"Type {expected} to confirm: ") != expected:
        raise KiokukoError("CANCELLED")


def cli_snapshot(service, content, *, operation_id=None, principal="profile-owner"):
    operation_id = operation_id or new_id("cli")
    identity = Identity("cli", "cli_user", principal, "cli-admin", resolve_workspace(Path.cwd()), "dm")
    # Distinct command IDs avoid a mutable 'current CLI session'. A retry reuses all IDs.
    return service.snapshot(operation_id, operation_id, content, identity)


def execute(args, home, *, input_fn=input, output=print):
    action = args.kiokuko_action
    if action == "setup":
        check_host(home, require_config=False)
        setup(home)
        check_host(home)
        store = Store(home, initialize=True)
        store.close()
        return {"configured": True, "native_files": "preserved", "restart_agent": True}
    if action == "restore":
        check_host(home)
        confirm({"restore": str(args.path), "same_profile_only": True}, "restore", input_fn=input_fn, output=output)
        return restore(home, args.path)
    check_host(home)
    store = Store(home, initialize=action in {"remember", "import-native-user-profile"})
    try:
        service = Service(store, host_guard=check_host, content_guard=host_scan)
        if action in {"doctor", "verify"}:
            return verify(service)
        if action == "config":
            return load_config(home)
        if action == "status":
            with service.transaction() as db:
                return {"deliveries": dict(db.execute("SELECT state,count(*) FROM retrieval_deliveries GROUP BY state")),
                        "candidates": dict(db.execute("SELECT state,count(*) FROM memory_candidates GROUP BY state")),
                        "operations": dict(db.execute("SELECT state,count(*) FROM explicit_operation_receipts GROUP BY state")),
                        "recent_operations": [dict(row) for row in db.execute("SELECT operation,state,entry_id,entry_revision,created_at FROM explicit_operation_receipts ORDER BY created_at DESC LIMIT 20")],
                        "sync_skips_and_errors": [dict(row) for row in db.execute("SELECT * FROM status_events ORDER BY updated_at DESC")],
                        "verified_compaction": dict(db.execute("SELECT COALESCE(sum(accepted_count),0) AS accepted,COALESCE(sum(rejected_count),0) AS rejected FROM compaction_receipts").fetchone()),
                        "schema": SCHEMA_VERSION}
        if action == "curation":
            from .curation import curate
            return curate(service, cli_snapshot(service, "curation"), input_fn=input_fn, output=output)
        if action in {"remember", "correct", "forget"}:
            body = getattr(args, "text", None)
            scope = getattr(args, "scope", None)
            expected = getattr(args, "expected_revision", None)
            if expected is not None and expected < 1:
                raise KiokukoError("INVALID_REVISION")
            command = ExplicitCommand(action, body, scope, getattr(args, "id", None), expected)
            snap = cli_snapshot(service, canonical(asdict(command)), operation_id=args.operation_id)
            return service.explicit(snap, command)
        if action in {"search", "show", "history", "conflicts"}:
            snap = cli_snapshot(service, action)
            if action in {"show", "history"}:
                # Human administrator may inspect entries to review and purge any owner.
                with service.transaction() as db:
                    entry = service._entry(db, args.id, admin=True)
                    return [dict(row) for row in db.execute("SELECT * FROM memory_revisions WHERE entry_id=? ORDER BY revision", (args.id,))] if action == "history" else entry
            return service.search(snap, getattr(args, "query", ""), conflicts=action == "conflicts")
        if action == "pending":
            with service.transaction() as db:
                return [dict(row) for row in db.execute("SELECT * FROM memory_candidates WHERE state='pending' ORDER BY created_at")]
        if action in {"principals", "workspaces"}:
            with service.transaction() as db:
                return [dict(row) for row in db.execute(f"SELECT * FROM {action} ORDER BY id")]
        if action == "workspace-link":
            identity_hash = resolve_workspace(args.path)
            if identity_hash is None:
                raise KiokukoError("SCOPE_UNAVAILABLE")
            with service.transaction() as db:
                target = db.execute("SELECT id FROM workspaces WHERE id=?", (args.workspace_id,)).fetchone()
                before = db.execute("SELECT workspace_id FROM workspace_aliases WHERE identity_hash=?", (identity_hash,)).fetchone()
            if target is None:
                raise KiokukoError("SCOPE_UNAVAILABLE")
            confirm({"source": str(args.path.resolve()), "identity": identity_hash, "target_workspace": args.workspace_id,
                     "effect": "Future snapshots may read this workspace's shared memory; existing snapshots are unchanged"},
                    args.workspace_id, input_fn=input_fn, output=output)
            with service.transaction(write=True) as db:
                current = db.execute("SELECT workspace_id FROM workspace_aliases WHERE identity_hash=?", (identity_hash,)).fetchone()
                if (tuple(current) if current else None) != (tuple(before) if before else None):
                    raise KiokukoError("APPROVAL_CHANGED")
                db.execute("INSERT INTO workspace_aliases VALUES (?,?) ON CONFLICT(identity_hash) DO UPDATE SET workspace_id=excluded.workspace_id", (identity_hash, args.workspace_id))
            return {"workspace_linked": True}
        if action in {"approve", "purge-candidate"}:
            review, review_hash = service.candidate_review(args.id)
            confirm({**review, "purge_scope": PURGE_SCOPE} if action == "purge-candidate" else review,
                    args.id, input_fn=input_fn, output=output)
            return service.approve(args.id, review_hash) if action == "approve" else purge_candidate(service, args.id, review_hash)
        if action == "reject":
            service.reject(args.id)
            return {"rejected": args.id}
        if action in {"purge", "share"}:
            review, review_hash = entry_review(service, args.id)
            payload = {"entry": review, "operation": action}
            workspace = None
            if action == "purge":
                payload["scope"] = PURGE_SCOPE
            else:
                snap = cli_snapshot(service, "share")
                workspace = snap.workspace_id
                payload.update(new_scope=args.scope, new_workspace=workspace, expected_revision=args.expected_revision)
            confirm(payload, args.id, input_fn=input_fn, output=output)
            return purge(service, args.id, review_hash) if action == "purge" else share(service, args.id, args.expected_revision, review_hash, args.scope, workspace)
        if action == "reindex":
            return reindex(service)
        if action == "backup":
            return backup(service, args.path)
        if action == "export":
            with service.transaction() as db:
                return {"format": "kiokuko.export.v1", "entries": [dict(row) for row in db.execute("SELECT * FROM memory_entries ORDER BY id")]}
        if action == "import-native-user-profile":
            with service.transaction() as db:
                known = db.execute("SELECT 1 FROM principals WHERE id=?", (args.principal,)).fetchone()
            if known is None:
                raise KiokukoError("INVALID_PRINCIPAL")
            raw = args.path.read_text()
            # Inspect all input before staging any bounded chunks; never rewrite secrets.
            from .security import scan
            scan(raw, max_chars=1_000_000)
            host_scan(raw)
            confirm({"principal": args.principal, "source": str(args.path), "state": "pending", "characters": len(raw)},
                    args.principal, input_fn=input_fn, output=output)
            results = []
            for start in range(0, len(raw), 600):
                chunk = raw[start:start+600]
                if chunk.strip():
                    snap = cli_snapshot(service, chunk, principal=args.principal)
                    results.append(service.propose(snap, {"claim": chunk, "scope": "principal"}, source="import"))
            return {"candidates": results}
        raise KiokukoError("INVALID_COMMAND")
    finally:
        store.close()


def cli_handler(args):
    try:
        result = execute(args, active_home())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("error") else 0
    except (KiokukoError, OSError, ValueError, EOFError) as error:
        print(public_error(error), file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hermes kiokuko")
    setup_parser(parser)
    return cli_handler(parser.parse_args(argv))
