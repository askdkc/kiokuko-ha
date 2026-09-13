"""Actual host hook dispatch and slash policy; only transport and pip are fake."""
import asyncio
from types import SimpleNamespace

import pytest

from hermes_kiokuko.config import load_config
from hermes_kiokuko.store import Store


@pytest.fixture(params=["discord", "telegram", "bluebubbles"])
def gateway_commands(host, monkeypatch, request):
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent
    from gateway.session import SessionSource
    home, manager = host
    Store(home, initialize=True).close()
    runner = object.__new__(GatewayRunner)
    runner._draining = False
    from gateway.hooks import HookRegistry
    runner.hooks = HookRegistry()
    monkeypatch.setattr(runner, "_scale_to_zero_note_real_inbound", lambda: None)
    extra = {'allow_admin_from': ['owner'], 'group_allow_admin_from': ['owner']}
    platform = Platform(request.param)
    runner.config = SimpleNamespace(platforms={platform: SimpleNamespace(extra=extra)})
    replies, authenticated = [], []

    async def send(chat_id, text, **kwargs):
        replies.append((chat_id, text, kwargs))
        return SimpleNamespace(success=True)

    def authorize(source):
        authenticated.append(source.user_id)
        return source.user_id != 'outsider'

    monkeypatch.setattr(runner, '_is_user_authorized', authorize)
    monkeypatch.setattr(runner, '_adapter_for_source', lambda source: SimpleNamespace(send=send))

    def event(text, *, user='owner', chat='channel', thread='thread', chat_type='group', **kwargs):
        return MessageEvent(text=text, message_id='123', source=SessionSource(
            platform=platform, user_id=user, chat_id=chat, thread_id=thread,
            chat_type=chat_type), **kwargs)

    async def dispatch(message):
        previous = len(replies)
        if message.internal or not message.allow_gateway_control:
            await runner._hm_admit_event(message)
            return message, []
        text = await runner._handle_message(message)
        if text is None:
            return message, replies[previous:]
        source = message.source
        if text is not None:
            from gateway.platforms.base import _reply_anchor_for_event
            anchor = _reply_anchor_for_event(message)
            await send(source.chat_id, text, reply_to=anchor,
                       metadata=runner._thread_metadata_for_source(source, anchor))
        result = None
        return result, replies[previous:]

    return SimpleNamespace(home=home, manager=manager, runner=runner, event=event,
                           dispatch=dispatch, extra=extra, authenticated=authenticated,
                           replies=replies, platform=platform.value)


def test_chat_monitor_status_enable_disable_and_thread_reply(gateway_commands):
    g = gateway_commands

    async def scenario():
        for command, enabled in [('/kiokuko-monitor', False), ('/kiokuko-monitor enable', True),
                                 ('/kiokuko_monitor status', True), ('/kiokuko-monitor disable', False)]:
            event, replies = await g.dispatch(g.event(command))
            assert event is None and len(replies) == 1
            chat, text, route = replies[0]
            assert ('有効' if enabled else '無効') in text
            assert '対話CLI' not in text and 'Unknown command' not in text
            expected_anchor = None if g.platform == 'telegram' else '123'
            assert chat == 'channel' and route['reply_to'] == expected_anchor
            assert route['metadata']['thread_id'] == 'thread'
            assert load_config(g.home)['monitor']['enabled'] is enabled
    asyncio.run(scenario())


@pytest.mark.parametrize('command', ['/kiokuko-monitor', '/kiokuko-monitor enable',
                                     '/kiokuko-monitor disable', '/kiokuko-update', '/kiokuko-update status'])
def test_gateway_non_admin_is_denied_by_host_policy(gateway_commands, command):
    g = gateway_commands
    event, replies = asyncio.run(g.dispatch(g.event(command, user='member')))
    assert event is None and 'admin-only' in replies[0][1]
    assert g.authenticated and set(g.authenticated) == {'member'}
    assert not load_config(g.home)['monitor']['enabled']


@pytest.mark.parametrize('kwargs', [{'user': 'outsider'}, {'internal': True},
                                  {'allow_gateway_control': False}])
def test_untrusted_events_never_execute_or_reply(gateway_commands, kwargs):
    g = gateway_commands
    original = g.event('/kiokuko-monitor enable', **kwargs)
    event, replies = asyncio.run(g.dispatch(original))
    assert event is original and not replies
    assert not load_config(g.home)['monitor']['enabled']


def test_policy_scope_and_explicit_command_allowance(gateway_commands):
    g = gateway_commands
    g.extra['allow_admin_from'] = ['dm-owner']
    g.extra['group_user_allowed_commands'] = ['kiokuko-monitor']

    async def scenario():
        _, replies = await g.dispatch(g.event('/kiokuko-monitor enable', user='member'))
        assert '有効' in replies[0][1]
        _, replies = await g.dispatch(g.event('/kiokuko-update', user='member'))
        assert 'admin-only' in replies[0][1]
        g.extra.pop('group_user_allowed_commands')
        _, replies = await g.dispatch(g.event('/kiokuko-monitor disable', user='dm-owner'))
        assert 'admin-only' in replies[0][1]
        _, replies = await g.dispatch(g.event('/kiokuko-monitor disable', user='dm-owner', chat_type='dm'))
        assert '無効' in replies[0][1]
    asyncio.run(scenario())


def test_existing_ungated_host_policy_and_draining(gateway_commands):
    g = gateway_commands
    g.extra.clear()

    async def scenario():
        _, replies = await g.dispatch(g.event('/kiokuko-monitor enable', user='member'))
        assert '有効' in replies[0][1]
        g.runner._draining = True
        _, replies = await g.dispatch(g.event('/kiokuko-monitor disable', user='member'))
        assert 'shutting down' in replies[0][1]
        assert load_config(g.home)['monitor']['enabled']
    asyncio.run(scenario())


def test_host_command_hook_denial_prevents_monitor_changes(gateway_commands):
    g = gateway_commands
    g.runner.hooks._handlers['command:kiokuko-monitor'] = [
        lambda *_: {'decision': 'deny', 'message': 'Blocked by operator hook'}]
    _, replies = asyncio.run(g.dispatch(g.event('/kiokuko-monitor enable')))
    assert replies[0][1] == 'Blocked by operator hook'
    assert not load_config(g.home)['monitor']['enabled']


@pytest.mark.parametrize('case', ['child-task', 'wrong-args', 'next-event', 'wrong-profile'])
def test_gateway_binding_cannot_be_borrowed(gateway_commands, case, tmp_path):
    import inspect
    from hermes_cli.plugins import get_plugin_command_handler
    g = gateway_commands
    handler = get_plugin_command_handler('kiokuko-monitor')

    async def scenario():
        event = g.event('/kiokuko-monitor enable')
        g.runner._hm_pre_gateway_dispatch_hook(event, event.source)
        args = 'enable'
        if case == 'wrong-args':
            args = 'disable'
        elif case == 'next-event':
            other = g.event('ordinary chat')
            g.runner._hm_pre_gateway_dispatch_hook(other, other.source)
        elif case == 'wrong-profile':
            g.manager.home_path = tmp_path / 'other'

        async def call():
            reply = handler(args)
            return await reply if inspect.isawaitable(reply) else reply

        reply = await asyncio.create_task(call()) if case == 'child-task' else await call()
        assert any(word in reply for word in ('対話CLI', 'MISMATCH'))
        assert not load_config(g.home)['monitor']['enabled']
    asyncio.run(scenario())


def test_gateway_binding_is_consumed_once(gateway_commands):
    from hermes_cli.plugins import get_plugin_command_handler
    g = gateway_commands
    handler = get_plugin_command_handler('kiokuko-monitor')

    async def scenario():
        _, replies = await g.dispatch(g.event('/kiokuko-monitor enable'))
        assert '有効' in replies[0][1]
        assert '対話CLI' in handler('disable')
        assert load_config(g.home)['monitor']['enabled']
    asyncio.run(scenario())


def test_concurrent_gateway_commands_keep_their_arguments_and_senders(gateway_commands):
    g = gateway_commands

    async def scenario():
        await asyncio.gather(g.dispatch(g.event('/kiokuko-monitor enable', chat='a')),
                             g.dispatch(g.event('/kiokuko-monitor disable', user='member', chat='b')))
        replies = {chat: text for chat, text, _ in g.replies}
        assert '有効' in replies['a']
        assert 'admin-only' in replies['b']
        assert load_config(g.home)['monitor']['enabled']
    asyncio.run(scenario())


def test_gateway_update_start_and_status(gateway_commands, monkeypatch, tmp_path):
    import hermes_kiokuko.slash_update as update
    import threading
    import tomllib
    from pathlib import Path
    g = gateway_commands
    release = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text())['project']['version']
    finished = threading.Event()
    calls = []
    monkeypatch.setattr(update, '_job', None)
    monkeypatch.setattr(update.sys, 'prefix', str(tmp_path / 'venv'))
    (tmp_path / 'venv').mkdir()

    def pip(argv, **kwargs):
        calls.append((argv, kwargs))
        if 'install' in argv:
            return SimpleNamespace(returncode=0)
        finished.set()
        return SimpleNamespace(stdout='9.9\n')

    monkeypatch.setattr(update.subprocess, 'run', pip)

    async def scenario():
        _, replies = await g.dispatch(g.event('/kiokuko-update status'))
        assert 'まだ更新を実行していません' in replies[0][1] and not calls
        _, replies = await g.dispatch(g.event('/kiokuko-update'))
        assert '/kiokuko-update status' in replies[0][1]
        assert await asyncio.to_thread(finished.wait, 5)
        # The worker publishes completion under this lock after checking pip's version.
        for thread in threading.enumerate():
            if thread.name == 'kiokuko-update':
                await asyncio.to_thread(thread.join, 5)
        _, replies = await g.dispatch(g.event('/kiokuko-update status'))
        assert '完了' in replies[0][1] and '9.9' in replies[0][1]
        assert f'このprocessの読込時: {release}' in replies[0][1]
        assert len(calls) == 2
        argv, options = calls[0]
        assert argv[0] == update.sys.executable and argv[-1] == 'hermes-kiokuko'
        assert options['env']['HERMES_HOME'] == str(g.home)
        assert options['cwd'] == str(g.home)
    asyncio.run(scenario())
