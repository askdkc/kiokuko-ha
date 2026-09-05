from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import uuid


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def new_id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


@dataclass(frozen=True)
class Identity:
    platform: str
    origin: str
    principal_id: str | None
    conversation_id: str | None
    workspace_id: str | None
    chat_type: str


@dataclass(frozen=True)
class TurnSnapshot:
    profile_key: str
    session_id: str
    turn_id: str
    session_generation: int
    origin: str
    principal_id: str | None
    conversation_id: str | None
    workspace_id: str | None
    user_content_sha256: str
    task_id: str = ""
    platform: str = ""
    chat_type: str = ""

    @property
    def key(self):
        return self.profile_key, self.session_id, self.turn_id

    @property
    def immediate(self) -> bool:
        return self.principal_id is not None and self.origin in {"cli", "dm", "cli_user"}


@dataclass(frozen=True)
class ExplicitCommand:
    action: str
    body: str | None = None
    scope: str | None = None
    entry_id: str | None = None
    expected_revision: int | None = None
