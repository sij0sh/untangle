"""MAP's bounded seed → trace/correct → finish loop; audit evidence stays separate."""
import copy
import json
from typing import Any

from .ledger import (applicable_profiles, cover_lines, expand_specs, file_sha, in_scope_files, is_excluded, need,
                     need_list, need_text, parse_spec, unit_fingerprint)
from .work import checkpoint_to

EXPOSURES = ('cli_http_jobs', 'exports', 'registration_reflection_routing',
             'configuration_data', 'migrations', 'external_callers')


def object_at(value: Any, where: str) -> dict:
    need(isinstance(value, dict), f'{where}: expected an object.')
    return value


def claim(value: Any, where: str) -> None:
    if isinstance(value, dict):
        need_text(value, 'unknown', where)
    else:
        need(isinstance(value, str) and value.strip(), f'{where}: text or {{"unknown": "reason"}} required.')


def evidence(state: dict, records: Any, where: str, observed: bool = False) -> None:
    need(isinstance(records, list) and records, f'{where}: non-empty evidence list required.')
    local = False
    for e in records:
        object_at(e, where)
        need(e.get('kind') in ('observed', 'inferred', 'unknown'), f'{where}: evidence kind observed|inferred|unknown.')
        if e['kind'] == 'unknown':
            need_text(e, 'reason', where)
        else:
            spec = need_text(e, 'where', where)
            path, _ = parse_spec(state, spec, where)
            need(path in in_scope_files(state), f'{where}: evidence is outside inventory authorization: {path}.')
            local = True
    need(not observed or local, f'{where}: a traced flow needs source evidence, not only unknowns.')


def _records(payload: dict, key: str) -> dict:
    result = {}
    for record in need_list(payload, key, 'MAP'):
        object_at(record, f'MAP {key}')
        rid = need_text(record, 'id', f'MAP {key}')
        need(rid not in result and rid not in ('map', 'synthesize'), f'MAP: duplicate/reserved id {rid}.')
        result[rid] = record
    return result


def validate_model(state: dict, payload: dict) -> None:
    """Validate a complete replacement model, including its proposed audit partition."""
    object_at(payload, 'MAP model')
    model = object_at(payload.get('mapping'), 'MAP mapping')
    caps, units, flows = _records(model, 'capabilities'), _records(model, 'units'), _records(payload, 'entry_points')
    for cid, c in caps.items():
        need_text(c, 'purpose', f'MAP capability {cid}')
        if 'parent' in c:
            need(isinstance(c['parent'], str) and c['parent'] in caps, f'MAP: unknown capability parent for {cid}.')
    for cid in caps:
        visited, node = set(), cid
        while node:
            need(node not in visited, f'MAP: capability parent cycle at {node}.')
            visited.add(node)
            node = caps[node].get('parent')
    for uid, u in units.items():
        w = f'MAP unit {uid}'
        need_text(u, 'purpose', w)
        home = need_list(u, 'structural_home', w)
        need(home and all(isinstance(h, str) and h.strip() for h in home), f'{w}: meaningful structural_home required.')
        memberships = need_list(u, 'capabilities', w)
        need(all(isinstance(c, str) and c in caps for c in memberships), f'{w}: unknown capability.')
        if not memberships:
            need_text(u, 'capabilities_unknown', w)
        claim(u.get('owner'), w + ' owner')
        claim(u.get('contract'), w + ' contract')
        consumers = need_list(u, 'consumers', w)
        need(consumers, f'{w}: consumers or explicit unknown required.')
        for c in consumers:
            object_at(c, w + ' consumer')
            if 'unknown' in c:
                need_text(c, 'unknown', w)
            else:
                need_text(c, 'who', w)
                need_text(c, 'why', w)
        for key in ('incoming', 'outgoing'):
            for dep in need_list(u, key, w):
                need(isinstance(dep, str) and (dep in units or dep.startswith('external:')),
                     f'{w}: {key} dependency must name a mapping unit or external:boundary.')
        evidence(state, u.get('evidence'), w)
    roles = object_at(model.get('path_roles'), 'MAP path_roles')
    proposed = need_list(payload, 'exclusions', 'MAP', required=False)
    for rule in proposed:
        object_at(rule, 'MAP exclusion')
        need_text(rule, 'glob', 'MAP exclusion')
        need_text(rule, 'reason', 'MAP exclusion')
    scope = {p for p in in_scope_files(state) if not is_excluded(p, proposed)}
    need(set(roles) == scope, 'MAP: path_roles must account for exactly every candidate in-scope file.')
    for path, role in roles.items():
        object_at(role, f'MAP role {path}')
        if 'unclassified' in role:
            need_text(role, 'unclassified', f'MAP role {path}')
            need(not role.get('units'), f'MAP: {path} cannot be both unclassified and classified.')
        else:
            ids = need_list(role, 'units', f'MAP role {path}')
            need(ids and all(isinstance(i, str) and i in units for i in ids), f'MAP: {path} needs known mapping unit IDs.')
    for fid, f in flows.items():
        w = f'MAP flow {fid}'
        where = need_text(f, 'where', w)
        if not where.startswith('external:'):
            parse_spec(state, where, w)
        need(bool(f.get('flow')) != bool(f.get('excluded')), f'{w}: exactly one of flow/excluded required.')
        need_text(f, 'flow' if f.get('flow') else 'excluded', w)
        mids = need_list(f, 'map_units', w)
        need(all(isinstance(i, str) and i in units for i in mids), f'{w}: unknown map unit.')
        if not mids:
            need_text(f, 'unclassified', w)
        evidence(state, f.get('evidence'), w)
    exposures = object_at(model.get('exposure_checks'), 'MAP exposure_checks')
    need(set(exposures) == set(EXPOSURES), f'MAP: exposure_checks needs {EXPOSURES}.')
    for category, e in exposures.items():
        object_at(e, f'MAP exposure {category}')
        status = e.get('status')
        need(status in ('found', 'none', 'not_applicable', 'unknown'), f'MAP: invalid exposure {category}.')
        entries = need_list(e, 'entries', f'MAP exposure {category}')
        need(all(isinstance(i, str) and i in flows for i in entries), f'MAP exposure {category}: unknown flow.')
        need(bool(entries) == (status == 'found'), f'MAP exposure {category}: found requires entries; other states require none.')
        if status != 'found':
            need_text(e, 'reason', f'MAP exposure {category}')
        evidence(state, e.get('evidence'), f'MAP exposure {category}', observed=status in ('none', 'not_applicable'))
    discovered = {i for e in exposures.values() for i in e['entries']}
    need(discovered == set(flows), 'MAP: every entry point needs an exposure checklist association.')
    for c in need_list(payload, 'conventions', 'MAP'):
        object_at(c, 'MAP convention')
        need(c.get('status') in ('adopted', 'accidental', 'conflict'), 'MAP: convention status adopted|accidental|conflict.')
        need_text(c, 'rule', 'MAP convention')
        need_text(c, 'source', 'MAP convention')
    obs = need_list(payload, 'observables', 'MAP')
    need(obs, 'MAP: at least one observable required (documentation/data contracts count).')
    for o in obs:
        object_at(o, 'MAP observable')
        need(o.get('status') in ('contractual', 'uncharacterized'), 'MAP: observable status contractual|uncharacterized.')
        need_text(o, 'what', 'MAP observable')
    covered = {}
    audit_units = _records(payload, 'units')
    need(not (set(audit_units) & set(flows)), 'MAP: audit units and flows must have distinct IDs.')
    for uid, u in audit_units.items():
        need_text(u, 'responsibility', f'MAP audit unit {uid}')
        mids = need_list(u, 'map_units', f'MAP audit unit {uid}')
        need(all(isinstance(i, str) and i in units for i in mids), f'MAP audit unit {uid}: unknown mapping ID.')
        if not mids:
            need_text(u, 'unclassified', f'MAP audit unit {uid}')
        specs = expand_specs(state, need_list(u, 'paths', f'MAP audit unit {uid}'), proposed)
        need(specs, f'MAP audit unit {uid}: empty work unit.')
        for path, lines in cover_lines(state, specs, 'MAP').items():
            need(path in scope, f'MAP: audit unit {uid} includes excluded {path}.')
            need(not (covered.get(path, set()) & lines), f'MAP: audit range overlap in {path}.')
            covered.setdefault(path, set()).update(lines)
    need(all(covered.get(p) == parse_spec(state, p, 'MAP')[1] for p in scope),
         'MAP: audit units must partition every candidate file completely.')


def _semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items() if k not in ('evidence', 'sha256')}
    if isinstance(value, list):
        return [_semantic(v) for v in value]
    return value


def _stamp(state: dict, value: Any) -> Any:
    if isinstance(value, dict):
        value = {k: _stamp(state, v) for k, v in value.items()}
        spec = value.get('where')
        entry = state['ledger'].get(spec.partition(':')[0]) if isinstance(spec, str) else None
        if value.get('kind') in ('observed', 'inferred') and entry and entry['kind'] == 'file':
            value['sha256'] = entry['sha256']
    elif isinstance(value, list):
        value = [_stamp(state, v) for v in value]
    return value


def invalidate_audit(state: dict, reason: str) -> None:
    """Retain historical evidence, never retain current approval for a changed model."""
    keys = ('mapping', 'map', 'map_rev', 'scope', 'units', 'flows', 'findings',
            'recommendations', 'dismissed', 'waves', 'objections', 'reviewer')
    if state.get('scope') or state.get('findings') or state.get('recommendations'):
        state.setdefault('history', []).append(dict(reason=reason, rev=state['rev'],
            evidence={k: copy.deepcopy(state[k]) for k in keys if k in state}))
    if state.get('scope'):
        state['approved_budgets'] = dict(state['scope']['budgets'])
    for key in ('scope', 'recommendations', 'dismissed', 'waves', 'objections', 'reviewer'):
        state.pop(key, None)
    state['findings'] = {}
    state['queue'] = []
    for it in {**state.get('units', {}), **state.get('flows', {})}.values():
        it['status'] = 'pending'
        for key in ('trace', 'interactions', 'blocked'):
            it.pop(key, None)
    for e in state.get('ledger', {}).values():
        if e.pop('scope_excluded', False):
            e.update(disposition='pending')
            e.pop('reason', None)
    if state.get('policy'):
        state['policy']['selected_profiles'], state['policy']['limitations'] = applicable_profiles(state)


def reopen(state: dict, reason: str) -> str:
    invalidate_audit(state, reason)
    state['revise'] = reason
    return next_map(state)


def unknowns(state: dict, map_ids=None, flow_ids=None) -> list:
    """Named limitations, not evidence of safety. Exposure uncertainty is cross-cutting."""
    model = state.get('mapping', {})
    mids = model.get('units', {}) if map_ids is None else map_ids
    fids = state.get('flows', {}) if flow_ids is None else flow_ids
    found = []

    def scan(value, label):
        if isinstance(value, dict):
            for key in ('unknown', 'unclassified', 'capabilities_unknown'):
                if key in value:
                    found.append(f'{label}: {value[key]}')
            if value.get('kind') == 'unknown' or value.get('status') == 'unknown':
                found.append(f'{label}: {value.get("reason", "unknown")}')
            for key, child in value.items():
                scan(child, label + '/' + key)
        elif isinstance(value, list):
            for child in value:
                scan(child, label)

    for mid in mids:
        scan(model.get('units', {}).get(mid, {}), mid)
    for fid in fids:
        f = state.get('flows', {}).get(fid, {})
        if f.get('map_status') == 'unknown':
            found.append(f'{fid}: {f.get("mapping_trace", {}).get("reason", "unknown mapping flow")}')
        scan(f.get('mapping_trace', {}), fid + '/trace')
        found.extend(f'{fid}: {l}' for l in f.get('limitations', []))
    scan(model.get('path_roles', {}), 'path roles')
    scan(model.get('exposure_checks', {}), 'exposure')
    return sorted(set(found))


def next_map(state: dict) -> str:
    if state['counters']['map'] >= state['limits']['max_map_steps']:
        return checkpoint_to(state, 'map', 'max_map_steps allowance used')
    return 'map'


def check(state: dict, payload: dict) -> None:
    action = payload.get('action')
    need(action in ('seed', 'trace', 'finish'), 'MAP: action is seed|trace|finish.')
    need(state['counters']['map'] < state['limits']['max_map_steps'], 'MAP: allowance exhausted; checkpoint required.')
    if action == 'seed':
        validate_model(state, payload)
        return
    need(state.get('mapping'), 'MAP: seed a model first.')
    if action == 'trace':
        queue = state['mapping']['queue']
        need(queue and payload.get('flow') == queue[0], 'MAP: trace must name the current mapping flow.')
        ids = set(state['flows'])
        if 'correction' in payload:
            validate_model(state, payload['correction'])
            ids = {e['id'] for e in payload['correction']['entry_points'] if e.get('flow')}
            need(payload['flow'] in ids, 'MAP: correction must retain the current flow; use seed to remove it.')
        status = payload.get('disposition')
        need(status in ('traced', 'unknown', 'pending'), 'MAP: disposition traced|unknown|pending.')
        evidence(state, payload.get('evidence'), 'MAP trace', observed=status == 'traced')
        if status in ('unknown', 'pending'):
            need_text(payload, 'reason', 'MAP trace')
        for key in ('consumers', 'calls', 'effects', 'failure_cleanup'):
            claim(payload.get(key), f'MAP trace {key}')
        for fid in need_list(payload, 'reopen', 'MAP trace', required=False):
            need(isinstance(fid, str) and fid in ids, f'MAP: cannot reopen unknown/removed flow {fid}.')
    else:
        need(not state['mapping']['queue'], 'MAP: pending flows prevent finish.')
        need(all(f.get('map_status') in ('traced', 'unknown') and f.get('model_rev') == state['map_rev']
                 for f in state['flows'].values()), 'MAP: stale or unresolved mapping flow.')
        validate_model(state, state['mapping']['proposal'])


def _install(state: dict, payload: dict) -> None:
    proposal = {k: copy.deepcopy(payload.get(k, [])) for k in
                ('mapping', 'units', 'entry_points', 'conventions', 'observables', 'exclusions')}
    fingerprint = file_sha(json.dumps(_semantic(proposal), sort_keys=True).encode())
    changed = fingerprint != state.get('mapping', {}).get('fingerprint')
    prior_flows = state.get('flows', {})
    if changed:
        invalidate_audit(state, 'semantic map correction')
        state['map_rev'] = state.get('map_rev', 0) + 1
    old = state.get('units', {})
    passes = state.setdefault('passes_used', {})
    known = state.setdefault('unit_ids', [])
    audit_units = {}
    for u in proposal['units']:
        uid = u['id']
        if uid not in known:
            known.append(uid)
            state['counters']['units'] += 1
        paths = expand_specs(state, u['paths'], proposal['exclusions'])
        audit_units[uid] = dict(id=uid, responsibility=u['responsibility'], map_units=u['map_units'],
            kind='unit', paths=paths, depth=0, status='pending',
            passes=max(passes.get(uid, 0), old.get(uid, {}).get('passes', 0)),
            fingerprint=unit_fingerprint(state, cover_lines(state, paths, 'MAP')))
        if 'unclassified' in u:
            audit_units[uid]['unclassified'] = u['unclassified']
    state['units'] = audit_units
    state['map'] = {k: proposal[k] for k in ('conventions', 'observables', 'entry_points', 'exclusions')}
    state['mapping'] = _stamp(state, dict(proposal['mapping'], proposal=proposal, fingerprint=fingerprint))
    state['mapping']['units'] = {u['id']: u for u in state['mapping']['units']}
    state['mapping']['capabilities'] = {c['id']: c for c in state['mapping']['capabilities']}
    state['flows'] = {}
    for e in proposal['entry_points']:
        if not e.get('flow'):
            continue
        prior = prior_flows.get(e['id'], {}) if not changed else {}
        state['flows'][e['id']] = dict(_stamp(state, e), kind='flow', status='pending',
            passes=passes.get(e['id'], 0), map_status=prior.get('map_status', 'pending'),
            model_rev=prior.get('model_rev'), mapping_trace=prior.get('mapping_trace'))
    _queue(state)


def _queue(state: dict) -> None:
    state['mapping']['queue'] = [fid for fid, f in state['flows'].items() if f['map_status'] == 'pending']


def advance(state: dict, payload: dict) -> str:
    state['counters']['map'] += 1
    action = payload['action']
    if action == 'seed':
        _install(state, payload)
    elif action == 'trace':
        if 'correction' in payload:
            _install(state, payload['correction'])
        flow = state['flows'][payload['flow']]
        flow.update(map_status=payload['disposition'], model_rev=state['map_rev'],
                    mapping_trace=_stamp(state, {k: v for k, v in payload.items()
                                                if k not in ('correction', 'run', 'phase', 'rev')}))
        for fid in payload.get('reopen', []):
            state['flows'][fid]['map_status'] = 'pending'
        _queue(state)
    else:
        # Rebuild the proposed root work partition, discarding invalidated audit splits/statuses.
        # Reuse mapping dispositions only; cumulative unit/pass usage remains in its own ledger.
        _install(state, state['mapping']['proposal'])
        state.pop('revise', None)
        return 'scope'
    return next_map(state)
