"""Bounded, evidence-addressed extraction; no model scores establish truth.

Algorithm references (independently implemented): MemTensor/memmy-agent,
98146714aad8569a298cf8692946da8bb28bf7cb, big-turn-span-pipeline.ts and
span-pipeline.ts. No Memmy runtime or shared-namespace policy is imported.
"""
import json
import re
import unicodedata

from .errors import KiokukoError
from .models import canonical, digest
from .security import scan

VERSION = 'experience-v2'
EXTENDED = {'span', 'attempts', 'conditions', 'field_evidence'}


def normalized(value):
    # Preserve case, punctuation, negation and versions in exact identities.
    return unicodedata.normalize('NFC', value).strip()


def command(event):
    if event['type'] != 'tool.call':
        return None
    try:
        value = json.loads(event['text'])
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    args = value.get('arguments', value.get('args', value))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    text = args.get('command', args.get('cmd'))
    if not isinstance(text, str) or not text.strip() or len(text) > 200:
        return None
    return text.strip()


def command_context(event):
    value = json.loads(event['text'])
    args = value.get('arguments', value.get('args', value))
    if isinstance(args, str):
        args = json.loads(args)
    return canonical({k:v for k,v in args.items() if k not in {'command','cmd'}})


def exit_code(event):
    if event['type'] != 'tool.result':
        return None
    try:
        value = json.loads(event['text'])
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    codes = [value[k] for k in ('exit_code', 'exitCode', 'returncode') if k in value]
    if any(type(code) is not int for code in codes):
        return None
    return codes[0] if codes and len(set(codes)) == 1 else None


def fragments(event, cap):
    text = event['text']
    start = 0
    while start < len(text):
        lo, hi = 1, len(text) - start
        while lo < hi:
            mid = (lo + hi + 1) // 2
            part = {**event, 'text': text[start:start+mid], 'start': start, 'end': start+mid}
            if len(canonical([part])) <= cap:
                lo = mid
            else:
                hi = mid - 1
        part = {**event, 'text': text[start:start+lo], 'start': start, 'end': start+lo}
        if len(canonical([part])) > cap:
            raise KiokukoError('EXPERIENCE_EVENT_METADATA_LIMIT')
        yield part
        start += lo


class WindowPlan(list):
    def __init__(self):
        super().__init__()
        self.excluded = []


def windows(events, limit=32000, max_windows=4):
    if not events:
        return []
    # Parallel observations form an interval component: never reorder call/results.
    last = {}
    for i, event in enumerate(events):
        obs = event.get('observation')
        if obs is not None and event['type'] in {'tool.call', 'tool.result', 'error'}:
            last[obs] = i
    units, i = [], 0
    while i < len(events):
        end, j = i, i
        while j <= end:
            end = max(end, last.get(events[j].get('observation'), j))
            j += 1
        group = [part for event in events[i:end+1] for part in fragments(event, limit//3)]
        if len(canonical(group)) <= limit//2:
            units.append(group)
        else:
            units.extend([[part] for part in group])
        i = end + 1
    result, current, previous = WindowPlan(), [], []
    for index, unit in enumerate(units):
        if current and len(canonical(current + unit)) > limit:
            result.append(current)
            if len(result) == max_windows:
                result.excluded = [{'seq':e['seq'],'start':e['start'],'end':e['end']}
                                   for rest in units[index:] for e in rest]
                return result
            current = list(previous)  # overlap one complete unit, or addressed fragment
        current += unit
        previous = unit
        if len(canonical(current)) > limit:
            raise KiokukoError('EXPERIENCE_WINDOW_LIMIT')
    if current:
        result.append(current)
    return result


def verify_ref(ref, evidence):
    if not isinstance(ref, dict) or set(ref) - {'seq', 'quote', 'start'} or not {'seq','quote'} <= set(ref):
        raise KiokukoError('EXPERIENCE_INVALID_EVIDENCE')
    seq, quote = ref['seq'], ref['quote']
    if type(seq) is not int or not isinstance(quote, str) or not 1 <= len(quote) <= 200:
        raise KiokukoError('EXPERIENCE_INVALID_EVIDENCE')
    start = ref.get('start')
    if start is not None and (type(start) is not int or start < 0):
        raise KiokukoError('EXPERIENCE_INVALID_EVIDENCE')
    for source in evidence:
        if source['seq'] != seq:
            continue
        offset = source.get('start', 0)
        if start is None:
            pos = source['text'].find(quote)
        else:
            pos = start - offset
        if pos < 0 or source['text'][pos:pos+len(quote)] != quote:
            continue
        scan(quote, max_chars=200)
        return {**ref, 'start': offset+pos, 'digest': digest(source['text']),
                'type': source['type'], 'tool': source['tool'],
                'observation': source.get('observation')}
    raise KiokukoError('EXPERIENCE_INVALID_EVIDENCE')


def extend_structure(proposal, structure, evidence, refs):
    """Legacy summaries stay readable but cannot be counted as learning evidence."""
    if not EXTENDED <= proposal.keys():
        return structure
    span = proposal['span']
    if not isinstance(span, dict) or set(span) != {'start','end'} or any(type(v) is not int for v in span.values()):
        raise KiokukoError('EXPERIENCE_INVALID_SPAN')
    seqs = {e['seq'] for e in evidence}
    if span['start'] not in seqs or span['end'] not in seqs or span['start'] > span['end']:
        raise KiokukoError('EXPERIENCE_INVALID_SPAN')
    if any(not span['start'] <= ref['seq'] <= span['end'] for ref in refs):
        raise KiokukoError('EXPERIENCE_INVALID_SPAN')
    field_evidence = proposal['field_evidence']
    if not isinstance(field_evidence, dict) or set(field_evidence) != {'situation','action','observation'}:
        raise KiokukoError('EXPERIENCE_FIELD_EVIDENCE')
    grounded = {}
    for field, indices in field_evidence.items():
        if not isinstance(indices, list) or not indices or any(type(n) is not int or not 0 <= n < len(refs) for n in indices):
            raise KiokukoError('EXPERIENCE_FIELD_EVIDENCE')
        selected = [refs[n] for n in indices]
        required = {'situation': {'note','tool.call','tool.result'}, 'action': {'tool.call'}, 'observation': {'tool.result'}}[field]
        if any(r['type'] not in required for r in selected):
            raise KiokukoError('EXPERIENCE_FIELD_EVIDENCE')
        grounded[field] = [r['quote'] for r in selected]
    conditions = proposal['conditions']
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 6:
        raise KiokukoError('EXPERIENCE_CONDITIONS')
    checked_conditions = []
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {'value','evidence'}:
            raise KiokukoError('EXPERIENCE_CONDITIONS')
        value, idx = condition['value'], condition['evidence']
        if type(idx) is not int or not 0 <= idx < len(refs) or refs[idx]['type'] not in {'note','tool.call','tool.result'}:
            raise KiokukoError('EXPERIENCE_CONDITIONS')
        if not isinstance(value, str) or not 2 <= len(value.strip()) <= 120 or value not in refs[idx]['quote']:
            raise KiokukoError('EXPERIENCE_CONDITIONS')
        checked_conditions.append(normalized(value))
    attempts = proposal['attempts']
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 6:
        raise KiokukoError('EXPERIENCE_ATTEMPTS')
    checked, used = [], set()
    # Validation uses the full run. Window proposals are separately restricted to
    # calls and results present in that window before being persisted.
    for attempt in attempts:
        if not isinstance(attempt, dict) or set(attempt) != {'call_seq','result_seq','target'}:
            raise KiokukoError('EXPERIENCE_ATTEMPTS')
        a, b = attempt['call_seq'], attempt['result_seq']
        if type(a) is not int or type(b) is not int or not span['start'] <= a < b <= span['end'] or (a,b) in used:
            raise KiokukoError('EXPERIENCE_ATTEMPTS')
        call = next((e for e in evidence if e['seq'] == a), None)
        result = next((e for e in evidence if e['seq'] == b), None)
        if not call or not result or call['type'] != 'tool.call' or result['type'] != 'tool.result':
            raise KiokukoError('EXPERIENCE_ATTEMPTS')
        obs = call.get('observation')
        if obs is None or obs != result.get('observation') or call['tool'] != result['tool']:
            raise KiokukoError('EXPERIENCE_ATTEMPTS')
        target = command(call)
        if not target or attempt['target'] != target:
            raise KiokukoError('EXPERIENCE_TARGET')
        code = exit_code(result)
        checked.append({**attempt, 'action': target, 'tool': call['tool'], 'exit_code': code,
                        'outcome': 'unknown' if code is None else 'success' if code == 0 else 'failure',
                        'context': command_context(call), 'call_digest': digest(call['text']), 'result_digest': digest(result['text'])})
        used.add((a,b))
    checked.sort(key=lambda a: a['result_seq'])
    last = checked[-1]
    # A different check is not recovery. Mixed targets cannot establish one outcome.
    same = all((a['tool'],a['target'],a['context']) == (last['tool'],last['target'],last['context']) for a in checked)
    recovery = 'unknown'
    if same and last['outcome'] == 'success' and any(a['outcome'] == 'failure' for a in checked[:-1]):
        recovery = 'recovered'
    elif same and last['outcome'] == 'failure':
        recovery = 'unresolved'
    structure.update(schema_version=2, span=span, attempts=checked,
                     conditions=sorted(set(checked_conditions)), field_evidence=field_evidence,
                     grounded=grounded, recovery=recovery,
                     outcome=last['outcome'] if same else 'unknown')
    return structure


def features(structure):
    if structure.get('schema_version') != 2:
        return None
    last = structure['attempts'][-1]
    return {'tool': last['tool'], 'target': last['target'], 'context': last['context'], 'conditions': structure['conditions']}


def exact_identity(structure):
    # Run/seq addresses change between independent observations; content does not.
    base = {k: structure[k] for k in ('situation','observation','action','outcome','inference')}
    if structure.get('schema_version') == 2:
        base.update(conditions=structure['conditions'], recovery=structure['recovery'],
                    grounded=structure['grounded'],
                    attempts=[{k:a[k] for k in ('tool','target','context','outcome','action')} for a in structure['attempts']])
    return canonical(base)


def select_prepared(prepared, limit=3):
    def rank(item):
        s = item[0]
        return (s.get('recovery') != 'recovered', s['outcome'] == 'unknown')
    ordered = sorted(prepared, key=lambda item: (*rank(item), exact_identity(item[0])))
    selected, seen, topics = [], set(), set()
    for quality in sorted({rank(item) for item in ordered}):
        group = [item for item in ordered if rank(item) == quality]
        while group and len(selected) < limit:
            item = next((x for x in group if canonical(features(x[0]) or x[0]['situation']) not in topics), group[0])
            group.remove(item)
            key = exact_identity(item[0])
            if key in seen:
                continue
            selected.append(item)
            seen.add(key)
            topics.add(canonical(features(item[0]) or item[0]['situation']))
    return selected
