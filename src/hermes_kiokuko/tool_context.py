from contextvars import ContextVar
from dataclasses import dataclass

from . import runtime
from .errors import KiokukoError, public_error
from .identity import resolve_identity

TOOL_NAMES = frozenset({"kiokuko_recall", "kiokuko_propose", "kiokuko_manage"})
_verified = ContextVar("kiokuko_verified_tool_context", default=None)


@dataclass(frozen=True)
class VerifiedContext:
    service: object
    snapshot: object
    tool_name: str


def tool_execution_middleware(*, tool_name, args, next_call, session_id="", turn_id="", task_id="", **kwargs):
    if tool_name not in TOOL_NAMES:
        return next_call(args)
    try:
        service = runtime.current()
        snap = service.get_snapshot(session_id, turn_id)
        live = resolve_identity(service.store, session_id, snap.platform, workspace=False)
        if snap.task_id != task_id or any(getattr(snap, key) != getattr(live, key) for key in
                ("platform", "origin", "principal_id", "conversation_id", "chat_type")):
            raise KiokukoError("TOOL_CONTEXT_MISMATCH")
        context = VerifiedContext(service, snap, tool_name)
    except Exception as error:
        # Returning an error is essential: the host resumes after a middleware exception.
        return public_error(error)
    token = _verified.set(context)
    try:
        return next_call(args)
    finally:
        _verified.reset(token)


def require_context(tool_name, session_id, task_id):
    context = _verified.get()
    if context is None or context.tool_name != tool_name or \
            context.snapshot.session_id != session_id or context.snapshot.task_id != (task_id or ""):
        raise KiokukoError("TOOL_CONTEXT_UNAVAILABLE")
    # Handler and service both verify, including the generation at commit.
    with context.service.transaction(context.snapshot):
        pass
    return context
