"""Payload checks per stage; raise ValueError to refuse `done`."""
from pathlib import Path

from .ledger import (cover_lines, expand_specs, file_sha, in_scope_files, need, need_approval,
    need_list, need_text, parse_spec)
from .work import all_items, budget
from . import mapping


TIERS = ('requirement', 'convention', 'default', 'reference', 'simplest')
TRACKS = ('A', 'B', 'gap')
BUDGETS = ('dissect_transitions', 'max_units', 'passes_per_unit', 'split_depth', 'synth_reopens',
           'challenge_rounds')
PITCH = ('behavior_lost', 'complexity_removed', 'preserving_alternative', 'decision_needed')


def check_inventory(state, payload):
    need_approval(payload, 'INVENTORY')
    limits = payload.get('limits')
    need(isinstance(limits, dict) and set(limits) == {'max_files', 'max_bytes', 'max_map_steps'}
         and all(type(v) is int and v > 0 for v in limits.values()),
         'INVENTORY: limits needs positive integers max_files, max_bytes and max_map_steps.')
    rules = need_list(payload, 'exclude', 'INVENTORY', required=False)
    for r in rules:
        need(isinstance(r, dict), 'INVENTORY: each exclude is {"glob", "reason"}.')
        need_text(r, 'glob', 'INVENTORY exclude')
        need_text(r, 'reason', 'INVENTORY exclude')
    need('policy_root' not in payload, 'INVENTORY: policy_root is obsolete; references are skill-local.')


def check_map(state, payload):
    if not wants_checkpoint(payload):
        mapping.check(state, payload)


def check_scope(state, payload):
    if wants_checkpoint(payload):
        return
    if isinstance(payload.get('revise'), str) and payload['revise'].strip():
        return
    need_approval(payload, 'SCOPE')
    need(type(payload.get('map_rev')) is int and payload['map_rev'] == state['map_rev'],
          f'SCOPE: approval must name map_rev {state["map_rev"]} (the map the user saw).')
    b = payload.get('budgets')
    need(isinstance(b, dict) and set(b) == set(BUDGETS)
         and all(type(b.get(k)) is int and b[k] > 0 for k in BUDGETS),
         f'SCOPE: budgets needs positive integers {", ".join(BUDGETS)}.')
    need(b['max_units'] >= state['counters']['units'],
         f'SCOPE: max_units must cover {state["counters"]["units"]} cumulative issued units.')
    need(all(b[k] >= v for k, v in state.get('approved_budgets', {}).items()),
         'SCOPE: previously approved budgets cannot be reset/lowered during remapping.')
    need(all(b[key] >= state['counters'][counter] for key, counter in
             (('dissect_transitions', 'dissect'), ('synth_reopens', 'synth'), ('challenge_rounds', 'challenge'))),
         'SCOPE: budgets cannot be below cumulative usage after remapping/reinventory.')
    cmds = need_list(payload, 'allowed_commands', 'SCOPE')
    need(all(isinstance(c, str) and c for c in cmds), 'SCOPE: allowed_commands are exact command strings.')


def wants_checkpoint(payload):
    return isinstance(payload.get('checkpoint'), str) and payload['checkpoint'].strip()


def check_commands(state: dict, payload: dict) -> None:
    commands = need_list(payload, 'commands_run', 'COMMANDS', required=False)
    allowed = (state.get('scope', {}).get('allowed_commands', [])
               if state['phase'] in ('dissect', 'synthesize', 'challenge', 'checkpoint') else [])
    need(all(isinstance(c, str) and c in allowed for c in commands),
         'COMMANDS: not approved at SCOPE; before scope static inspection only. Ask at CHECKPOINT.')


def wants_map_correction(payload):
    return isinstance(payload.get('map_correction'), str) and payload['map_correction'].strip()


def _check_findings(state, payload, item):
    w = f'DISSECT {item}'
    ids = set()
    for f in need_list(payload, 'findings', w):
        need(isinstance(f, dict), f'{w}: findings are objects.')
        fid = need_text(f, 'id', w)
        owner = state['findings'].get(fid, {}).get('item')
        need(fid not in ids and owner in (None, item), f'{w}: finding id {fid} is already used.')
        ids.add(fid)
        path = parse_spec(state, need_text(f, 'where', w), w)[0]
        need(path in in_scope_files(state), f'{w}: {fid} cites `{path}`, which is out of scope.')
        for k in ('summary', 'mechanism', 'option', 'current_behavior', 'uncertainty'):
            need_text(f, k, f'{w} {fid}')
        consumers = need_list(f, 'consumers', f'{w} {fid}')
        need(consumers and all(isinstance(c, str) and c.strip() for c in consumers),
             f'{w}: affected consumers (or explicitly unknown) required.')
        mids = need_list(f, 'map_units', f'{w} {fid}')
        fids = need_list(f, 'flow_ids', f'{w} {fid}')
        need(all(isinstance(i, str) and i in state['mapping']['units'] for i in mids)
             and all(isinstance(i, str) and i in state['flows'] for i in fids), f'{w}: unknown map/flow reference.')
        roles = state['mapping']['path_roles'].get(path, {}).get('units', [])
        need(set(roles) <= set(mids), f'{w}: finding must reference its source path mapping units.')
        if all_items(state)[item]['kind'] == 'flow':
            need(item in fids, f'{w}: flow finding must reference its current flow.')
        if f.get('track') == 'A':
            related = set(fids) | {fid for fid, flow in state['flows'].items() if set(flow['map_units']) & set(mids)}
            unknown = mapping.unknowns(state, mids, related)
            if unknown or f['uncertainty'].lower() != 'none':
                need_text(f, 'uncertainty_resolution', f'{w} {fid}: A requires evidence resolving/isolating unknowns')
        need(f.get('evidence') in ('observed', 'inferred'), f'{w}: {fid} evidence is observed|inferred.')
        need(f.get('track') in TRACKS, f'{w}: {fid} track is A|B|gap.')
        pol = f.get('policy')
        need(isinstance(pol, dict) and pol.get('tier') in TIERS, f'{w}: {fid} policy.tier is one of {TIERS}.')
        need_text(pol, 'source', f'{w} {fid} policy')


def check_dissect(state, payload):
    if wants_checkpoint(payload) or wants_map_correction(payload):
        return
    head = state['queue'][0]
    need(payload.get('item') == head and payload.get('rev') == state['rev'],
          f'DISSECT: stale or duplicate; the current item is {head} at rev {state["rev"]} (see current.md).')
    check_commands(state, payload)
    it = all_items(state)[head]
    trace = payload.get('trace') if isinstance(payload.get('trace'), list) else []
    files = {s.partition(':')[0] for s in (it['paths'] if it['kind'] == 'unit' else trace) if isinstance(s, str)}
    def now(p):
        f = Path(state['root']) / p
        return file_sha(f.read_bytes()) if f.is_file() else None
    changed = [p for p in files if p in state['ledger'] and now(p) != state['ledger'][p]['sha256']]
    need(not changed, f'DISSECT: changed since INVENTORY: {changed}. Send {{"checkpoint": "drift"}}.')
    modes = [k for k in ('reviewed', 'children', 'interactions', 'trace', 'blocked') if k in payload]
    need(len(modes) == 1, 'DISSECT: send exactly one of reviewed, children, interactions, trace, blocked.')
    mode, w = modes[0], f'DISSECT {head}'
    if mode == 'blocked':
        need_text(payload, 'blocked', w)
    elif it['kind'] == 'flow':
        need(mode == 'trace' and isinstance(payload['trace'], list) and payload['trace'],
              f'{w}: a flow needs a non-empty `trace` list of path:range steps.')
        for s in payload['trace']:
            path, _ = parse_spec(state, s, w)
            need(path in in_scope_files(state), f'{w}: trace is outside approved scope.')
        limitations = need_list(payload, 'limitations', w)
        need(all(isinstance(l, str) and l.strip() for l in limitations), f'{w}: limitations are text.')
    elif it.get('children'):
        need(mode == 'interactions', f'{w}: children are reviewed; send `interactions`.')
        need_text(payload, 'interactions', w)
    elif mode == 'reviewed':
        want = cover_lines(state, it['paths'], w)
        got = (cover_lines(state, expand_specs(state, payload['reviewed']), w)
               if isinstance(payload['reviewed'], list) else None)
        need(got == want, f'{w}: `reviewed` must cover exactly the unit ranges: {it["paths"]}.')
    elif mode == 'children':
        kids = payload['children']
        need(isinstance(kids, list) and len(kids) >= 2, f'{w}: split into at least two children.')
        need(it['depth'] + 1 <= budget(state, 'split_depth'),
              f'{w}: split_depth reached; review it whole or send {{"checkpoint": "..."}}.')
        need(state['counters']['units'] + len(kids) <= budget(state, 'max_units'),
              f'{w}: max_units reached; review it whole or send {{"checkpoint": "..."}}.')
        union, child_ids = {}, set()
        for k in kids:
            need(isinstance(k, dict), f'{w}: children are objects.')
            kid = need_text(k, 'id', w)
            need(kid not in all_items(state) and kid not in child_ids and kid not in ('map', 'synthesize'),
                 f'{w}: duplicate/reserved id {kid}.')
            child_ids.add(kid)
            need_text(k, 'responsibility', w)
            for p, lines in cover_lines(state, expand_specs(state, need_list(k, 'paths', w)), w).items():
                need(not (union.get(p, set()) & lines), f'{w}: children overlap in `{p}`.')
                union.setdefault(p, set()).update(lines)
        need(union == cover_lines(state, it['paths'], w), f'{w}: children must partition {it["paths"]} exactly.')
        need(payload.get('findings') == [], f'{w}: a split has no findings; report them in the children.')
    else:
        raise ValueError(f'{w}: a unit takes `reviewed`, `children` or `blocked`.')
    _check_findings(state, payload, head)


def check_synthesize(state, payload):
    if wants_checkpoint(payload) or wants_map_correction(payload):
        return
    w = 'SYNTHESIZE'
    recs = need_list(payload, 'recommendations', w)
    ids = {}
    for r in recs:
        need(isinstance(r, dict) and r.get('track') in TRACKS, f'{w}: each recommendation needs track A|B|gap.')
        need(need_text(r, 'id', w) not in ids, f'{w}: duplicate id {r["id"]}.')
        ids[r['id']] = r
        for k in ('title', 'change', 'verification'):
            need_text(r, k, f'{w} {r["id"]}')
        fs = need_list(r, 'findings', f'{w} {r["id"]}')
        need(fs and all(isinstance(i, str) and i in state['findings'] for i in fs),
             f'{w}: {r["id"]} cites unknown or no findings.')
        if r['track'] == 'A':
            need_text(r, 'preserves', f'{w} {r["id"]}')
            notA = [i for i in fs if state['findings'][i]['track'] != 'A']
            need(not notA, f'{w}: Track A {r["id"]} cites findings DISSECT filed as B or gap: {notA}. '
                            'Use Track B or gap, or reopen the item to reclassify.')
        elif r['track'] == 'B':
            need(isinstance(r.get('pitch'), dict), f'{w}: {r["id"]} needs `pitch`.')
            for k in PITCH:
                need_text(r['pitch'], k, f'{w} {r["id"]} pitch')
        else:
            need_text(r, 'missing_evidence', f'{w} {r["id"]}')
    for r in recs:
        deps = need_list(r, 'depends_on', w, required=False)
        need(all(isinstance(d, str) and d in ids for d in deps), f'{w}: {r["id"]} depends on an unknown recommendation.')

    def needs_b(rid, seen):
        for d in ids[rid].get('depends_on', []):
            if d not in seen:
                seen.add(d)
                if ids[d]['track'] == 'B' or needs_b(d, seen):
                    return True
        return False

    bad = [r['id'] for r in recs if r['track'] == 'A' and needs_b(r['id'], set())]
    need(not bad, f'{w}: Track A depends on a Track B bargain: {bad}. Move it to B or drop the dependency.')
    used = {i for r in recs for i in r['findings']}
    for d in need_list(payload, 'dismissed', w, required=False):
        need(isinstance(d, dict) and isinstance(d.get('finding'), str) and d['finding'] in state['findings'],
             f'{w}: dismissed cites unknown finding.')
        need_text(d, 'reason', w)
        used.add(d['finding'])
    missing = sorted(set(state['findings']) - used)
    need(not missing, f'{w}: findings neither used nor dismissed: {missing}.')
    for wv in need_list(payload, 'waves', w, required=False):
        mapping.object_at(wv, w + ' wave')
        need_text(wv, 'id', w)
        need_text(wv, 'acceptance', w)
        need(all(isinstance(i, str) and i in ids for i in need_list(wv, 'recommendations', w)),
             f'{w}: wave {wv["id"]} cites unknown ids.')
    reopen = need_list(payload, 'reopen', w, required=False)
    need(all(isinstance(i, str) and i in all_items(state) for i in reopen), f'{w}: reopen names unknown items.')


def check_challenge(state, payload):
    if wants_checkpoint(payload):
        return
    w = 'CHALLENGE'
    need(payload.get('reviewer') in ('fresh', 'self'), f'{w}: reviewer is fresh|self.')
    for o in need_list(payload, 'objections', w):
        need(isinstance(o, dict) and o.get('verdict') in ('accepted', 'rejected'),
              f'{w}: each objection needs verdict accepted|rejected.')
        need_text(o, 'claim', w)
        need_text(o, 'reason', w)
        if o['verdict'] == 'accepted':
            targets = need_list(o, 'reopen', w)
            need(targets and all(isinstance(t, str) and (t in ('map', 'synthesize') or t in all_items(state))
                                 for t in targets),
                  f'{w}: an accepted objection must reopen map, named units/flows or synthesize.')


def check_checkpoint(state, payload):
    need_approval(payload, 'CHECKPOINT')
    d = payload.get('decision')
    need(d in ('extend', 'reinventory', 'publish_incomplete'),
          'CHECKPOINT: decision is extend|reinventory|publish_incomplete.')
    if d == 'extend':
        b, cmds, limits = payload.get('budgets', {}), payload.get('allowed_commands', []), payload.get('limits', {})
        need(isinstance(b, dict) and isinstance(cmds, list) and isinstance(limits, dict) and (b or cmds or limits),
             'CHECKPOINT: extend names budgets/limits to raise and/or allowed_commands to add.')
        need(not state['checkpoint'].get('drift'), 'CHECKPOINT: drift requires reinventory or incomplete publication.')
        need(all(isinstance(c, str) and c.strip() for c in cmds), 'CHECKPOINT: allowed_commands are exact strings.')
        need('scope' in state or not cmds, 'CHECKPOINT: without approved scope, commands cannot be added.')
        need('scope' in state or state.get('approved_budgets') or not b,
             'CHECKPOINT: before initial SCOPE only limits may extend, no commands/budgets.')
        for k, v in limits.items():
            old = state.get('limits', {}).get(k, 0)
            need(k in ('max_files', 'max_bytes', 'max_map_steps') and type(v) is int and v > old,
                 f'CHECKPOINT: {k} must be a known inventory limit raised above {old}.')
        for k, v in b.items():
            old = state.get('scope', {}).get('budgets', state.get('approved_budgets', {})).get(k, 0)
            need(k in BUDGETS and type(v) is int and v > old,
                 f'CHECKPOINT: {k} must be a known budget raised above {old}.')
