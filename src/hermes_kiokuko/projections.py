"""Commit fence for optional embedding consumers. MVP never enqueues these jobs."""
import math
import struct

from .errors import KiokukoError
from .models import now


def commit_vector_result(service, job_id, lease_id, profile_id, vector):
    if not isinstance(vector, bytes):
        raise KiokukoError("INVALID_VECTOR")
    with service.transaction(write=True) as db:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None or job["state"] != "leased" or job["lease_id"] != lease_id or job["lease_expires_at"] <= now():
            raise KiokukoError("STALE_JOB")
        entry = db.execute("SELECT * FROM memory_entries WHERE id=?", (job["entry_id"],)).fetchone()
        if entry is None or entry["state"] != "active" or entry["current_revision"] != job["entry_revision"]:
            raise KiokukoError("STALE_JOB")
        profile = db.execute("SELECT dimensions FROM embedding_profiles WHERE id=?", (profile_id,)).fetchone()
        if profile is None or len(vector) != profile[0] * 4 or not all(math.isfinite(v[0]) for v in struct.iter_unpack('<f', vector)):
            raise KiokukoError("INVALID_VECTOR")
        db.execute("INSERT OR REPLACE INTO memory_vectors VALUES (?,?,?,?)", (entry["id"], job["entry_revision"], profile_id, vector))
        db.execute("UPDATE jobs SET state='completed',lease_id=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?", (now(), job_id))
