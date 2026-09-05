"""Deterministic file predicates, never an LLM's assertion of truth."""
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
import stat
import tomllib

from .errors import KiokukoError
from .models import canonical, digest, now
from .security import scan
from .service import insert
from .workspace import resolve_workspace

MAX_FILE_BYTES = 131072
EXTENSIONS = {".json", ".toml", ".md", ".txt", ".py", ".js", ".ts", ".yaml", ".yml", ".ini", ".cfg"}


def bind_root(db, snapshot, root):
    root = str(Path(root).resolve(strict=True))
    identity = resolve_workspace(root)
    mapped = db.execute("SELECT workspace_id FROM workspace_aliases WHERE identity_hash=?", (identity,)).fetchone()
    if (mapped[0] if mapped else identity) != snapshot.workspace_id:
        raise KiokukoError("WORKSPACE_CHANGED")
    insert(db, "snapshot_roots", dict(zip(("profile_key", "session_id", "turn_id"), snapshot.key),
                                    canonical_root=root, identity_hash=identity))


def read_source(root, relative):
    if not isinstance(relative, str) or not 0 < len(relative) <= 240:
        raise KiokukoError("FACT_PATH_DENIED")
    path = PurePosixPath(relative)
    if path.is_absolute() or str(path) != relative or any(p.startswith(".") for p in path.parts) or \
            "\\" in relative or path.suffix not in EXTENSIONS:
        raise KiokukoError("FACT_PATH_DENIED")
    scan(relative)
    descriptor = None
    try:
        # Walk every component with O_NOFOLLOW, including the bound root. No glob,
        # recursive search, arbitrary command, symlink, FIFO or external URI.
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise KiokukoError("FACT_SOURCE_UNAVAILABLE")
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(data) > MAX_FILE_BYTES or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise KiokukoError("FACT_SOURCE_CHANGED")
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except (OSError, UnicodeError, ValueError):
        raise KiokukoError("FACT_SOURCE_UNAVAILABLE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def verify_predicate(root, predicate):
    if not isinstance(predicate, dict) or set(predicate) != {"path", "format", "selector", "value"}:
        raise KiokukoError("FACT_INVALID")
    path, fmt, selector, value = (predicate[k] for k in ("path", "format", "selector", "value"))
    if re.search(r"password|passwd|secret|token|api.?key|credential|cookie|authorization", canonical([path, selector]), re.I):
        raise KiokukoError("SECRET_REJECTED")
    if type(value) not in {str, int, float, bool, type(None)}:
        raise KiokukoError("FACT_INVALID")
    if type(value) is float and not math.isfinite(value):
        raise KiokukoError("FACT_INVALID")
    scan(canonical(predicate))
    text, sha = read_source(root, path)
    try:
        if fmt in {"json", "toml"}:
            if PurePosixPath(path).suffix != "." + fmt or not isinstance(selector, list) or not 1 <= len(selector) <= 12:
                raise KiokukoError("FACT_INVALID")
            current = json.loads(text) if fmt == "json" else tomllib.loads(text)
            for key in selector:
                if isinstance(current, dict) and isinstance(key, str):
                    current = current[key]
                elif isinstance(current, list) and type(key) is int and key >= 0:
                    current = current[key]
                else:
                    raise KiokukoError("FACT_INVALID")
            if type(current) is not type(value) or canonical(current) != canonical(value):
                raise KiokukoError("FACT_MISMATCH")
            claim = f"設定ファイル {path} の {canonical(selector)} は {canonical(value)}。"
        elif fmt == "line":
            if type(selector) is not int or selector < 1 or not isinstance(value, str) or "\n" in value or "\r" in value:
                raise KiokukoError("FACT_INVALID")
            if text.splitlines()[selector - 1] != value:
                raise KiokukoError("FACT_MISMATCH")
            claim = f"ファイル {path} の {selector} 行目の記載: {canonical(value)}"
        else:
            raise KiokukoError("FACT_INVALID")
    except (KeyError, IndexError, TypeError, ValueError):
        raise KiokukoError("FACT_INVALID") from None
    scan(claim)
    return {"claim": claim, "source_sha256": sha, "predicate": predicate}


def root_for(db, snapshot):
    row = db.execute("SELECT * FROM snapshot_roots WHERE profile_key=? AND session_id=? AND turn_id=?", snapshot.key).fetchone()
    if row is None or resolve_workspace(row["canonical_root"]) != row["identity_hash"]:
        raise KiokukoError("FACT_WORKSPACE_UNAVAILABLE")
    return row["canonical_root"]


def fact_current(db, entry):
    row = db.execute("""SELECT f.*,r.canonical_root,r.identity_hash FROM verified_facts f
        JOIN snapshot_roots r USING(profile_key,session_id,turn_id)
        WHERE f.entry_id=? AND f.entry_revision=?""", (entry["id"], entry["current_revision"])).fetchone()
    if row is None:
        return None
    try:
        if resolve_workspace(row["canonical_root"]) != row["identity_hash"]:
            raise KiokukoError("WORKSPACE_CHANGED")
        result = verify_predicate(row["canonical_root"], json.loads(row["predicate_json"]))
        return True
    except (KiokukoError, OSError, ValueError):
        return False


def expire_stale(service, db, snapshot):
    from .retrieval import scope_sql
    clause, values = scope_sql(snapshot)
    rows = db.execute(f"""SELECT * FROM memory_entries WHERE {clause} AND state='active'
        AND EXISTS(SELECT 1 FROM verified_facts f WHERE f.entry_id=memory_entries.id
                   AND f.entry_revision=memory_entries.current_revision)""", values).fetchall()
    for row in rows:
        if fact_current(db, row) is False:
            service._change(db, dict(row), "expire_request")


def attach_fact(db, entry, snapshot, result):
    insert(db, "verified_facts", {"entry_id": entry["id"], "entry_revision": entry["current_revision"],
           **dict(zip(("profile_key", "session_id", "turn_id"), snapshot.key)),
           "predicate_json": canonical(result["predicate"]), "source_sha256": result["source_sha256"], "verified_at": now()})


def store_verified(service, snapshot, predicates, receipt_hash):
    accepted, rejected = [], 0
    with service.transaction(snapshot, write=True) as db:
        if db.execute("SELECT 1 FROM compaction_receipts WHERE receipt_hash=?", (receipt_hash,)).fetchone():
            return {"accepted": [], "rejected": 0, "replayed": True}
        root = root_for(db, snapshot)
        scope = "principal_workspace" if snapshot.chat_type == "dm" else "conversation_workspace"
        for predicate in predicates:
            try:
                checked = verify_predicate(root, predicate)
                service.validate_content(checked["claim"])
            except KiokukoError:
                rejected += 1
                continue
            key = digest(canonical([snapshot.profile_key, snapshot.principal_id if snapshot.chat_type == "dm" else snapshot.conversation_id,
                                    scope, snapshot.workspace_id, predicate]))
            if db.execute("SELECT 1 FROM fact_receipts WHERE receipt_hash=?", (key,)).fetchone():
                continue
            entry = service._create(db, snapshot, checked["claim"], scope, kind="project_fact", file_verified=True)
            attach_fact(db, entry, snapshot, checked)
            insert(db, "fact_receipts", {"receipt_hash": key, "entry_id": entry["id"]})
            accepted.append(entry["id"])
        # Check again before commit: no stale generation or modified file is adopted.
        for entry_id in accepted:
            entry = service._entry(db, entry_id, admin=True)
            if not fact_current(db, entry):
                raise KiokukoError("FACT_SOURCE_CHANGED")
        insert(db, "compaction_receipts", {"receipt_hash": receipt_hash, "accepted_count": len(accepted),
               "rejected_count": rejected, "created_at": now()})
    return {"accepted": accepted, "rejected": rejected, "replayed": False}
