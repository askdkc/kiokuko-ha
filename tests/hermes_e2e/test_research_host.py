import asyncio
from hermes_kiokuko.config import load_config, write_yaml
from hermes_kiokuko.models import ExplicitCommand
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store
from test_gateway_commands import gateway_commands


def test_packaged_skill_namespace_loaded(host):
    home, manager = host
    assert 'kiokuko-tools:memory-reasoning' in manager._plugin_skills
    from tools.skills_tool import skill_view
    result = skill_view('kiokuko-tools:memory-reasoning')
    assert 'Preserve the current' in str(result)


def test_authenticated_buffered_research_and_duplicate_event(gateway_commands, monkeypatch):
    g = gateway_commands
    calls = []
    def run(home, request, memories, **kwargs):
        calls.append((request, memories))
        return '検査済みの回答'
    monkeypatch.setattr('hermes_kiokuko.research.run', run)
    async def scenario():
        _, replies = await g.dispatch(g.event('/kiokuko-research 出典付きで確認して'))
        assert replies[0][1] == '検査済みの回答'
        await g.dispatch(g.event('/kiokuko-research 出典付きで確認して'))
        assert len(calls) == 1
        _, replies = await g.dispatch(g.event('/kiokuko-research 変更した内容'))
        assert 'CONFLICT' in replies[0][1] and len(calls) == 1
        _, replies = await g.dispatch(g.event('/kiokuko-research 出典付きで確認して', user='outsider'))
        assert not replies and len(calls) == 1
    asyncio.run(scenario())


def test_auto_routing_retains_auth_and_failure_is_closed(gateway_commands, monkeypatch):
    g = gateway_commands
    cfg = load_config(g.home); cfg['research']['mode'] = 'auto'
    write_yaml(g.home / 'kiokuko' / 'config.yaml', cfg)
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise ValueError('private candidate must not appear')
    monkeypatch.setattr('hermes_kiokuko.research.run', fail)
    async def scenario():
        _, replies = await g.dispatch(g.event('最新情報を調べて'))
        assert '未確認' in replies[0][1] and 'private' not in replies[0][1]
        _, replies = await g.dispatch(g.event('最新情報を調べて', user='outsider'))
        assert not replies and len(calls) == 1
    asyncio.run(scenario())


def test_gateway_status_reports_loaded_runtime(gateway_commands):
    import json
    g = gateway_commands
    _, replies = asyncio.run(g.dispatch(g.event('/kiokuko-research status')))
    info = json.loads(replies[0][1])
    assert info['gateway_loaded'] is True and info['host_ready']


def test_cancelled_gateway_result_not_replayed(gateway_commands, monkeypatch):
    import threading
    from hermes_kiokuko.slash_research import SlashResearch
    g = gateway_commands
    started, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        started.set(); release.wait(3)
        return 'late unchecked output'
    monkeypatch.setattr('hermes_kiokuko.research.run', slow)
    async def scenario():
        task = asyncio.create_task(g.dispatch(g.event('/kiokuko-research 出典を調べて')))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        release.set()
        await asyncio.sleep(.1)
        _, replies = await g.dispatch(g.event('/kiokuko-research 出典を調べて'))
        assert 'late' not in replies[0][1] and '中断' in replies[0][1]
    asyncio.run(scenario())


def test_research_route_is_main_model_without_stream_or_fallback(host, monkeypatch):
    from types import SimpleNamespace
    from openai import OpenAI
    from hermes_kiokuko.config import read_yaml
    from hermes_kiokuko.research import synthesize
    import agent.auxiliary_client as aux
    home, _ = host
    cfg = read_yaml(home/'config.yaml')
    cfg['model'] = {'provider':'openrouter','default':'chosen-model'}
    cfg['auxiliary']['compression'] = {'provider':'other','model':'wrong-model'}
    write_yaml(home/'config.yaml', cfg)
    client = OpenAI(api_key='fake-local-test-only')
    calls = []
    def resolve(provider, **kwargs):
        calls.append((provider, kwargs))
        return client, kwargs['model']
    monkeypatch.setattr(aux, 'resolve_provider_client', resolve)
    monkeypatch.setattr(client, 'with_options', lambda **kwargs:client)
    def create(**kwargs):
        assert kwargs['stream'] is False and kwargs['model'] == 'chosen-model'
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{"claims":[],"unknowns":["unknown"],"premises":[]}'))])
    monkeypatch.setattr(client.chat.completions, 'create', create)
    assert synthesize(home, {'request':'test'})['unknowns'] == ['unknown']
    assert len(calls) == 1 and calls[0][0] == 'openrouter'


def test_scoped_memory_reaches_research_model(gateway_commands, monkeypatch):
    from hermes_kiokuko.identity import opaque
    from hermes_kiokuko.models import Identity
    g = gateway_commands
    store = Store(g.home)
    try:
        service = Service(store)
        platform = g.platform
        owner = opaque(store.key, 'principal', platform, 'user_id', 'owner')
        other = opaque(store.key, 'principal', platform, 'user_id', 'someone-else')
        conversation = opaque(store.key, 'conversation', platform, 'channel', 'thread')
        for principal, claim in [(owner,'sentinel rule 日本語'),(other,'foreign sentinel 日本語')]:
            who = Identity(platform,'dm',principal,conversation,None,'dm')
            snap = service.snapshot(principal, 'save', claim, who)
            service.explicit(snap, ExplicitCommand('remember',claim,'principal'))
    finally:
        store.close()
    def run(home, request, memories, **kwargs):
        assert any('sentinel rule' in m['text'] for m in memories)
        assert not any('foreign' in m['text'] for m in memories)
        return 'scope verified'
    monkeypatch.setattr('hermes_kiokuko.research.run', run)
    _, replies = asyncio.run(g.dispatch(g.event('/kiokuko-research 日本語を調べて', chat_type='dm')))
    assert replies[0][1] == 'scope verified'
