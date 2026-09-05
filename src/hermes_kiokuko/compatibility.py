import inspect
from pathlib import Path
import re
import sys

from .config import load_config, validate_native
from .errors import KiokukoError

AUDITED_SHA = "13e72fb205b735df679e0fd5f5996a34ac4accc6"


def active_home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()).resolve()


def check_host(home: Path | None = None, *, require_config=True) -> None:
    try:
        import hermes_cli
        from agent.memory_provider import MemoryProvider
        from agent import turn_context
        from agent.auxiliary_client import call_llm
        from gateway import session_context
        from hermes_cli.middleware import run_tool_execution_middleware
        from hermes_cli.plugins import PluginContext
        import yaml  # host-owned dependency, checked before activating any surface
        match = re.fullmatch(r"0\.21\.\d+(?:[-+][\w.]+)?", hermes_cli.__version__)
        if not match or not (3, 11) <= sys.version_info[:2] < (3, 14):
            raise KiokukoError("UNSUPPORTED_HERMES")
        if "messages" not in inspect.signature(MemoryProvider.sync_turn).parameters or \
                "rewound" not in inspect.signature(MemoryProvider.on_session_switch).parameters:
            raise KiokukoError("HOST_CONTRACT_MISMATCH")
        if not all(callable(getattr(PluginContext, name, None)) for name in
                   ("register_hook", "register_middleware", "register_tool", "register_cli_command")):
            raise KiokukoError("HOST_CONTRACT_MISMATCH")
        if not callable(run_tool_execution_middleware) or not callable(turn_context.compose_user_api_content):
            raise KiokukoError("HOST_CONTRACT_MISMATCH")
        if not {"task", "messages", "timeout"} <= set(inspect.signature(call_llm).parameters):
            raise KiokukoError("HOST_CONTRACT_MISMATCH")
        if "HERMES_SESSION_ID" not in session_context._VAR_MAP:
            raise KiokukoError("HOST_CONTRACT_MISMATCH")
        current = active_home()
        if home is not None and Path(home).resolve() != current:
            raise KiokukoError("PROFILE_IDENTITY_MISMATCH")
        if require_config:
            validate_native(current)
            load_config(current)
    except (ImportError, AttributeError, TypeError, ValueError):
        raise KiokukoError("HOST_CONTRACT_MISMATCH") from None


def surface_is_compatible_and_selected() -> bool:
    try:
        check_host()
        return True
    except (KiokukoError, OSError):
        return False
