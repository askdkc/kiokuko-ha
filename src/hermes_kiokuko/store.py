from contextlib import contextmanager
import hashlib
import hmac
from importlib.resources import files
import os
from pathlib import Path
import secrets
import sqlite3
import time

from .errors import KiokukoError
from .filesystem import (acquire_lock, atomic_write, checked_file, file_lock,
                         local_filesystem, private_directory)
from .models import now

SCHEMA = files("hermes_kiokuko").joinpath("migrations/001_initial.sql").read_text()
FTS = files("hermes_kiokuko").joinpath("migrations/fts.sql").read_text()
INITIAL_CHECKSUM = hashlib.sha256((SCHEMA + FTS).encode()).hexdigest()
FACT_SCHEMA = files("hermes_kiokuko").joinpath("migrations/002_verified_facts.sql").read_text()
FACT_CHECKSUM = hashlib.sha256(FACT_SCHEMA.encode()).hexdigest()
CHECKSUM = hashlib.sha256((SCHEMA + FTS + FACT_SCHEMA).encode()).hexdigest()
SCHEMA_VERSION = 2


def execute_statements(db, script):
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""


def schema_digest(db) -> str:
    rows = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return hashlib.sha256(repr([tuple(row) for row in rows]).encode()).hexdigest()


class Store:
    """One connection per operation; shared holder locks exclude restore."""
    def __init__(self, home: Path, *, initialize=False):
        self.home = Path(home).resolve()
        self.directory = self.home / "kiokuko"
        self.path = self.directory / "kiokuko.db"
        self.key_path = self.directory / "identity.key"
        self.holder = None
        if initialize:
            private_directory(self.directory)
        if not self.directory.is_dir() or self.directory.is_symlink():
            raise KiokukoError("STORE_NOT_INITIALIZED")
        local_filesystem(self.directory)
        self.holder = acquire_lock(self.directory / "holders.lock")
        try:
            with file_lock(self.directory / "migration.lock", exclusive=True):
                if not self.path.exists():
                    if not initialize:
                        raise KiokukoError("STORE_NOT_INITIALIZED")
                    self._initialize()
                checked_file(self.path)
                checked_file(self.key_path)
                self.key = self.key_path.read_bytes()
                if len(self.key) != 32:
                    raise KiokukoError("KEY_MISMATCH")
                self.profile_key = hmac.new(self.key, b"kiokuko-profile-v1", "sha256").hexdigest()
                self.inode = self.path.stat().st_ino
                self._migrate()
                with self.transaction() as db:
                    self.db_id = db.execute("SELECT value FROM store_metadata WHERE key='db_id'").fetchone()[0]
                connection = sqlite3.connect(self.path)
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                finally:
                    connection.close()
        except BaseException:
            self.close()
            raise

    def _initialize(self):
        if not self.key_path.exists():
            atomic_write(self.key_path, secrets.token_bytes(32))
        checked_file(self.key_path)
        key = self.key_path.read_bytes()
        if len(key) != 32:
            raise KiokukoError("KEY_MISMATCH")
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            try:
                # executescript commits implicitly; execute complete statements individually instead.
                statement = ""
                for line in FTS.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                fts = "1"
            except sqlite3.OperationalError as exc:
                if "no such module" not in str(exc) and "tokenizer" not in str(exc):
                    raise
                fts = "0"
            db.execute("PRAGMA application_id=0x4B484D45")
            db.execute("PRAGMA user_version=1")
            db.execute("INSERT INTO schema_migrations VALUES (1,?,?)", (INITIAL_CHECKSUM, now()))
            values = {"key_hash": hashlib.sha256(key).hexdigest(), "db_id": secrets.token_hex(24),
                      "fts": fts, "schema_hash": schema_digest(db)}
            db.executemany("INSERT INTO store_metadata VALUES (?,?)", values.items())
            db.commit()
        except BaseException:
            db.rollback()
            # Never delete or reset a failed database. Doctor reports the incomplete schema.
            raise
        finally:
            db.close()

    def _migrate(self):
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] == 1:
                self.guard(db, legacy=True)
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                    raise KiokukoError("DATABASE_INTEGRITY_FAILED")
                execute_statements(db, FACT_SCHEMA)
                db.execute("INSERT INTO schema_migrations VALUES (2,?,?)", (FACT_CHECKSUM, now()))
                db.execute("PRAGMA user_version=2")
                db.execute("UPDATE store_metadata SET value=? WHERE key='schema_hash'", (schema_digest(db),))
            self.guard(db)
            db.commit()
        except sqlite3.Error:
            raise KiokukoError("DATABASE_ERROR") from None
        finally:
            db.close()

    def close(self):
        if self.holder is not None:
            os.close(self.holder)
            self.holder = None

    def guard(self, db, *, legacy=False):
        if self.holder is None:
            raise KiokukoError("STORE_CLOSED")
        checked_file(self.path)
        checked_file(self.key_path)
        if self.path.stat().st_ino != self.inode or self.key_path.read_bytes() != self.key:
            raise KiokukoError("STORE_IDENTITY_CHANGED")
        if db.execute("PRAGMA application_id").fetchone()[0] != 0x4B484D45 or \
                db.execute("PRAGMA user_version").fetchone()[0] != (1 if legacy else SCHEMA_VERSION):
            raise KiokukoError("SCHEMA_MISMATCH")
        rows = db.execute("SELECT version,checksum FROM schema_migrations").fetchall()
        expected = [(1, INITIAL_CHECKSUM)] + ([] if legacy else [(2, FACT_CHECKSUM)])
        if sorted(tuple(row) for row in rows) != expected:
            raise KiokukoError("CHECKSUM_MISMATCH")
        meta = dict(db.execute("SELECT key,value FROM store_metadata").fetchall())
        if meta.get("key_hash") != hashlib.sha256(self.key).hexdigest():
            raise KiokukoError("KEY_MISMATCH")
        if meta.get("schema_hash") != schema_digest(db):
            raise KiokukoError("SCHEMA_MISMATCH")
        if hasattr(self, "db_id") and meta.get("db_id") != self.db_id:
            raise KiokukoError("STORE_IDENTITY_CHANGED")

    @contextmanager
    def transaction(self, *, write=False, deadline=None, check=None):
        db = None
        try:
            if self.holder is None:
                raise KiokukoError("STORE_CLOSED")
            for path in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
                checked_file(path)
            timeout = min(2.5 if write else .15, max(0., deadline - time.monotonic())) if deadline else (2.5 if write else .15)
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=timeout, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            if deadline:
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self.guard(db)
            if check:
                check(db)
            yield db
            self.guard(db)
            if check:
                check(db)
            if deadline and time.monotonic() >= deadline:
                raise KiokukoError("DEADLINE_EXCEEDED")
            db.commit()
        except sqlite3.Error as exc:
            name = getattr(exc, "sqlite_errorname", "")
            code = "STORE_BUSY" if name in {"SQLITE_BUSY", "SQLITE_LOCKED"} else \
                "DEADLINE_EXCEEDED" if name == "SQLITE_INTERRUPT" else "DATABASE_ERROR"
            raise KiokukoError(code) from None
        finally:
            if db is not None:
                db.close()

    def status(self, code: str):
        with self.transaction(write=True, deadline=time.monotonic() + .05) as db:
            db.execute("INSERT INTO status_events VALUES (?,1,?) ON CONFLICT(code) DO UPDATE SET count=count+1,updated_at=excluded.updated_at", (code, now()))
