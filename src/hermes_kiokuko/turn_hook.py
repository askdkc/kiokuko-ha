import logging
import time

from . import runtime
from .deliveries import prepare
from .errors import KiokukoError
from .explicit_commands import parse
from .identity import resolve_identity

logger = logging.getLogger(__name__)


def pre_llm_call(*, session_id: str, turn_id: str, user_message, task_id="",
                 conversation_history=None, platform="", parent_session_id="", **kwargs):
    service = None
    # One deadline covers snapshot, explicit commit and context preparation.
    deadline = time.monotonic() + .5
    stage = "runtime"
    try:
        service = runtime.current()
        stage = "identity"
        identity = resolve_identity(service.store, session_id, platform, host_session=True)
        from agent.runtime_cwd import resolve_agent_cwd
        stage = "snapshot"
        snapshot = service.snapshot(session_id, turn_id, user_message, identity, task_id=task_id,
                                    parent_session_id=parent_session_id, deadline=deadline,
                                    workspace_root=resolve_agent_cwd())
        stage = "monitor"
        from .monitor_capture import begin_turn
        begin_turn(service, snapshot, user_message)
        stage = "explicit_command"
        receipt = None
        if service.config["explicit_commands"]["enabled"]:
            try:
                command = parse(user_message)
                if command:
                    receipt = service.explicit(snapshot, command, deadline=deadline)
            except KiokukoError as error:
                receipt = {"ok": False, "error": error.code}
                runtime.record_status(error.code, service)
        stage = "context"
        context = prepare(service, snapshot, user_message, conversation_history, receipt, deadline=deadline)
        return {"context": context}
    except Exception as error:
        code = error.code if isinstance(error, KiokukoError) else (
            "STORE_UNAVAILABLE" if isinstance(error, OSError) else "INTERNAL_ERROR")
        # The host otherwise logs arbitrary exception text and discards the hook result.
        # Keep the cause visible without exposing user text, paths or identity values.
        logger.warning("Kiokuko pre_llm_call failed: stage=%s code=%s", stage, code)
        runtime.record_status(code, service)
        # Host cannot turn failure into delivery success: this has no signed marker.
        return {"context": "KIOKUKO STATUS: " + code + ". Memory context unavailable."}
