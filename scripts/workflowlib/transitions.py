"""State changes per accepted stage; each returns the next stage, or None after publishing."""
from pathlib import Path

from . import mapping

from .ledger import (applicable_profiles, cover_lines, expand_specs, find_drift, in_scope_files, is_excluded, load_policy,
    need, unit_fingerprint, walk_tree)
from .work import all_items, budget, checkpoint_to, coverage_summary, next_work, reopen_items
from .exports import publish


def on_inventory(state, payload):
    root = Path(state['root'])
    policy = load_policy()
    ledger = walk_tree(root, payload.get('exclude', []), payload['limits'])
    mapping.invalidate_audit(state, 'reinventoried source/reference snapshot')
    state.update(limits=payload['limits'], exclude=payload.get('exclude', []), policy=policy, ledger=ledger)
    policy['selected_profiles'], policy['limitations'] = applicable_profiles(state)
    state['units'], state['flows'], state['findings'] = {}, {}, {}
    state.pop('mapping', None)
    state.pop('map', None)
    state.pop('checkpoint', None)
    state.setdefault('counters', {'map': 0, 'dissect': 0, 'units': 0, 'synth': 0, 'challenge': 0})
    return mapping.next_map(state)


def on_map(state, payload):
    return mapping.advance(state, payload)


def on_scope(state, payload):
    if payload.get('revise'):
        return mapping.reopen(state, payload['revise'])
    for p in in_scope_files(state):
        reason = is_excluded(p, state['map']['exclusions'])
        if reason:
            state['ledger'][p].update(disposition='excluded', scope_excluded=True,
                                      reason=f'{reason} (approved at SCOPE)')
    state['policy']['selected_profiles'], state['policy']['limitations'] = applicable_profiles(state)
    state['scope'] = {'budgets': payload['budgets'], 'allowed_commands': payload['allowed_commands'],
                      'approved_map_rev': state['map_rev']}
    state['approved_budgets'] = dict(payload['budgets'])
    state['queue'] = [i for i, it in all_items(state).items() if it['status'] == 'pending']
    return next_work(state)


def on_dissect(state, payload):
    head = state['queue'].pop(0)
    it = all_items(state)[head]
    state['counters']['dissect'] += 1
    state['findings'] = {k: f for k, f in state['findings'].items() if f['item'] != head}
    for f in payload['findings']:
        state['findings'][f['id']] = dict(f, item=head,
            source_sha256=state['ledger'][f['where'].partition(':')[0]]['sha256'])
    if 'children' in payload:
        it['children'] = [k['id'] for k in payload['children']]
        for k in payload['children']:
            k = dict(k, paths=expand_specs(state, k['paths']))
            state['units'][k['id']] = {'kind': 'unit', 'responsibility': k['responsibility'], 'paths': k['paths'],
                                       'map_units': it.get('map_units', []), 'depth': it['depth'] + 1,
                                       'parent': head, 'status': 'pending',
                                       'passes': state.get('passes_used', {}).get(k['id'], 0),
                                       'fingerprint': unit_fingerprint(state,
                                                                       cover_lines(state, k['paths'], 'DISSECT'))}
        known = state.setdefault('unit_ids', [])
        for child in it['children']:
            if child not in known:
                known.append(child)
                state['counters']['units'] += 1
        state['queue'][:0] = it['children'] + [head]
        return next_work(state)
    it['passes'] = it.get('passes', 0) + 1
    state.setdefault('passes_used', {})[head] = it['passes']
    if 'blocked' in payload:
        it.update(status='blocked', blocked=payload['blocked'])
    else:
        it['status'] = 'reviewed'
        for k in ('trace', 'interactions', 'limitations'):
            if k in payload:
                it[k] = payload[k]
    return next_work(state)


def on_synthesize(state, payload):
    for k in ('recommendations', 'dismissed', 'waves'):
        state[k] = payload.get(k, [])
    reopen = payload.get('reopen', [])
    if not reopen:
        return 'challenge'
    reopen_items(state, reopen, 'SYNTHESIZE')
    state['counters']['synth'] += 1
    if state['counters']['synth'] > budget(state, 'synth_reopens'):
        return checkpoint_to(state, 'dissect', 'synth_reopens budget used', required_budget='synth_reopens')
    return next_work(state)


def on_challenge(state, payload):
    state['reviewer'] = payload['reviewer']
    state['objections'] = state.get('objections', []) + payload['objections']
    targets = [t for o in payload['objections'] if o['verdict'] == 'accepted' for t in o['reopen']]
    if targets:
        units = [t for t in targets if t not in ('map', 'synthesize')]
        state['counters']['challenge'] += 1
        exhausted = state['counters']['challenge'] > budget(state, 'challenge_rounds')
        if 'map' in targets:
            nxt = mapping.reopen(state, '; '.join(o['reason'] for o in payload['objections']
                                                 if o['verdict'] == 'accepted'))
            if exhausted:
                return checkpoint_to(state, 'map', 'challenge_rounds budget used', required_budget='challenge_rounds')
            return nxt
        reopen_items(state, units, 'CHALLENGE')
        resume = 'dissect' if units else 'synthesize'
        if exhausted:
            return checkpoint_to(state, resume, 'challenge_rounds budget used', required_budget='challenge_rounds')
        return next_work(state) if units else 'synthesize'
    diffs = find_drift(state)
    need(not diffs, 'CHALLENGE: source or policy changed since INVENTORY; a complete report is blocked: '
                     + '; '.join(diffs[:20]) + '. Send {"checkpoint": "drift"}.')
    c = coverage_summary(state)
    reasons = [f'blocked: {", ".join(c["blocked"])}'] if c['blocked'] else []
    reasons += [f'pending: {", ".join(c["pending"])}'] if c['pending'] else []
    reasons += [f'{len(c["unreviewed"])} in-scope files unreviewed'] if c['unreviewed'] else []
    return publish(state, not reasons, reasons)


def _resume(state):
    cp = state['checkpoint']
    target = cp['resume']
    required = cp.get('required_budget')
    if required:
        budgets = state.get('scope', {}).get('budgets', state.get('approved_budgets', {}))
        used = state['counters'][{'synth_reopens': 'synth', 'challenge_rounds': 'challenge'}[required]]
        need(budgets.get(required, 0) >= used,
             f'CHECKPOINT: extend {required} to cover cumulative usage {used}.')
    nxt = next_work(state) if target == 'dissect' else mapping.next_map(state) if target == 'map' else target
    need(nxt != 'checkpoint', f'CHECKPOINT: the extension does not cover: {state["checkpoint"]["reason"]}.')
    return nxt


def on_checkpoint(state, payload):
    d = payload['decision']
    if d == 'reinventory':
        mapping.invalidate_audit(state, 'reinventory requested; source/scope approval suspended')
        return 'inventory'
    if d == 'publish_incomplete':
        return publish(state, False, [f'published at checkpoint: {state["checkpoint"]["reason"]}'])
    state['limits'].update(payload.get('limits', {}))
    if 'scope' not in state and payload.get('budgets'):
        state['approved_budgets'].update(payload['budgets'])
    if 'scope' in state:
        state['scope']['budgets'].update(payload.get('budgets', {}))
        state['approved_budgets'] = dict(state['scope']['budgets'])
        state['scope']['allowed_commands'] += [c for c in payload.get('allowed_commands', [])
                                               if c not in state['scope']['allowed_commands']]
    return _resume(state)
