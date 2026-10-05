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


def host_contract_report():
    """Report the exact import/API checks without dumping arbitrary exception text."""
    import importlib
    modules = {}
    checks = []
    names = ('hermes_cli', 'agent.memory_provider', 'agent.turn_context',
             'agent.auxiliary_client', 'gateway.session_context',
             'hermes_cli.middleware', 'hermes_cli.plugins', 'hermes_constants', 'yaml')
    for name in names:
        try:
            module = importlib.import_module(name)
            modules[name] = module
            checks.append({'name': 'import:' + name, 'ok': True,
                           'path': getattr(module, '__file__', None)})
        except (ImportError, AttributeError, TypeError, ValueError) as error:
            detail = {'name': 'import:' + name, 'ok': False,
                      'reason': type(error).__name__}
            missing = getattr(error, 'name', None)
            if isinstance(missing, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*', missing):
                detail['missing_module'] = missing
            checks.append(detail)
    version = getattr(modules.get('hermes_cli'), '__version__', None)
    supported = (isinstance(version, str) and bool(re.fullmatch(r"0\.21\.\d+(?:[-+][\w.]+)?", version))
                 and (3, 11) <= sys.version_info[:2] < (3, 14))
    checks.append({'name': 'supported_version', 'ok': bool(supported),
                   'hermes_version': version, 'python_version': list(sys.version_info[:2])})
    contracts = [
        ('agent.memory_provider', 'MemoryProvider.sync_turn', {'messages'}),
        ('agent.memory_provider', 'MemoryProvider.on_session_switch', {'rewound'}),
        ('agent.turn_context', 'compose_user_api_content', None),
        ('agent.auxiliary_client', 'call_llm', {'task', 'messages', 'timeout'}),
        ('hermes_cli.middleware', 'run_tool_execution_middleware', None),
        *[('hermes_cli.plugins', 'PluginContext.' + name, None) for name in
          ('register_hook', 'register_middleware', 'register_tool', 'register_cli_command', 'register_command', 'register_skill')],
        ('hermes_constants', 'get_hermes_home', None),
    ]
    for module_name, attribute, required in contracts:
        value = modules.get(module_name)
        for part in attribute.split('.'):
            value = getattr(value, part, None)
        check = {'name': module_name + '.' + attribute, 'ok': callable(value)}
        if required is not None and callable(value):
            try:
                parameters = set(inspect.signature(value).parameters)
                check.update(ok=required <= parameters, required_parameters=sorted(required),
                             actual_parameters=sorted(parameters))
            except (TypeError, ValueError):
                check.update(ok=False, reason='SIGNATURE_UNAVAILABLE')
        checks.append(check)
    variables = getattr(modules.get('gateway.session_context'), '_VAR_MAP', None)
    checks.append({'name': 'gateway.session_context.HERMES_SESSION_ID',
                   'ok': isinstance(variables, dict) and 'HERMES_SESSION_ID' in variables})
    failed = [c for c in checks if not c['ok']]
    # Preserve established failure codes. A failed import is not proof of an
    # unsupported version; the version is simply unknown in that case.
    code = ('UNSUPPORTED_HERMES' if modules.get('hermes_cli') is not None and not supported
            else 'HOST_CONTRACT_MISMATCH') if failed else None
    return {'ok': not failed, 'error': code, 'hermes_version': version,
            'checks': checks, 'failed_checks': failed}


def check_host(home: Path | None = None, *, require_config=True) -> None:
    report = host_contract_report()
    if not report['ok']:
        raise KiokukoError(report['error'])
    try:
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
