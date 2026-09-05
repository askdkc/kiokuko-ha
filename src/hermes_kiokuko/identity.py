import hmac
from pathlib import Path

from .errors import KiokukoError
from .models import Identity, canonical
from .workspace import resolve_workspace


def opaque(key: bytes, kind: str, *parts: str) -> str:
    return kind + "_" + hmac.new(key, canonical(parts).encode(), "sha256").hexdigest()


def bound_values() -> dict[str, str]:
    from gateway import session_context
    result = {}
    for key in ("PLATFORM", "SOURCE", "CHAT_ID", "CHAT_TYPE", "THREAD_ID", "USER_ID", "USER_ID_ALT",
                "KEY", "ID", "PROFILE"):
        value = session_context._VAR_MAP["HERMES_SESSION_" + key].get()
        if value is session_context._UNSET:
            continue
        if not isinstance(value, str):
            raise KiokukoError("INVALID_IDENTITY")
        result[key] = value
    cron = session_context._VAR_MAP["HERMES_CRON_SESSION"].get()
    if cron is not session_context._UNSET:
        if not isinstance(cron, str):
            raise KiokukoError("INVALID_IDENTITY")
        result["CRON"] = cron
    return result


def resolve_identity(store, session_id: str, platform: str, *, workspace=True) -> Identity:
    from agent.delegation_context import is_delegated_child_context
    from agent.runtime_cwd import resolve_agent_cwd
    from tools.skill_provenance import get_current_write_origin
    from hermes_cli.profiles import get_active_profile_name
    bound = bound_values()
    if bound.get("ID") and bound["ID"] != session_id:
        raise KiokukoError("SESSION_IDENTITY_MISMATCH")
    if bound.get("PROFILE") and bound["PROFILE"] != get_active_profile_name():
        raise KiokukoError("PROFILE_IDENTITY_MISMATCH")
    if platform and bound.get("PLATFORM") and platform != bound["PLATFORM"]:
        raise KiokukoError("PLATFORM_IDENTITY_MISMATCH")
    actual_platform = bound.get("PLATFORM") or platform
    principal = None
    conversation = None
    chat_type = bound.get("CHAT_TYPE", "")
    if actual_platform == "cli" and not bound.get("USER_ID") and not bound.get("USER_ID_ALT"):
        principal, origin, chat_type = "profile-owner", "cli", "dm"
    else:
        origin = "unknown"
        # A platform argument alone cannot authenticate a sender.
        if bound.get("PLATFORM") and bound.get("ID") == session_id:
            kind = "user_id_alt" if bound.get("USER_ID_ALT") else "user_id"
            user = bound.get("USER_ID_ALT") or bound.get("USER_ID")
            if user:
                principal = opaque(store.key, "principal", actual_platform, kind, user)
            if chat_type in {"private", "dm", "direct"}:
                origin, chat_type = "dm", "dm"
            elif chat_type in {"group", "supergroup", "channel", "guild"}:
                origin, chat_type = "group_chat", "group"
            chat = bound.get("CHAT_ID") or bound.get("KEY")
            if chat:
                conversation = opaque(store.key, "conversation", actual_platform, chat, bound.get("THREAD_ID", ""))
    if conversation is None:
        conversation = opaque(store.key, "conversation", actual_platform, session_id)
    if bound.get("CRON") == "1":
        origin = "cron"
    if is_delegated_child_context():
        origin = "delegation"
    if get_current_write_origin() == "background_review":
        origin = "background_review"
    cwd = resolve_agent_cwd() if workspace else None
    return Identity(actual_platform, origin, principal, conversation, resolve_workspace(cwd), chat_type)


def can_read(row, snapshot) -> bool:
    scope = row["scope_type"]
    if scope == "profile":
        return bool(row["shared_by_admin"])
    if scope == "workspace":
        return bool(row["shared_by_admin"]) and snapshot.workspace_id is not None and row["workspace_id"] == snapshot.workspace_id
    if "workspace" in scope and (snapshot.workspace_id is None or row["workspace_id"] != snapshot.workspace_id):
        return False
    if scope.startswith("principal"):
        return snapshot.chat_type == "dm" and snapshot.principal_id is not None and row["principal_id"] == snapshot.principal_id
    if scope.startswith("conversation"):
        return snapshot.conversation_id is not None and row["conversation_id"] == snapshot.conversation_id
    return False


def scope_values(scope: str, snapshot):
    if scope not in {"principal", "principal_workspace", "conversation", "conversation_workspace"}:
        raise KiokukoError("SCOPE_DENIED")
    p = snapshot.principal_id if scope.startswith("principal") else None
    c = snapshot.conversation_id if scope.startswith("conversation") else None
    w = snapshot.workspace_id if "workspace" in scope else None
    if (scope.startswith("principal") and not p) or (scope.startswith("conversation") and not c) or ("workspace" in scope and not w):
        raise KiokukoError("SCOPE_UNAVAILABLE")
    return p, c, w
