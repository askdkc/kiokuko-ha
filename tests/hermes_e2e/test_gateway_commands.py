"""Actual host hook dispatch and slash policy; only transport and pip are fake."""
import asyncio
from types import SimpleNamespace

import pytest

from hermes_kiokuko.config import load_config
from hermes_kiokuko.store import Store


@pytest.fixture(params=["discord", "telegram", "bluebubbles"])
def gateway_commands(host, monkeypatch, request):
    from gateway.run import GatewayRunner
    from gateway.config import Platform, PlatformConfig, GatewayConfig
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
    runner.config = GatewayConfig(platforms={platform: PlatformConfig(extra=extra)})
    replies, authenticated = [], []

    async def send(chat_id, text, **kwargs):
        replies.append((chat_id, text, kwargs))
        return SimpleNamespace(success=True)

    def authorize(source):
        authenticated.append(source.user_id)
        return source.user_id != 'outsider'

    monkeypatch.setattr(runner, '_is_user_authorized', authorize)
    adapter_method = '_adapter_for_source' if hasattr(runner, '_adapter_for_source') else '_delivery_adapter_for'
    monkeypatch.setattr(runner, adapter_method, lambda source: SimpleNamespace(send=send))

    def event(text, *, user='owner', chat='channel', thread='thread', chat_type='group', **kwargs):
        return MessageEvent(text=text, message_id='123', source=SessionSource(
            platform=platform, user_id=user, chat_id=chat, thread_id=thread,
            chat_type=chat_type), **kwargs)

    async def bind(message):
        import inspect
        result = runner._hm_pre_gateway_dispatch_hook(message, message.source)
        return await result if inspect.isawaitable(result) else result

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

    yield SimpleNamespace(home=home, manager=manager, runner=runner, event=event,
                           dispatch=dispatch, bind=bind, extra=extra, authenticated=authenticated,
                           replies=replies, platform=platform.value)
    executor = getattr(runner, "_executor", None)
    if executor is not None:
        executor.shutdown(wait=True)


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
        await g.bind(event)
        args = 'enable'
        if case == 'wrong-args':
            args = 'disable'
        elif case == 'next-event':
            other = g.event('ordinary chat')
            await g.bind(other)
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


def test_registered_sync_handler_returns_to_origin_task_from_executor(gateway_commands):
    """The new host copies context into its executor then awaits on the origin task."""
    from hermes_cli.plugins import get_plugin_command_handler
    from hermes_kiokuko.gateway_commands import _pending
    g = gateway_commands
    handler = get_plugin_command_handler('kiokuko-update')

    async def scenario():
        event = g.event('/kiokuko-update help')
        await g.bind(event)
        envelope = _pending.get()
        result = await asyncio.to_thread(handler, 'help')
        assert asyncio.iscoroutine(result)
        assert _pending.get() is envelope
        assert '結果確認' in await result
    asyncio.run(scenario())


@pytest.mark.parametrize('case', ['child-await', 'executor-await', 'different-ctx',
                                 'wrong-command', 'next-event', 'copied-replay', 'duplicate'])
def test_deferred_gateway_envelope_rejects_theft_and_replay(gateway_commands, case):
    from contextvars import copy_context
    from hermes_cli.plugins import get_plugin_command_handler
    from hermes_kiokuko.gateway_commands import dispatch
    g = gateway_commands
    handler = get_plugin_command_handler('kiokuko-monitor')

    async def scenario():
        event = g.event('/kiokuko-monitor enable')
        await g.bind(event)
        copied = copy_context()
        result = await asyncio.to_thread(handler, 'enable')
        if case == 'child-await':
            reply = await asyncio.create_task(result)
        elif case == 'executor-await':
            reply = await asyncio.to_thread(asyncio.run, result)
        elif case == 'different-ctx':
            result.close()
            reply = await dispatch(object(), 'kiokuko-monitor', 'enable')
        elif case == 'wrong-command':
            result.close()
            reply = await dispatch(handler.ctx, 'kiokuko-update', 'enable')
        elif case == 'next-event':
            await g.bind(g.event('/kiokuko-monitor disable'))
            reply = await result
        else:
            assert '有効' in await result
            result = copied.run(handler, 'enable') if case == 'copied-replay' else handler('enable')
            if not asyncio.iscoroutine(result):
                assert '対話CLI' in result
                return
            reply = await result
        assert 'GATEWAY_CONTEXT_MISMATCH' in reply
        assert load_config(g.home)['monitor']['enabled'] is (case in {'copied-replay', 'duplicate'})
    asyncio.run(scenario())


@pytest.mark.parametrize('case', ['outsider', 'member', 'hook', 'slash', 'internal', 'control', 'draining',
                                 'delegated', 'background'])
def test_gateway_update_denials_never_spawn_pip(gateway_commands, monkeypatch, case):
    import hermes_kiokuko.slash_update as update
    g = gateway_commands
    monkeypatch.setattr(update, '_job', None)
    calls = []
    monkeypatch.setattr(update.subprocess, 'run', lambda *a, **kw: calls.append(a))
    kwargs = {}
    if case in {'outsider', 'member'}:
        kwargs['user'] = case
    elif case == 'hook':
        g.runner.hooks._handlers['command:kiokuko-update'] = [
            lambda *_: {'decision': 'deny', 'message': 'Blocked by operator hook'}]
    elif case == 'slash':
        g.extra['group_user_allowed_commands'] = ['kiokuko-monitor']
        kwargs['user'] = 'member'
    elif case == 'internal':
        kwargs['internal'] = True
    elif case == 'control':
        kwargs['allow_gateway_control'] = False
    elif case == 'draining':
        g.runner._draining = True
    elif case == 'delegated':
        monkeypatch.setattr('agent.delegation_context.is_delegated_child_context', lambda: True)
    else:
        monkeypatch.setattr('tools.skill_provenance.get_current_write_origin', lambda: 'background_review')
    asyncio.run(g.dispatch(g.event('/kiokuko-update', **kwargs)))
    assert update._job is None and calls == []


@pytest.mark.parametrize('text', ['/kiokuko-update help', '/kiokuko_update help',
                                  '/kiokuko_update@KiokukoBot help', '  /kiokuko-update help  '])
def test_host_parser_help_does_not_start_update(gateway_commands, monkeypatch, text):
    import hermes_kiokuko.slash_update as update
    monkeypatch.setattr(update, '_job', None)
    g = gateway_commands
    _, replies = asyncio.run(g.dispatch(g.event(text)))
    assert len(replies) == 1 and '結果確認' in replies[0][1]
    assert update._job is None


def test_cli_update_four_operations_through_process_command(host, monkeypatch):
    import cli as host_cli
    import hermes_kiokuko.slash_update as update
    home, manager = host
    cli = object.__new__(host_cli.HermesCLI)
    cli.session_id = 'update-cli'
    cli._agent_running = False
    cli.config = {}
    manager._cli_ref = cli
    output, starts = [], []
    monkeypatch.setattr(host_cli, '_cprint', lambda value: output.append(str(value)))
    monkeypatch.setattr(host_cli, '_ensure_skill_commands', lambda: {})
    monkeypatch.setattr(host_cli, 'get_skill_bundles', lambda: {})
    monkeypatch.setattr(update, '_job', None)
    monkeypatch.setattr(update.threading.Thread, 'start',
                        lambda thread: (starts.append(thread), __import__('os').close(thread._args[2])))
    try:
        for action in ['status', 'help', '', 'retry']:
            output.clear()
            assert cli.process_command('/kiokuko-update ' + action)
            assert output and '管理者を確認できません' not in str(output)
        assert len(starts) == 1
        update._job.state = 'failed'
        assert cli.process_command('/kiokuko-update retry')
        assert len(starts) == 2
    finally:
        manager._cli_ref = None


def test_cancelled_receiver_keeps_update_job_observable_and_unlocks(gateway_commands, monkeypatch, tmp_path):
    import os
    import threading
    import hermes_kiokuko.slash_update as update
    from hermes_cli.plugins import get_plugin_command_handler
    from hermes_kiokuko.gateway_commands import GatewayCommands
    from hermes_kiokuko.filesystem import acquire_lock
    g = gateway_commands
    monkeypatch.setattr(update, '_job', None)
    monkeypatch.setattr(update.sys, 'prefix', str(tmp_path))
    started, release, returning = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def worker(job, env, fd):
        try:
            calls.append(job)
            started.set()
            release.wait(5)
            with update._lock:
                job.state, job.version = 'complete', job.previous
        finally:
            os.close(fd)
    original = GatewayCommands._execute
    def slow_reply(handler, home, args):
        result = original(handler, home, args)
        returning.set()
        release.wait(5)
        return result
    monkeypatch.setattr(update, 'perform_update', worker)
    monkeypatch.setattr(GatewayCommands, '_execute', staticmethod(slow_reply))
    handler = get_plugin_command_handler('kiokuko-update')
    async def receive():
        await g.bind(g.event('/kiokuko-update'))
        return await handler('')
    async def scenario():
        task = asyncio.create_task(receive())
        try:
            assert await asyncio.to_thread(returning.wait, 3)
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert '更新中' in handler.execute(g.home, 'status')
            assert '更新中' in handler.execute(g.home, 'retry')
            assert len(calls) == 1
        finally:
            release.set()
            for thread in threading.enumerate():
                if thread.name == 'kiokuko-update':
                    await asyncio.to_thread(thread.join, 3)
        assert '完了' in handler.execute(g.home, 'status')
        assert '完了' in handler.execute(g.home, 'retry')
        assert len(calls) == 1
        os.close(acquire_lock(tmp_path / '.kiokuko-update.lock', exclusive=True, timeout=0))
    asyncio.run(scenario())
