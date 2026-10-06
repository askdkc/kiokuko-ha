from types import SimpleNamespace
import importlib
from pathlib import Path
import tomllib
import pytest
from packaging.specifiers import SpecifierSet

from hermes_kiokuko.compatibility import host_contract_report, check_host
from hermes_kiokuko.errors import KiokukoError


@pytest.fixture
def modules(monkeypatch):
    def sync(self, user_content, assistant_response, messages=None):
        pass
    def switch(self, session_id, rewound=False):
        pass
    def llm(task, messages, timeout):
        pass
    noop = lambda *args, **kwargs: None
    provider = SimpleNamespace(sync_turn=sync, on_session_switch=switch)
    context = SimpleNamespace(**{name:noop for name in ('register_hook','register_middleware','register_tool','register_cli_command','register_command','register_skill')})
    values = {'hermes_cli':SimpleNamespace(__version__='0.21.4'),
              'hermes_cli.version_info':SimpleNamespace(get_version_info=lambda: SimpleNamespace(base_version='unknown')),
              'agent.memory_provider':SimpleNamespace(MemoryProvider=provider),
              'agent.turn_context':SimpleNamespace(compose_user_api_content=noop),
              'agent.auxiliary_client':SimpleNamespace(call_llm=llm),
              'gateway.session_context':SimpleNamespace(_VAR_MAP={'HERMES_SESSION_ID':object()}),
              'hermes_cli.middleware':SimpleNamespace(run_tool_execution_middleware=noop),
              'hermes_cli.plugins':SimpleNamespace(PluginContext=context),
              'hermes_constants':SimpleNamespace(get_hermes_home=noop), 'yaml':SimpleNamespace()}
    def load(name):
        value = values[name]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(importlib, 'import_module', load)
    return values


def test_compatible_contract(modules):
    assert host_contract_report()['ok']


@pytest.mark.parametrize('python, supported', [
    ((3, 10), False), ((3, 11), True), ((3, 12), True),
    ((3, 13), True), ((3, 14), True), ((3, 15), False), ((4, 0), False),
])
def test_python_support_matches_package_installation(modules, monkeypatch, python, supported):
    from hermes_kiokuko import compatibility
    monkeypatch.setattr(compatibility.sys, 'version_info', (*python, 7, 'final', 0))
    result = host_contract_report()
    assert result['ok'] is supported
    if not supported:
        assert result['error'] == 'UNSUPPORTED_HERMES'
        with pytest.raises(KiokukoError, match='UNSUPPORTED_HERMES'):
            check_host(require_config=False)
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    assert ('.'.join(map(str, python)) in SpecifierSet(project['requires-python'])) is supported


@pytest.mark.parametrize('python, version, supported', [
    ((3, 13), '0.21.0', True), ((3, 13), '0.21.3', True),
    ((3, 14), '0.21.0', False), ((3, 14), '0.21.3', False),
    ((3, 14), '0.21.4', True), ((3, 14), '0.21.99', True),
    ((3, 14), '0.22.0', False),
])
def test_python314_requires_host_with_compatible_executor(modules, monkeypatch, python, version, supported):
    from hermes_kiokuko import compatibility
    monkeypatch.setattr(compatibility.sys, 'version_info', (*python, 7, 'final', 0))
    modules['hermes_cli'].__version__ = version
    assert host_contract_report()['ok'] is supported
    if not supported:
        with pytest.raises(KiokukoError, match='UNSUPPORTED_HERMES'):
            check_host(require_config=False)


@pytest.mark.parametrize('name', ['hermes_cli','agent.memory_provider','agent.auxiliary_client'])
def test_missing_module_identified_without_exception_text(modules, name):
    modules[name] = ModuleNotFoundError('credential secret must never be shown', name=name)
    result = host_contract_report()
    failure = next(c for c in result['failed_checks'] if c['name']=='import:'+name)
    assert failure['missing_module'] == name and result['error'] == 'HOST_CONTRACT_MISMATCH'
    assert 'credential' not in str(result)
    with pytest.raises(KiokukoError, match='HOST_CONTRACT_MISMATCH'):
        check_host(require_config=False)


def test_missing_skill_api_identified_and_still_rejected(modules):
    del modules['hermes_cli.plugins'].PluginContext.register_skill
    result = host_contract_report()
    assert result['failed_checks'] == [{'name':'hermes_cli.plugins.PluginContext.register_skill','ok':False}]
    with pytest.raises(KiokukoError, match='HOST_CONTRACT_MISMATCH'):
        check_host(require_config=False)


def test_changed_signature_shows_actual_and_required_parameters(modules):
    modules['agent.memory_provider'].MemoryProvider.sync_turn = lambda self, text: None
    check = host_contract_report()['failed_checks'][0]
    assert check['required_parameters'] == ['messages']
    assert check['actual_parameters'] == ['self','text']


def test_unsupported_version_keeps_existing_failure_code(modules):
    modules['hermes_cli'].__version__ = '0.22.0'
    assert host_contract_report()['error'] == 'UNSUPPORTED_HERMES'


def test_api_success_does_not_claim_gateway_dispatch_verified(modules):
    result = host_contract_report()
    assert result['ok'] and result['scope'] == 'import_and_api_checks'
    assert result['dispatch_contract']['status'] == 'unverified'
    assert result['dispatch_contract']['runtime_exercised'] is False


@pytest.mark.parametrize('version', ['0.0.0', '0.21.99'])
def test_unstamped_and_untested_hosts_are_identified(modules, version):
    modules['hermes_cli'].__version__ = version
    result = host_contract_report()
    assert result['ok'] is (version != '0.0.0')
    assert result['dispatch_contract']['status'] == 'unverified'


def test_current_host_uses_canonical_identity_not_placeholder(modules):
    modules['hermes_cli'].__version__ = '0.0.0'
    modules['hermes_cli.version_info'].get_version_info = lambda: SimpleNamespace(base_version='0.21.5')
    result = host_contract_report()
    assert result['ok'] and result['hermes_version'] == '0.21.5'


def test_unstamped_archive_requires_exact_audited_sources(modules, monkeypatch, tmp_path):
    from hermes_kiokuko import compatibility
    monkeypatch.setattr(compatibility.sys, 'version_info', (3, 14, 7, 'final', 0))
    modules['hermes_cli'].__version__ = '0.0.0'
    modules['hermes_cli'].__file__ = str(tmp_path / 'hermes_cli' / '__init__.py')
    import hashlib
    source = tmp_path / 'dispatch.py'
    source.write_text('audited fixture')
    monkeypatch.setitem(compatibility.AUDITED_DISPATCH_SOURCES, '0.0.0', {
        'commit': 'fixture', 'files': {'dispatch.py': hashlib.sha256(source.read_bytes()).hexdigest()}})
    assert host_contract_report()['ok']
    source.write_text('changed source')
    assert host_contract_report()['error'] == 'UNSUPPORTED_HERMES'
