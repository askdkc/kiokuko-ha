import hashlib
import importlib.util
import inspect
from pathlib import Path
import re
import sys
import tomllib

from .config import load_config, validate_native
from .errors import KiokukoError

AUDITED_SHA = "13e72fb205b735df679e0fd5f5996a34ac4accc6"


AUDITED_DISPATCH_SOURCES = {'0.21.0': {'commit': '13e72fb205b735df679e0fd5f5996a34ac4accc6',
            'files': {'gateway/run_inbound.py': '8b983ea50a991d5ec8512062f8baea73a3e4a9aec8a6093fe7b19004f0f7d45c',
                      'gateway/run.py': '0ac228aa6f766474013565e24aa65d87cea992f5e665e4de2bb5782e5fd3848e',
                      'hermes_cli/plugins.py': '546c692e3f251bc8ef6920c0c1cfc94a26a892b4076c45aa75616e85dc48680b',
                      'cli.py': '1acf08b2002d1e9bba58360986d53a7f1f7d6724a79b6dbe09c77fc74a160151'}},
 '0.21.4': {'commit': 'e794bb31cb75d2dc8937f928e736fb73155e674c',
            'files': {'gateway/run_inbound.py': '5e66d8b1b7c6c28e471e84b51c57a55415fa30c3df3df5701ddc65a3b8a6b386',
                      'gateway/run.py': 'acb8e4ed5b675ce49c12ebd1014aa034f746068a4fde23275c29e6e77b29e69c',
                      'hermes_cli/plugins.py': '78b57210ab2aadaacac11ac11a1112e252872fc8021e0d692f8ad1566bdb2922',
                      'cli.py': '4a1710806d415804e8eeecb01e49e3c72231bb060d6fe202b84cb3263460ae69'}}}


def _prepare_host_imports() -> None:
    """Expose new root modules omitted by an older Hermes editable mapping.

    Use the installed package's source tree, never CWD or HERMES_HOME. Missing
    source/dependencies remain errors; we do not supply a replacement YAML API.
    """
    try:
        spec = importlib.util.find_spec('hermes_cli')
    except (ImportError, ValueError, AttributeError):
        return
    if spec is None or not spec.origin:
        return
    package = Path(spec.origin).resolve()
    if package.name != '__init__.py' or package.parent.name != 'hermes_cli':
        return
    root = package.parent.parent
    if str(root) in sys.path:
        return
    module = root / 'hermes_yaml.py'
    if not module.is_file() or module.resolve().parent != root:
        return
    try:
        with (root / 'pyproject.toml').open('rb') as source:
            project = tomllib.load(source).get('project', {})
    except (OSError, ValueError):
        return
    if not isinstance(project, dict) or project.get('name') != 'hermes-agent':
        return
    sys.path.append(str(root))
    importlib.invalidate_caches()


def active_home() -> Path:
    _prepare_host_imports()
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()).resolve()


def host_contract_report():
    """Report the exact import/API checks without dumping arbitrary exception text."""
    import importlib
    _prepare_host_imports()
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
            'checks': checks, 'failed_checks': failed,
            'scope': 'import_and_api_checks',
            'dispatch_contract': dispatch_contract_report(modules.get('hermes_cli'), version)}


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


def dispatch_contract_report(hermes_module, version):
    """Identify tested fixture sources, without executing a command in doctor."""
    audited = AUDITED_DISPATCH_SOURCES.get(version)
    matched = False
    module_path = getattr(hermes_module, '__file__', None)
    if audited and module_path:
        root = Path(module_path).resolve().parent.parent
        try:
            matched = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
                          for name, digest in audited['files'].items())
        except OSError:
            pass
    return {
        'status': 'tested_fixture_source_match' if matched else 'unverified',
        'commit': audited['commit'] if matched else None,
        'runtime_exercised': False,
        'scope': 'source_fingerprint_only',
        'required': 'sync handler coroutine awaited on origin Gateway task; CLI entry remains synchronous',
        'note': 'API checks do not guarantee Gateway delivery. Doctor does not send commands or run updates.',
    }
