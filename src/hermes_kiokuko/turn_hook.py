import time

from . import runtime
from .deliveries import prepare
from .errors import KiokukoError
from .explicit_commands import parse
from .identity import resolve_identity


def pre_llm_call(*, session_id: str, turn_id: str, user_message, task_id="",
                 conversation_history=None, platform="", parent_session_id="", **kwargs):
    service = None
    # One deadline covers snapshot, explicit commit and context preparation.
    deadline = time.monotonic() + .5
    try:
        service = runtime.current()
        identity = resolve_identity(service.store, session_id, platform)
        from agent.runtime_cwd import resolve_agent_cwd
        snapshot = service.snapshot(session_id, turn_id, user_message, identity, task_id=task_id,
                                    parent_session_id=parent_session_id, deadline=deadline,
                                    workspace_root=resolve_agent_cwd())
        receipt = None
        if service.config["explicit_commands"]["enabled"]:
            try:
                command = parse(user_message)
                if command:
                    receipt = service.explicit(snapshot, command, deadline=deadline)
            except KiokukoError as error:
                receipt = {"ok": False, "error": error.code}
                runtime.record_status(error.code, service)
        context = prepare(service, snapshot, user_message, conversation_history, receipt, deadline=deadline)
        return {"context": context}
    except (KiokukoError, OSError) as error:
        code = error.code if isinstance(error, KiokukoError) else "STORE_UNAVAILABLE"
        runtime.record_status(code, service)
        # Host cannot turn failure into delivery success: this has no signed marker.
        return {"context": "KIOKUKO STATUS: " + code + ". Memory context unavailable."}
