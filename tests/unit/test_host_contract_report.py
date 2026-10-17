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
    import yaml
    from contextvars import copy_context
    class Manager:
        def add_provider(self, provider):
            self.provider = provider
        def sync_all(self, user, assistant, *, session_id='', messages=None):
            copy_context().run(self.provider.sync_turn, user, assistant,
                               session_id=session_id, messages=messages)
        def flush_pending(self, timeout=None):
            return True
        def shutdown_all(self):
            pass
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
              'agent.memory_manager':SimpleNamespace(MemoryManager=Manager),
              'agent.turn_context':SimpleNamespace(compose_user_api_content=noop),
              'agent.auxiliary_client':SimpleNamespace(call_llm=llm),
              'gateway.session_context':SimpleNamespace(_VAR_MAP={'HERMES_SESSION_ID':object()}),
              'hermes_cli.middleware':SimpleNamespace(run_tool_execution_middleware=noop),
              'hermes_cli.plugins':SimpleNamespace(PluginContext=context),
              'hermes_constants':SimpleNamespace(get_hermes_home=noop),
              'yaml':SimpleNamespace(safe_load=yaml.safe_load, safe_dump=yaml.safe_dump, YAMLError=yaml.YAMLError)}
    def load(name):
        value = values[name]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(importlib, 'import_module', load)
    return values


def test_compatible_contract(modules):
    assert host_contract_report()['ok']


@pytest.mark.parametrize('version', ['0.19.0', '0.20.9', '0.22.1', '0.23.0', '0.99.123', '1.0.0', '0.0.0', 'development', None])
def test_compatible_apis_do_not_depend_on_release_metadata(modules, version):
    modules['hermes_cli'].__version__ = version
    report = host_contract_report()
    assert report['ok'], report['failed_checks']


@pytest.mark.parametrize('version', [[], {}, ['0', '22'], {'major':0}, object()])
def test_unknown_metadata_shapes_are_only_diagnostic(modules, version):
    modules['hermes_cli'].__version__ = version
    report = host_contract_report()
    assert report['ok'], report['failed_checks']
    assert report['hermes_version'] is None


@pytest.mark.parametrize('version', [[], {}, SimpleNamespace(major=0)])
def test_unknown_canonical_metadata_preserves_capability_admission(modules, version):
    modules['hermes_cli'].__version__ = '0.0.0'
    modules['hermes_cli.version_info'].get_version_info = lambda: SimpleNamespace(base_version=version)
    report = host_contract_report()
    assert report['ok'], report['failed_checks']
    assert report['hermes_version'] == '0.0.0'


@pytest.mark.parametrize('python', [(3, 11), (3, 12), (3, 14), (3, 15), (3, 99), (4, 0)])
def test_python_metadata_does_not_reject_working_runtime(modules, monkeypatch, python):
    from hermes_kiokuko import compatibility
    monkeypatch.setattr(compatibility.sys, 'version_info', (*python, 7, 'final', 0))
    result = host_contract_report()
    assert result['ok'], result['failed_checks']
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    assert '.'.join(map(str, python)) in SpecifierSet(project['requires-python'])


@pytest.mark.parametrize('version', ['5.1', '5.4.1', '6.0.3', '7.0', '99.0'])
def test_yaml_declaration_matches_safe_dump_sort_keys_api(version):
    from packaging.requirements import Requirement
    project = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text())['project']
    dependency = next(Requirement(d) for d in project['dependencies'] if Requirement(d).name.lower() == 'pyyaml')
    assert version in dependency.specifier
    assert '5.0' not in dependency.specifier  # sort_keys was added in the 5.1 API.
    test_dependency = next(Requirement(d) for d in project['optional-dependencies']['test']
                           if Requirement(d).name.lower() == 'pyyaml')
    assert dependency.specifier == test_dependency.specifier


def test_build_floor_matches_used_pep639_metadata():
    from packaging.requirements import Requirement
    project = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text())
    backend = next(Requirement(d) for d in project['build-system']['requires']
                   if Requirement(d).name.lower() == 'setuptools')
    assert isinstance(project['project']['license'], str) and project['project']['license-files']
    assert '76.0' not in backend.specifier  # These fields require the PEP 639 API.
    assert '77.0' in backend.specifier and '99.0' in backend.specifier


def test_test_floor_matches_builtin_pythonpath_configuration():
    from packaging.requirements import Requirement
    project = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text())
    dependency = next(Requirement(d) for d in project['project']['optional-dependencies']['test']
                      if Requirement(d).name.lower() == 'pytest')
    assert project['tool']['pytest']['ini_options']['pythonpath']
    assert '7.0' in dependency.specifier and '99.0' in dependency.specifier
    assert '6.2' not in dependency.specifier


@pytest.mark.parametrize('version', ['5.1', '5.4.1', '6.0.3', '99.0', 'unknown', None])
def test_yaml_metadata_uses_safe_operations(modules, version):
    modules['yaml'].__version__ = version
    assert host_contract_report()['ok']


@pytest.mark.parametrize('api', ['load', 'loads'])
def test_missing_toml_operation_rejected(modules, monkeypatch, api):
    from hermes_kiokuko import compatibility
    operations = SimpleNamespace(load=compatibility.tomllib.load, loads=compatibility.tomllib.loads)
    delattr(operations, api)
    monkeypatch.setattr(compatibility, 'tomllib', operations)
    report = host_contract_report()
    assert report['error'] == 'HOST_CONTRACT_MISMATCH'
    assert any(c['name'] == 'python.tomllib.parse' and not c['ok'] for c in report['failed_checks'])


@pytest.mark.parametrize('api', ['safe_load', 'safe_dump', 'YAMLError'])
def test_yaml_missing_api_rejected(modules, api):
    modules['yaml'].__version__ = '6.0.3'  # A known label cannot replace the API.
    delattr(modules['yaml'], api)
    report = host_contract_report()
    assert report['error'] == 'HOST_CONTRACT_MISMATCH'
    assert any(c['name'] == 'yaml.' + api and not c['ok'] for c in report['failed_checks'])


@pytest.mark.parametrize('broken', ['roundtrip', 'unsafe_tags', 'types', 'dump_type'])
def test_yaml_broken_behavior_rejected(modules, broken):
    load = modules['yaml'].safe_load
    def changed(text):
        if broken == 'roundtrip' or (broken == 'unsafe_tags' and text.startswith('!!')):
            return {}
        value = load(text)
        if broken == 'types':
            value['enabled'] = 1  # Python equality must not confuse 1 and True.
        return value
    modules['yaml'].safe_load = changed
    if broken == 'dump_type':
        dump = modules['yaml'].safe_dump
        modules['yaml'].safe_dump = lambda *a, **k: dump(*a, **k).encode()
    report = host_contract_report()
    assert report['error'] == 'HOST_CONTRACT_MISMATCH'
    assert any(c['name'] == 'yaml.safe_roundtrip' and not c['ok'] for c in report['failed_checks'])


@pytest.mark.parametrize('failure,reason', [('raise', 'AttributeError'),
    ('drop', 'SYNC_CALLBACK_NOT_OBSERVED'), ('context', 'SYNC_PAYLOAD_OR_CONTEXT_MISMATCH')])
def test_sync_failure_rejected_by_behavior_not_version(modules, monkeypatch, failure, reason):
    from contextvars import Context
    manager = modules['agent.memory_manager'].MemoryManager
    def broken(self, user, assistant, *, session_id='', messages=None):
        if failure == 'raise':
            raise AttributeError('private executor API missing; secret must not be shown')
        if failure == 'context':
            Context().run(self.provider.sync_turn, user, assistant,
                          session_id=session_id, messages=messages)
    monkeypatch.setattr(manager, 'sync_all', broken)
    result = host_contract_report()
    assert result['error'] == 'HOST_CONTRACT_MISMATCH'
    assert result['failed_checks'] == [{'name': 'agent.memory_manager.sync_roundtrip',
                                       'ok': False, 'reason': reason}]
    assert 'secret' not in str(result)
    with pytest.raises(KiokukoError, match='HOST_CONTRACT_MISMATCH'):
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


def test_required_keywords_cannot_be_positional_only(modules):
    def changed(self, user, assistant, messages=None, /):
        pass
    modules['agent.memory_provider'].MemoryProvider.sync_turn = changed
    assert host_contract_report()['failed_checks'][0]['name'] == 'agent.memory_provider.MemoryProvider.sync_turn'


def test_keyword_compatible_wrapper_is_supported(modules):
    modules['agent.memory_provider'].MemoryProvider.sync_turn = lambda self, user, assistant, **kwargs: None
    assert host_contract_report()['ok']


@pytest.mark.parametrize('version', ['0.21.4', '0.22.1', '1.99.0', '0.0.0'])
def test_missing_api_rejected_regardless_of_release_metadata(modules, version):
    modules['hermes_cli'].__version__ = version
    del modules['hermes_cli.plugins'].PluginContext.register_middleware
    result = host_contract_report()
    assert result['error'] == 'HOST_CONTRACT_MISMATCH'
    assert result['failed_checks'] == [
        {'name': 'hermes_cli.plugins.PluginContext.register_middleware', 'ok': False}]


def test_api_success_does_not_claim_gateway_dispatch_verified(modules):
    result = host_contract_report()
    assert result['ok'] and result['scope'] == 'import_api_and_sync_probe'
    assert result['dispatch_contract']['status'] == 'unverified'
    assert result['dispatch_contract']['runtime_exercised'] is False


@pytest.mark.parametrize('version', ['0.0.0', '0.21.99'])
def test_unstamped_and_untested_hosts_are_identified(modules, version):
    modules['hermes_cli'].__version__ = version
    result = host_contract_report()
    assert result['ok']
    assert result['dispatch_contract']['status'] == 'unverified'


def test_current_host_uses_canonical_identity_not_placeholder(modules):
    modules['hermes_cli'].__version__ = '0.0.0'
    modules['hermes_cli.version_info'].get_version_info = lambda: SimpleNamespace(base_version='0.21.5')
    result = host_contract_report()
    assert result['ok'] and result['hermes_version'] == '0.21.5'


def test_source_fingerprint_is_diagnostic_not_admission(modules, monkeypatch, tmp_path):
    from hermes_kiokuko import compatibility
    monkeypatch.setattr(compatibility.sys, 'version_info', (3, 14, 7, 'final', 0))
    modules['hermes_cli'].__version__ = '0.0.0'
    modules['hermes_cli'].__file__ = str(tmp_path / 'hermes_cli' / '__init__.py')
    import hashlib
    source = tmp_path / 'dispatch.py'
    source.write_text('audited fixture')
    monkeypatch.setitem(compatibility.AUDITED_DISPATCH_SOURCES, '0.0.0', {
        'commit': 'fixture', 'files': {'dispatch.py': hashlib.sha256(source.read_bytes()).hexdigest()}})
    report = host_contract_report()
    assert report['ok'] and report['dispatch_contract']['status'] == 'tested_fixture_source_match'
    source.write_text('changed source')
    report = host_contract_report()
    assert report['ok'] and report['dispatch_contract']['status'] == 'unverified'


def test_sync_probe_reuses_implementation_and_rechecks_changed_methods(modules, monkeypatch):
    from hermes_kiokuko.compatibility import _memory_sync_probe
    _memory_sync_probe.cache_clear()
    assert host_contract_report()['ok']
    modules['hermes_cli'].__version__ = '99.99.99'
    assert host_contract_report()['ok']
    assert _memory_sync_probe.cache_info().misses == 1
    manager = modules['agent.memory_manager'].MemoryManager
    monkeypatch.setattr(manager, 'flush_pending', lambda self, timeout=None: False)
    assert not host_contract_report()['ok']
    assert _memory_sync_probe.cache_info().misses == 2
