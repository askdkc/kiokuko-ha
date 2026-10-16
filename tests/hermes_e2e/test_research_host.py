import asyncio
import json
import pytest
from hermes_kiokuko.config import load_config, write_yaml
from hermes_kiokuko.models import ExplicitCommand
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store
from test_gateway_commands import gateway_commands


@pytest.mark.parametrize('platform', ['cli', 'telegram'])
@pytest.mark.parametrize('bridge', [False, True])
def test_ordinary_search_runs_source_chasing(host, monkeypatch, platform, bridge):
    """Actual Hermes dispatch + plugin middleware; only web/model transports are fake."""
    home, manager = host
    import tools.web_tools as web
    from model_tools import handle_function_call
    overview, original = 'https://example.org/overview', 'https://example.org/original'
    quote = 'We measured the original field experiment result ourselves.'
    searches, fetches = [], []
    observed = []
    def observe(kind, payload, next_call, **context):
        result = next_call(payload)
        observed.append((context.get('tool_name'), result))
        return result
    monkeypatch.setattr('hermes_kiokuko.monitor_capture.observe', observe)
    def search(query, limit=3):
        searches.append(query)
        return json.dumps({'success': True, 'data': {'web': [{'url': overview, 'title': 'Secondary overview'}]}})
    async def extract(urls, **kwargs):
        assert kwargs['format'] == 'markdown' and kwargs['char_limit'] > 4000
        fetches.extend(urls)
        return json.dumps({'results': [{'url': u, 'content': ('x' * 4500 + '\n[Original](/original)' if u == overview else
                                                           'Original report.\n' + quote)} for u in urls]})
    def model(home, payload):
        if payload['stage'] == 'plan':
            return {'claims': [payload['request'], 'What experiment?', 'What result?'],
                    'routes': [{'query': 'experiment terminology', 'angle': 'terms'},
                               {'query': 'experiment original authors', 'angle': 'authors'}]}
        sources = payload['sources']
        primary = next((s for s in sources if s['url'] == original), None)
        return {'map': {}, 'unknowns': [], 'evidence': [
            {'claim_id': f'C{i}', 'source_id': primary['id'], 'quote': quote,
             'kind': 'original', 'primary_basis': 'Original report.'} for i in range(1, 4)] if primary else [],
            'leads': [{'source_id': 'S1', 'relation': 'reference', 'target': '/original', 'quote': '/original'}]}
    monkeypatch.setattr(web, 'web_search_tool', search)
    monkeypatch.setattr(web, 'web_extract_tool', extract)
    monkeypatch.setattr('hermes_kiokuko.research.synthesize', model)
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from agent.turn_context import _collect_pre_llm_call_context
    from types import SimpleNamespace
    session = f'ordinary-{platform}'
    provider = KiokukoMemoryProvider()
    provider.initialize(session, hermes_home=str(home))
    # Set actual Gateway context for the Gateway variant; CLI uses host CLI identity.
    from gateway import session_context as sc
    if platform == 'telegram':
        for key, value in {'HERMES_SESSION_PLATFORM': platform, 'HERMES_SESSION_USER_ID': 'owner',
                           'HERMES_SESSION_CHAT_ID': 'chat', 'HERMES_SESSION_CHAT_TYPE': 'dm',
                           'HERMES_SESSION_ID': session}.items():
            sc._VAR_MAP[key].set(value)
    _collect_pre_llm_call_context(SimpleNamespace(session_id=session, platform=platform, model='test'),
        effective_task_id='task', turn_id='ordinary-turn', original_user_message='Search for the field experiment',
        messages=[{'role': 'user', 'content': 'Search for the field experiment'}], conversation_history=[])
    name, args = 'web_search', {'query': 'field experiment'}
    if bridge:
        name, args = 'tool_call', {'name': name, 'arguments': args}
    def call(turn='ordinary-turn'):
        return json.loads(handle_function_call(name, args, session_id=f'ordinary-{platform}', turn_id=turn,
                                              task_id='task', enabled_toolsets=['web']))
    result = call()
    if bridge and 'error' in result:
        # Current hosts keep web_search directly listed; the bridge must reject
        # it before middleware, and the model's instructed direct retry works.
        from tools.tool_search import is_deferrable_tool_name
        assert not is_deferrable_tool_name('web_search') and not searches
        name, args = 'web_search', {'query': 'field experiment'}
        result = call()
    assert result.get('research', {}).get('method') == 'imozuru', result
    assert result['research']['stop_reason'] == 'evidence_covered'
    assert result['research']['evidence'][0]['line'] == 2
    assert fetches == [overview, original] and len(searches) == 2
    assert searches[0] == 'field experiment'
    assert result['data']['web'][0]['url'] == original
    assert observed and observed[0][0] == 'web_search' and json.loads(observed[0][1]) == result
    assert call() == result and fetches == [overview, original]
    if name == 'web_search':
        args = {'query': 'repeat with another phrasing'}
        assert call() == result and len(searches) == 2
    if platform == 'telegram':
        sc._VAR_MAP['HERMES_SESSION_USER_ID'].set('different-user')
        assert call()['research']['stop_reason'] == 'RESEARCH_CONTEXT_MISMATCH'
    provider.shutdown()


def test_non_search_and_disabled_search_are_untouched(host):
    from hermes_cli.middleware import run_tool_execution_middleware
    home, _ = host
    from hermes_kiokuko.config import load_config, write_yaml
    for tool in ['terminal', 'kiokuko_recall', 'file_search', 'web_extract']:
        result = run_tool_execution_middleware(tool, {'sample': 1}, lambda args: args)
        # Memory middleware separately requires identity; the research path must
        # leave its admission/error behavior alone.
        if tool != 'kiokuko_recall':
            assert result == {'sample': 1}
    cfg = load_config(home); cfg['research']['mode'] = 'off'
    write_yaml(home / 'kiokuko/config.yaml', cfg)
    assert run_tool_execution_middleware('web_search', {'query': 'lookup'}, lambda args: 'normal') == 'normal'


def test_explicit_command_uses_real_orchestrator_and_preserves_unknowns(gateway_commands, monkeypatch):
    g = gateway_commands
    import tools.web_tools as web
    calls = []
    def search(query, limit=3):
        calls.append(query)
        return json.dumps({'success': True, 'data': {'web': [{'url': 'https://example.org/secondary'}]}})
    async def extract(urls, **kwargs):
        return json.dumps({'results': [{'url': u, 'content': 'This overview cites an unknown original report, which is not available.'} for u in urls]})
    def model(home, payload):
        if payload['stage'] == 'plan':
            return {'claims': [payload['request'], 'Original author?', 'Original measurement?'],
                    'routes': [{'query': 'overview terms', 'angle': 'terms'}, {'query': 'original report author', 'angle': 'authors'}]}
        return {'map': {}, 'evidence': [], 'leads': [], 'unknowns': ['Original not identified']}
    monkeypatch.setattr(web, 'web_search_tool', search)
    monkeypatch.setattr(web, 'web_extract_tool', extract)
    monkeypatch.setattr('hermes_kiokuko.research.synthesize', model)
    async def scenario():
        _, replies = await g.dispatch(g.event('/kiokuko-research 原文を確認して'))
        assert 'saturated' in replies[0][1] and replies[0][1].count('未確認') == 3
        assert len(calls) == 2
        await g.dispatch(g.event('/kiokuko-research 原文を確認して', user='outsider'))
        assert len(calls) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['delegation', 'cron', 'unbound'])
def test_background_and_unbound_search_do_not_start_research(host, kind):
    from contextlib import nullcontext
    from agent.delegation_context import delegated_child_context
    from gateway import session_context as sc
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from agent.turn_context import _collect_pre_llm_call_context
    from types import SimpleNamespace
    from hermes_cli.middleware import run_tool_execution_middleware
    home, _ = host
    provider = KiokukoMemoryProvider()
    provider.initialize('background-session', hermes_home=str(home))
    if kind == 'cron':
        sc._VAR_MAP['HERMES_CRON_SESSION'].set('1')
    with delegated_child_context('background-session') if kind == 'delegation' else nullcontext():
        if kind != 'unbound':
            _collect_pre_llm_call_context(SimpleNamespace(session_id='background-session', platform='cli', model='test'),
                effective_task_id='task', turn_id='turn', original_user_message='Search current source',
                messages=[{'role':'user', 'content':'Search current source'}], conversation_history=[])
        result = run_tool_execution_middleware('web_search', {'query': 'source'}, lambda args: 'ordinary',
            session_id='background-session', turn_id='turn', task_id='task')
        assert result == 'ordinary'
    provider.shutdown()


def test_host_stop_interrupt_crosses_worker_and_failure_is_reused(host, monkeypatch):
    import threading
    from types import SimpleNamespace
    from tools import interrupt
    import tools.web_tools as web
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from agent.turn_context import _collect_pre_llm_call_context
    from model_tools import handle_function_call
    home, _ = host
    provider = KiokukoMemoryProvider()
    provider.initialize('interrupt-session', hermes_home=str(home))
    _collect_pre_llm_call_context(SimpleNamespace(session_id='interrupt-session', platform='cli', model='test'),
        effective_task_id='task', turn_id='turn', original_user_message='Search original evidence',
        messages=[{'role': 'user', 'content': 'Search original evidence'}], conversation_history=[])
    caller, searches, stages = threading.get_ident(), [], []
    def search(query, limit=3):
        searches.append(query)
        interrupt.set_interrupt(True, thread_id=caller)
        return json.dumps({'success': True, 'data': {'web': [{'url': 'https://example.org/report'}]}})
    def model(home, payload):
        stages.append(payload['stage'])
        return {'claims': [payload['request'], 'Who?', 'What result?'],
                'routes': [{'query': 'original evidence', 'angle': 'terms'}, {'query': 'original authors', 'angle': 'authors'}]}
    monkeypatch.setattr(web, 'web_search_tool', search)
    monkeypatch.setattr('hermes_kiokuko.research.synthesize', model)
    try:
        result = json.loads(handle_function_call('web_search', {'query': 'original evidence'},
            session_id='interrupt-session', turn_id='turn', task_id='task'))
        assert result['research']['stop_reason'] == 'RESEARCH_CANCELLED'
    finally:
        interrupt.set_interrupt(False, thread_id=caller)
    repeated = json.loads(handle_function_call('web_search', {'query': 'changed words'},
        session_id='interrupt-session', turn_id='turn', task_id='task'))
    assert repeated == result and len(searches) == 1 and stages == ['plan']
    provider.shutdown()


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


@pytest.mark.parametrize('user_request', ['最新情報を調べて', '原文を検索して', 'Search for the original paper'])
def test_auto_routing_retains_auth_and_failure_is_closed(gateway_commands, monkeypatch, user_request):
    g = gateway_commands
    cfg = load_config(g.home); cfg['research']['mode'] = 'auto'
    write_yaml(g.home / 'kiokuko' / 'config.yaml', cfg)
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise ValueError('private candidate must not appear')
    monkeypatch.setattr('hermes_kiokuko.research.run', fail)
    async def scenario():
        _, replies = await g.dispatch(g.event(user_request))
        assert '未確認' in replies[0][1] and 'private' not in replies[0][1]
        _, replies = await g.dispatch(g.event(user_request, user='outsider'))
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
