"""Observation never changes provider/tool execution, including on failures."""
from types import SimpleNamespace
import time

from . import runtime
from .errors import KiokukoError
from .identity import resolve_identity
from .models import canonical
from .orca_transport import MAX_EVENT

SENSITIVE = {'authorization', 'proxy_authorization', 'cookie', 'set_cookie', 'api_key',
             'x_api_key', 'extra_headers', 'default_headers', 'headers', 'http_client'}


def safe_value(value, depth=0):
    if depth > 20:
        raise KiokukoError('MONITOR_PAYLOAD_LIMIT')
    if isinstance(value, str) and len(value) > MAX_EVENT:
        raise KiokukoError("MONITOR_PAYLOAD_LIMIT")
    if isinstance(value, (dict, list, tuple)) and len(value) > 10000:
        raise KiokukoError("MONITOR_PAYLOAD_LIMIT")
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, dict):
        return {str(k): safe_value(v, depth+1) for k, v in value.items()
                if str(k).lower().replace('-', '_') not in SENSITIVE
                and not str(k).lower().endswith(('_api_key', '_token', '_password', '_secret'))}
    if isinstance(value, (list, tuple)):
        return [safe_value(v, depth+1) for v in value]
    if isinstance(value, SimpleNamespace):
        return safe_value(vars(value), depth+1)
    if hasattr(value, 'model_dump'):
        return safe_value(value.model_dump(mode='json'), depth+1)
    raise KiokukoError('MONITOR_UNSUPPORTED_PAYLOAD')


def payload(value):
    result = safe_value(value)
    if len(canonical(result).encode()) > MAX_EVENT:
        raise KiokukoError('MONITOR_PAYLOAD_LIMIT')
    return result


def current_binding(session_id, turn_id, task_id):
    service = runtime.current()
    if not service.config['monitor']['enabled']:
        return None
    snap = service.get_snapshot(session_id, turn_id)
    live = resolve_identity(service.store, session_id, snap.platform, workspace=False, host_session=True)
    if snap.task_id != task_id or any(getattr(snap, k) != getattr(live, k) for k in
            ('platform', 'origin', 'principal_id', 'conversation_id', 'chat_type')):
        raise KiokukoError('MONITOR_IDENTITY_MISMATCH')
    if snap.origin not in {'cli', 'cli_user', 'dm', 'group_chat'}:
        raise KiokukoError('MONITOR_ORIGIN_UNSUPPORTED')
    from .monitor import get_manager
    manager = get_manager(service)
    return manager, snap


def begin_turn(service, snap, raw):
    if not service.config['monitor']['enabled']:
        return
    try:
        from .monitor import get_manager
        get_manager(service).begin(snap, payload({'text': raw}))
    except Exception:
        runtime.record_status('MONITOR_CAPTURE_MISSING', service)


def observe(kind, request, next_call, *, session_id='', turn_id='', task_id='',
            api_request_id='', model='', provider='', api_mode='', tool_name='', **kwargs):
    binding = None
    observation = None
    try:
        binding = current_binding(session_id, turn_id, task_id)
        if binding:
            manager, snap = binding
            if kind == 'model' and api_mode not in {'chat_completions', 'chat_completion', 'codex_responses', ''}:
                raise KiokukoError('MONITOR_API_UNSUPPORTED')
            observation = manager.next_observation(snap)
            attrs = {'observation': observation, 'request_id': str(api_request_id)[:256],
                     'model': str(model)[:256], 'provider': str(provider)[:128],
                     'api_mode': str(api_mode)[:64], 'tool': tool_name}
            manager.append(snap, kind + ('.request' if kind == 'model' else '.call'),
                           'agent', payload(request), attrs)
    except Exception as error:
        if binding:
            code = error.code if isinstance(error, KiokukoError) else 'MONITOR_CAPTURE_MISSING'
            binding[0].drop(binding[1], code)
        else:
            runtime.record_status('MONITOR_CAPTURE_MISSING')
    started = time.monotonic()
    try:
        result = next_call(request)
    except BaseException as error:
        observed_error = error
        # Hermes wraps downstream failures to preserve them across middleware frames.
        while type(observed_error).__name__ == "_DownstreamExecutionError" and isinstance(getattr(observed_error, "original", None), BaseException):
            observed_error = observed_error.original
        if binding and observation is not None:
            try:
                binding[0].append(binding[1], 'error', 'harness',
                    payload({'error_type': type(observed_error).__name__, 'status_code': getattr(observed_error, 'status_code', None),
                             'body': getattr(observed_error, 'body', None)}),
                    {'observation': observation, 'duration_ms': (time.monotonic()-started)*1000})
            except Exception:
                binding[0].drop(binding[1], 'MONITOR_CAPTURE_MISSING')
            if isinstance(observed_error, (InterruptedError, KeyboardInterrupt)) or type(observed_error).__name__ == 'CancelledError':
                binding[0].abort(binding[1])
        raise
    if binding and observation is not None:
        try:
            binding[0].append(binding[1], kind + ('.response' if kind == 'model' else '.result'),
                'model' if kind == 'model' else 'tool', payload(result),
                {'observation': observation, 'duration_ms': (time.monotonic()-started)*1000,
                 'tool': tool_name})
        except Exception:
            binding[0].drop(binding[1], 'MONITOR_CAPTURE_MISSING')
    return result


def llm_execution_middleware(*, request, next_call, **kwargs):
    return observe('model', request, next_call, **kwargs)


def capture_tool_middleware(*, tool_name, args, next_call, **kwargs):
    # Memory tool output must never become new evidence for its own contents.
    if tool_name.startswith('kiokuko_') or (tool_name == 'tool_call' and isinstance(args, dict) and str(args.get('name', '')).startswith('kiokuko_')):
        return next_call(args)
    return observe('tool', args, next_call, tool_name=tool_name, **kwargs)
