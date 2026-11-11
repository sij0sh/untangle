"""Derived files under <root>/.agents/artifacts/untangle/<audit-id>/: current.md, audit.json, report.md."""
import json
import time
from pathlib import Path

from . import mapping, storage
from .ledger import ART, applicable_profiles
from .work import all_items, coverage_summary
from .checks import PITCH


def out_dir(state):
    return Path(state['root']) / ART / state['audit_id']


def render_current(state, phase):
    lines = [f'# untangle {state["audit_id"]}: rev {state["rev"]}', '', f'Phase: {phase.upper()}',
             f'Root: {state["root"]}', f'Active artifacts: {out_dir(state)}',
             'Guard: ' + json.dumps({'run': state['audit_id'], 'phase': phase, 'rev': state['rev']})]
    policy = state.get('policy')
    if policy:
        base = Path(policy['root'])
        references = ['references/mapping.md'] if phase == 'map' else ['references/principles.md']
        if phase in ('scope', 'challenge'):
            references.append('references/mapping.md')
        paths = None
        if phase == 'dissect' and state.get('queue'):
            paths = all_items(state)[state['queue'][0]].get('paths')
        profiles, limits = applicable_profiles(state, paths)
        lines += ['', '## Read references'] + [f'- {base / p}' for p in references + profiles]
        lines += [f'- Limitation: {l}' for l in limits]
    if state.get('counters'):
        lines.append(f'MAP actions: {state["counters"]["map"]}/{state["limits"]["max_map_steps"]}; '
                     f'model revision: {state.get("map_rev", 0)}')
    if phase == 'map':
        model = state.get('mapping')
        queue = model.get('queue', []) if model else []
        action = 'seed' if not model else 'trace' if queue else 'finish'
        lines += [f'MAP next action: {action}', f'Mapping queue: {queue or "empty"}']
        if queue:
            fid = queue[0]
            lines += [f'Current mapping flow: {fid}', json.dumps(state['flows'][fid], indent=2)]
    if phase in ('scope', 'challenge') and state.get('mapping'):
        lines += ['', '## Working map'] + render_map(state)
        lines += ['', '## Mapping/analysis unknowns'] + ['- ' + u for u in mapping.unknowns(state)]
        lines += ['', '## Scope proposal', json.dumps(state.get('map', {}), indent=2)]
        lines += ['', '## Audit work partition', json.dumps(state.get('units', {}), indent=2)]
    if state.get('units'):
        c = coverage_summary(state)
        lines += [f'Coverage: {c["reviewed"]}/{c["in_scope"]} in-scope files reviewed; {c["discovered"]} '
                  f'discovered; {len(c["excluded"])} excluded; blocked: {", ".join(c["blocked"]) or "none"}']
    if phase == 'map' and state.get('revise'):
        lines.append(f'User asked to revise the map: {state["revise"]}')
    if phase == 'dissect' and state.get('map', {}).get('conventions'):
        lines += ['', '## Project conventions (tier 2)'] + [
            f'- [{c["status"]}] {c["rule"]} ({c["source"]})' for c in state['map']['conventions']]
    if phase == 'scope':
        lines.append(f'map_rev: {state["map_rev"]}')
    if state.get('scope'):
        used = {'dissect_transitions': 'dissect', 'max_units': 'units', 'synth_reopens': 'synth',
                'challenge_rounds': 'challenge'}
        lines.append('Budgets: ' + ', '.join(f'{k} {state["counters"][used[k]]}/{v}' if k in used else f'{k} {v}'
                                             for k, v in state['scope']['budgets'].items()))
        lines.append(f'Allowed commands: {state["scope"]["allowed_commands"] or "none (static only)"}')
    if phase == 'dissect' and state['queue']:
        i = state['queue'][0]
        it = all_items(state)[i]
        lines += ['', f'## Current item: {i} ({it["kind"]}), send "item": "{i}", "rev": {state["rev"]}']
        if it['kind'] == 'flow':
            lines += [f'Entry: {it["where"]}', f'Flow to trace: {it["flow"]}']
        else:
            lines += [f'Responsibility: {it["responsibility"]}',
                      f'Depth: {it["depth"]}; passes: {it.get("passes", 0)}']
            if it.get('children'):
                lines.append(f'Children reviewed: {", ".join(it["children"])}: send "interactions".')
            lines += ['Ranges (send exactly these in "reviewed"):'] + [f'- {p}' for p in it['paths']]
        mids = it.get('map_units', [])
        lines += ['', 'Map slice:'] + [json.dumps(state['mapping']['units'][mid], indent=2) for mid in mids]
        if it['kind'] == 'flow':
            lines += ['Preliminary mapping trace (not audit credit):', json.dumps(it.get('mapping_trace'), indent=2)]
        lines += ['Unknowns:'] + ['- ' + u for u in mapping.unknowns(state, mids, [i] if it['kind'] == 'flow' else [])]
        lines.append(f'Queue after this: {", ".join(state["queue"][1:]) or "empty"}')
    if phase == 'checkpoint':
        cp = state['checkpoint']
        lines += ['', f'## Checkpoint: {cp["reason"]}', f'Resumes at: {cp["resume"]}',
                  f'Pending queue: {", ".join(state.get("queue", [])) or "empty"}']
    return '\n'.join(lines) + '\n'


def render_map(state: dict) -> list:
    def cell(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return str(value).replace('|', '\\|').replace('\n', ' ')
    units = state.get('mapping', {}).get('units', {})
    lines = ['unit | structural home | capability | owner/contract | consumers + why | flows | evidence/gaps',
             '--- | --- | --- | --- | --- | --- | ---']
    connections = []
    capabilities = state.get('mapping', {}).get('capabilities', {})
    for uid, u in units.items():
        flows = [f'{fid} ({f["map_status"]})' for fid, f in state.get('flows', {}).items() if uid in f['map_units']]
        labels = [f'{cid}: {capabilities[cid]["purpose"]}' for cid in u['capabilities']]
        lines.append(' | '.join(cell(v) for v in (uid, u['structural_home'], labels,
            {'owner': u['owner'], 'contract': u['contract']}, u['consumers'], flows, u['evidence'])))
        if u['incoming'] or u['outgoing']:
            connections.append(f'- {uid} cross-boundary connections: incoming {u["incoming"]}; outgoing {u["outgoing"]}')
    lines += [''] + connections
    lines += [f'- Capability {cid}' + (f' (within {cap["parent"]})' if cap.get('parent') else '')
              + f': {cap["purpose"]}; mapping units: '
              + ', '.join(uid for uid, unit in units.items() if cid in unit['capabilities'])
              for cid, cap in capabilities.items()]
    owners = {}
    for uid, u in units.items():
        owner = json.dumps(u['owner'], sort_keys=True)
        owners.setdefault(owner, []).append(uid)
    lines += [f'- Shared/possibly duplicate owner {owner}: {ids}; inspect authority, do not assume a defect.'
              for owner, ids in owners.items() if len(ids) > 1]
    return lines


def render_report(state, complete, reasons):
    c = coverage_summary(state)
    recs = state.get('recommendations', [])
    f = state.get('findings', {})
    head = 'COMPLETE (static assessment of the approved scope)' if complete else 'INCOMPLETE: ' + '; '.join(reasons)
    out = [f'# Untangle audit {state["audit_id"]}', '', f'Status: **{head}**',
           f'Snapshot: `{state["root"]}`, report generated {time.strftime("%Y-%m-%d %H:%M:%S %Z")}; state rev '
           f'{state["rev"]}', f'Policy: local references at `{state.get("policy", {}).get("root", "not loaded")}`',
           f'Scope: {"approved" if state.get("scope") else "NOT APPROVED; provisional coverage only"}',
           f'MAP actions used: {state.get("counters", {}).get("map", 0)}',
           f'Reviewer: {state.get("reviewer", "none")}' + (' (self-review, not independent)'
                                                           if state.get('reviewer') == 'self' else ''),
           '', '## Coverage', '',
           f'- Discovered paths: {c["discovered"]}', f'- In scope: {c["in_scope"]}',
           f'- Reviewed: {c["reviewed"]}', f'- Unreviewed: {len(c["unreviewed"])}',
           f'- Blocked items: {", ".join(c["blocked"]) or "none"}',
           f'- Pending items: {", ".join(c["pending"]) or "none"}',
           f'- Excluded: {len(c["excluded"])}']
    out += [f'  - `{p}`: {r}' for p, r in sorted(c['excluded'].items())]
    out += [f'  - unreviewed `{p}`' for p in c['unreviewed']]
    out += ['', 'Full coverage does not prove semantic correctness.'
            + (' The in-scope source and policy matched the approved snapshot when this was validated.'
               if complete else '')]

    out += ['', '## Working map (not audit coverage)'] + render_map(state)
    limitations = mapping.unknowns(state) + state.get('policy', {}).get('limitations', [])
    out += ['', '## Unknowns / static limitations'] + (['- ' + u for u in limitations] or ['None recorded.'])
    out += ['', '## Exposure discovery', json.dumps(state.get('mapping', {}).get('exposure_checks', {}), indent=2)]
    out += ['', '## Unclassified paths', json.dumps({p: r for p, r in state.get('mapping', {}).get('path_roles', {}).items()
                                                  if 'unclassified' in r}, indent=2)]

    def rec(r):
        lines = ['', f'### {r["id"]}: {r["title"]}', '', f'Change: {r["change"]}']
        lines += [f'- Finding {i} at `{f[i]["where"]}` ({f[i]["evidence"]}; {f[i]["policy"]["tier"]}: '
                  f'{f[i]["policy"]["source"]}): {f[i]["summary"]}' for i in r['findings'] if i in f]
        for fid in r['findings']:
            if fid in f:
                lines += [f'- {fid} current behavior: {f[fid].get("current_behavior", "not recorded")}; '
                          f'consumers: {f[fid].get("consumers", [])}; uncertainty: {f[fid].get("uncertainty", "unknown")}']
        for k in ('preserves', 'missing_evidence', 'verification', 'benefit', 'risk', 'effort'):
            if r.get(k):
                lines.append(f'- {k.replace("_", " ").capitalize()}: {r[k]}')
        for k in PITCH:
            if r.get('pitch', {}).get(k):
                lines.append(f'- {k.replace("_", " ").capitalize()}: {r["pitch"][k]}')
        if r.get('depends_on'):
            lines.append(f'- Depends on: {", ".join(r["depends_on"])}')
        return lines

    for track, title in (('A', 'Track A: preserve behavior'),
                         ('B', 'Track B: simplification bargains (approval requested; none approved)'),
                         ('gap', 'Evidence gaps')):
        out += ['', f'## {title}']
        chosen = [r for r in recs if r['track'] == track]
        out += sum((rec(r) for r in chosen), []) or ['', 'None.']
    out += ['', '## Refactor waves']
    out += [f'- {w["id"]}: {", ".join(w["recommendations"])}. Acceptance: {w["acceptance"]}'
            for w in state.get('waves', [])] or ['None.']
    used = {i for r in recs for i in r['findings']} | {d['finding'] for d in state.get('dismissed', [])}
    loose = [i for i in f if i not in used]
    out += ['', '## Findings not yet synthesized']
    out += [f'- {i} [{f[i]["track"]}] `{f[i]["where"]}`: {f[i]["summary"]}' for i in loose] or ['None.']
    out += ['', '## Dismissed findings']
    out += [f'- {d["finding"]}: {d["reason"]}' for d in state.get('dismissed', [])] or ['None.']
    out += ['', '## Challenge']
    out += [f'- [{o["verdict"]}] {o["claim"]}: {o["reason"]}' for o in state.get('objections', [])] or ['None.']
    out += ['', '## Limitations', '', f'- Commands run: {", ".join(state.get("commands_run", [])) or "none"}',
            '- Coverage and mapping do not prove equivalence. Reported commands/evidence are agent-supplied, not independently verified.',
            '- No target-source edits are authorized by this report.',
            '- B pitches need the user\'s decision; approving one does not authorize implementation.']
    return '\n'.join(out) + '\n'


def export(state, phase):
    out = out_dir(state)
    out.mkdir(parents=True, exist_ok=True)
    storage.write_json(out / 'audit.json', dict(state, phase=phase))
    (out / 'current.md').write_text(render_current(state, phase), encoding='utf-8')


def publish(state, complete, reasons):
    state['published'] = {'complete': complete, 'reasons': reasons}
    return None  # flow commits the publishing state before generating any final files
