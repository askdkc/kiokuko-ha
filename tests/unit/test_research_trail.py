"""Source chasing must establish evidence, not promote search snippets."""
import json

import pytest


REQUEST = 'Trace the original field experiment and its measured result'
ROOT = 'https://example.org/overview'
ORIGINAL = 'https://example.org/original'
QUOTE = 'We measured a reduction of 20 percent in the field experiment.'
BODY = 'Original experiment by its authors.\n' + QUOTE


def planner(home, payload):
    if payload['stage'] == 'plan':
        return {'claims': [REQUEST, 'Who conducted the experiment?', 'What was measured?'],
                'routes': [{'query': REQUEST, 'angle': 'terminology'},
                           {'query': 'field experiment original authors report', 'angle': 'authors'}]}
    sources = payload['sources']
    original = next((s for s in sources if s['url'] == ORIGINAL), None)
    overview = next((s for s in sources if s['url'] == ROOT), None)
    return {'map': {'terms': [], 'people': [], 'venues': []},
            'evidence': ([{'claim_id': f'C{i}', 'source_id': original['id'], 'quote': QUOTE,
                           'kind': 'original', 'primary_basis': 'Original experiment by its authors.'}
                          for i in range(1, 4)] if original else []),
            'leads': ([{'source_id': overview['id'], 'relation': 'reference',
                       'target': ORIGINAL, 'quote': ORIGINAL}] if overview else []),
            'unknowns': []}


def backends(*, blocked=False, cycle=False):
    searches, fetches = [], []
    def search(query, limit=3):
        searches.append(query)
        return json.dumps({'success': True, 'data': {'web': [{'url': ROOT, 'title': 'Overview'}]}})
    def extract(urls):
        fetches.extend(urls)
        return {'results': [({'url': u, 'error': '403 forbidden'} if blocked and u == ORIGINAL else
                            {'url': u, 'content': ('Overview references ' + ORIGINAL if u == ROOT else
                                                  BODY + ('\nSee ' + ROOT if cycle else ''))}) for u in urls]}
    return search, extract, searches, fetches


def test_follows_secondary_to_original_with_locations(tmp_path):
    from hermes_kiokuko.research_trail import explore
    search, extract, searches, fetches = backends()
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=planner)
    assert result['stop_reason'] == 'evidence_covered'
    assert len(searches) == 2 and len(set(searches)) == 2
    assert fetches == [ROOT, ORIGINAL]
    assert len(result['claims']) == 3
    assert not result['unresolved']
    assert result['evidence'][0]['line'] == 2
    assert result['evidence'][0]['end_line'] == 2
    assert any(e['from'] == 'S1' and e['target'] == ORIGINAL for e in result['edges'])


@pytest.mark.parametrize('blocked', [False, True])
def test_missing_and_unreachable_primary_remain_unresolved(tmp_path, blocked):
    from hermes_kiokuko.research_trail import explore
    search, extract, searches, fetches = backends(blocked=blocked)
    def unknown(home, payload):
        result = planner(home, payload)
        if payload['stage'] != 'plan':
            result['evidence'] = []
            if not blocked:
                result['leads'] = []
        return result
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=unknown)
    assert result['stop_reason'] != 'evidence_covered'
    assert len(result['unresolved']) == 3 and not result['evidence']
    if blocked:
        assert any(f['url'] == ORIGINAL for f in result['failures'])
    assert len(searches) <= 6 and len(fetches) == len(set(fetches))


def test_secondary_mislabeled_or_forged_quotes_cannot_cover_claim(tmp_path):
    from hermes_kiokuko.research_trail import explore
    search, extract, _, _ = backends()
    def forged(home, payload):
        result = planner(home, payload)
        if payload['stage'] != 'plan':
            for e in result['evidence']:
                e['kind'] = 'secondary'
        return result
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=forged)
    assert not result['evidence'] and len(result['unresolved']) == 3
    def invented(home, payload):
        result = planner(home, payload)
        if payload['stage'] != 'plan':
            for e in result['evidence']:
                e['quote'] = 'This was invented by the model.'
        return result
    assert not explore(tmp_path, REQUEST, search=search, extract=extract, model=invented)['evidence']


def test_cycle_duplicate_queries_and_final_review_are_bounded(tmp_path):
    from hermes_kiokuko.research_trail import explore
    search, extract, searches, fetches = backends(cycle=True)
    def cyclic(home, payload):
        result = planner(home, payload)
        if payload['stage'] != 'plan':
            result['evidence'] = []
            for source in payload['sources']:
                if source['url'] == ORIGINAL:
                    result['leads'].append({'source_id': source['id'], 'relation': 'reference',
                                           'target': ROOT + '#again', 'quote': ROOT})
                    result['leads'].append({'source_id': source['id'], 'relation': 'term',
                                           'target': REQUEST, 'quote': 'field experiment'})
        return result
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=cyclic)
    assert result['stop_reason'] == 'saturated'
    assert len(searches) == 2 and fetches == [ROOT, ORIGINAL]
    assert result['rounds'] <= 4


def test_final_review_can_remove_apparently_complete_evidence(tmp_path):
    from hermes_kiokuko.research_trail import explore
    search, extract, _, _ = backends()
    def review(home, payload):
        result = planner(home, payload)
        if payload['stage'] == 'review_trail':
            result['evidence'] = []
        return result
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=review)
    assert result['stop_reason'] != 'evidence_covered'
    assert len(result['unresolved']) == 3


def test_cancellation_stops_before_next_backend(tmp_path):
    import threading
    from hermes_kiokuko.errors import KiokukoError
    from hermes_kiokuko.research_trail import explore
    flag = threading.Event()
    search, extract, searches, fetches = backends()
    def stop(query, limit=3):
        value = search(query, limit)
        flag.set()
        return value
    with pytest.raises(KiokukoError, match='RESEARCH_CANCELLED'):
        explore(tmp_path, REQUEST, search=stop, extract=extract, model=planner, cancelled=flag)
    assert len(searches) == 1 and fetches == []


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://127.0.0.1/private',
    'http://[::1]/', 'http://10.0.0.1/', 'https://localhost/a', 'https://a.internal/b',
    'https://user:password@example.org/a', 'https://example.org/a?api_key=secret'])
def test_private_and_secret_urls_are_not_eligible(url):
    from hermes_kiokuko.research_trail import public_url
    assert public_url(url) is None


@pytest.mark.parametrize('quote', ['', '…', ' ... ', 'invented unsupported excerpt'])
def test_empty_ellipsis_and_invented_quotes_cannot_match(quote):
    from hermes_kiokuko.research_trail import excerpt
    assert excerpt({'content': BODY}, quote) is None


def test_redirect_and_url_alias_deduplication(tmp_path):
    from hermes_kiokuko.research_trail import explore
    fetched = []
    def search(query, limit=3):
        return {'success': True, 'data': {'web': [{'url': ROOT + '#fragment'}, {'url': ROOT + '?utm_source=test'}]}}
    def extract(urls):
        fetched.extend(urls)
        # Actual Hermes shape: no invented final_url metadata survives trimming.
        return {'results': [{'url': ORIGINAL, 'content': BODY} for u in urls]}
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=planner)
    assert fetched == [ROOT]
    assert result['evidence'] and result['sources'][0]['url'] == ORIGINAL
    assert result['edges'][0]['relation'] == 'redirect'


def test_unsafe_observed_redirect_is_not_primary_evidence(tmp_path):
    from hermes_kiokuko.research_trail import explore
    search, _, _, _ = backends()
    result = explore(tmp_path, REQUEST, search=search, model=planner,
        extract=lambda urls: {'results': [{'url': 'http://127.0.0.1/private', 'content': BODY}]})
    assert not result['sources'] and not result['evidence']
    assert result['failures'] and len(result['unresolved']) == 3


def test_timeout_after_fetch_discards_unreviewed_evidence(tmp_path, monkeypatch):
    from hermes_kiokuko import research_trail as trail
    now = [100.]
    monkeypatch.setattr(trail.time, 'monotonic', lambda: now[0])
    search, extract, _, _ = backends()
    def slow(urls):
        value = extract(urls)
        now[0] += 50
        return value
    result = trail.explore(tmp_path, REQUEST, search=search, extract=slow, model=planner)
    assert result['stop_reason'] == 'time_limit'
    assert not result['evidence'] and len(result['unresolved']) == 3


def test_fresh_queries_and_infinite_chain_hit_hard_budgets(tmp_path):
    from hermes_kiokuko.research_trail import explore
    searches, fetched = [], []
    def search(query, limit=3):
        searches.append(query)
        return {'success': True, 'data': {'web': [{'url': f'https://example.org/page/{len(searches)}'}]}}
    def extract(urls):
        fetched.extend(urls)
        return {'results': [{'url': u, 'content': 'An unconfirmed report.\nNewAuthor from a new field described this work.'} for u in urls]}
    def infinite(home, payload):
        result = planner(home, payload)
        if payload['stage'] != 'plan':
            result['evidence'] = []
            result['leads'] = [{'source_id': 'S1', 'relation': 'author', 'quote': 'NewAuthor',
                                'target': f'NewAuthor new report round {payload.get("round", 9)} query {i}'} for i in range(4)]
        return result
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=infinite)
    assert len(searches) == 6 and len(fetched) <= 12
    assert len(set(fetched)) == len(fetched)
    assert result['stop_reason'] in {'search_limit', 'round_limit', 'source_limit'}
    assert len(result['unresolved']) == 3


def test_plan_must_keep_original_conditions_and_two_distinct_routes(tmp_path):
    from hermes_kiokuko.research_trail import explore
    from hermes_kiokuko.errors import KiokukoError
    search, extract, searches, fetched = backends()
    def invalid(home, payload):
        result = planner(home, payload)
        result['claims'][0] = 'A simpler question without the original conditions'
        return result
    with pytest.raises(KiokukoError, match='RESEARCH_INVALID_PLAN'):
        explore(tmp_path, REQUEST, search=search, extract=extract, model=invalid)
    assert searches == fetched == []


def test_single_fact_uses_lookup_budget_without_unnecessary_branching(tmp_path):
    from hermes_kiokuko.research_trail import explore
    search, extract, searches, fetched = backends()
    def lookup(home, payload):
        if payload['stage'] == 'plan':
            return {'mode': 'lookup', 'claims': [REQUEST], 'routes': [{'query': REQUEST, 'angle': 'official'}]}
        result = planner(home, payload)
        result['evidence'] = [e for e in result['evidence'] if e['claim_id'] == 'C1']
        return result
    result = explore(tmp_path, REQUEST, search=search, extract=extract, model=lookup)
    assert result['scope'] == 'lookup' and len(result['claims']) == 1
    assert len(searches) == 1 and len(fetched) == 2 and result['rounds'] <= 2
    assert result['stop_reason'] == 'evidence_covered'
