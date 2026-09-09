"""Real plugin registration and interactive CLI dispatch for monitor controls."""
import asyncio
from types import SimpleNamespace

import pytest

from hermes_kiokuko.config import load_config, setup
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.store import Store


@pytest.fixture
def monitor_slash(host, monkeypatch):
    import cli as host_cli
    from hermes_cli.plugins import get_plugin_command_handler
    home, manager = host
    Store(home, initialize=True).close()
    cli = object.__new__(host_cli.HermesCLI)
    cli.session_id = 'monitor-cli-session'
    cli._agent_running = False
    cli.config = {}
    manager._cli_ref = cli
    output = []
    monkeypatch.setattr(host_cli, '_cprint', lambda value: output.append(str(value)))
    monkeypatch.setattr(host_cli, '_ensure_skill_commands', lambda: {})
    monkeypatch.setattr(host_cli, 'get_skill_bundles', lambda: {})
    handler = get_plugin_command_handler('kiokuko-monitor')
    assert handler is not None
    def run(args=''):
        output.clear()
        assert cli.process_command('/kiokuko-monitor ' + args)
        return '\n'.join(output)
    try:
        yield home, cli, handler, run
    finally:
        manager._cli_ref = None


def test_monitor_slash_toggles_only_current_profile(monitor_slash, tmp_path):
    home, _, _, run = monitor_slash
    other = tmp_path / 'other-profile'
    setup(other)
    before = (other / 'kiokuko/config.yaml').read_bytes()
    assert '無効' in run()
    assert not load_config(home)['monitor']['enabled']
    assert '有効' in run('enable')
    assert load_config(home)['monitor']['enabled']
    text = run('status')
    assert '完了 0件' in text and '未実行' in text
    assert 'api_requests' not in text and 'retention_days' not in text
    assert '無効' in run('disable')
    assert not load_config(home)['monitor']['enabled']
    assert (other / 'kiokuko/config.yaml').read_bytes() == before


def test_monitor_slash_node_failure_and_invalid_args_do_not_enable(monitor_slash, monkeypatch):
    home, _, _, run = monitor_slash
    def missing():
        raise KiokukoError('NODE_MISSING')
    monkeypatch.setattr('hermes_kiokuko.monitor_cli.runtime_check', missing)
    assert 'NODE_MISSING' in run('enable')
    assert not load_config(home)['monitor']['enabled']
    for args in ('enable extra', 'toggle', 'help'):
        assert '/kiokuko-monitor enable' in run(args)
        assert not load_config(home)['monitor']['enabled']


@pytest.mark.parametrize('context', ['running', 'missing-cli', 'gateway', 'wrong-session', 'delegated', 'background'])
def test_monitor_slash_denies_non_admin_paths(monitor_slash, monkeypatch, context):
    home, cli, handler, _ = monitor_slash
    from gateway import session_context as sc
    tokens = []
    if context == 'running':
        cli._agent_running = True
    elif context == 'missing-cli':
        handler.ctx._manager._cli_ref = None
    elif context == 'gateway':
        tokens = sc.set_session_vars(platform='photon', chat_type='dm', chat_id='chat', user_id='sender')
    elif context == 'wrong-session':
        tokens = sc.set_session_vars(platform='cli', session_id='different-session')
    elif context == 'delegated':
        monkeypatch.setattr('agent.delegation_context.is_delegated_child_context', lambda: True)
    else:
        monkeypatch.setattr('tools.skill_provenance.get_current_write_origin', lambda: 'background_review')
    try:
        assert '対話CLIで実行' in handler('enable')
        assert not load_config(home)['monitor']['enabled']
    finally:
        sc.clear_session_vars(tokens)


def test_monitor_gateway_dispatch_without_sender_context_denied(monitor_slash):
    home, _, _, _ = monitor_slash
    from gateway.run_inbound import GatewayInboundMixin
    runner = SimpleNamespace(_draining=False, _hm_quick_commands=lambda: {})
    event = SimpleNamespace(get_command_args=lambda: 'enable')
    handled, result, _ = asyncio.run(GatewayInboundMixin._hm_dispatch_quick_and_plugin_commands(
        runner, event, None, 'kiokuko_monitor'))
    assert handled and '対話CLIで実行' in result
    assert not load_config(home)['monitor']['enabled']


def test_monitor_disable_releases_active_recorder(monitor_slash):
    home, _, _, run = monitor_slash
    from hermes_kiokuko import runtime
    from hermes_kiokuko.monitor import get_manager
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    run('enable')
    provider = KiokukoMemoryProvider()
    provider.initialize('monitor-cli-session', hermes_home=str(home))
    try:
        manager = get_manager(runtime.current())
        assert not manager.stopping.is_set()
        assert '無効' in run('disable')
        assert manager.stopping.is_set() and not manager.writer.is_alive()
        assert not runtime.current().config['monitor']['enabled']
    finally:
        provider.shutdown()
