"""One guarded run/root. Canonical commit precedes every recoverable derived export."""
import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import checks, exports, ledger, steps, storage, work

SCHEMA_VERSION = 2


def _path(root: Path) -> Path:
    return storage.base_dir(root) / 'workflow.json'


def _load(root: Path) -> dict:
    if not _path(root).exists():
        raise ValueError('No workflow in progress. Run `start`.')
    return storage.read_json(_path(root))


def _schema(state: dict) -> None:
    if state.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Legacy/unsupported active state preserved. Run `abandon` to archive it as '
                         'INCOMPLETE, then `start` explicitly; no automatic migration.')


def show(state: dict, launcher: str) -> str:
    phase = state['phase']
    guard = {'run': state['audit_id'], 'phase': phase, 'rev': state['rev']}
    return (f'STEP: {phase.upper()}\nActive artifacts: {exports.out_dir(state)}\n'
            f'First read: {exports.out_dir(state) / "current.md"}\n'
            f'{steps.PROMPTS[phase]}\n\n'
            f'Every payload must include: {json.dumps(guard)}\n'
            f'Then run: {launcher} done  (JSON on stdin; include the guard above)')


def _derive(root: Path, state: dict) -> None:
    """A failed export never invites replay of an already-committed completion."""
    try:
        exports.export(state, state['phase'])
        if state['phase'] == 'published':
            p = state['published']
            (exports.out_dir(state) / 'report.md').write_text(
                exports.render_report(state, p['complete'], p['reasons']), encoding='utf-8')
            _path(root).unlink()
    except (OSError, ValueError) as error:
        raise ValueError(f'COMMITTED {state["audit_id"]}/{state["rev"]}; export failed; run status '
                         f'(do not replay done): {error}') from error


def _refresh(root: Path, state: dict) -> dict:
    """Drift on resume is a committed checkpoint, never silent stale-map reuse."""
    if state['phase'] == 'published' and not state['published']['complete']:
        return state  # incomplete publication makes no current-snapshot completeness claim
    if state['phase'] not in ('inventory', 'checkpoint') and state.get('ledger') is not None:
        try:
            diffs = ledger.find_drift(state)
        except (OSError, ValueError) as error:
            diffs = [f'cannot validate snapshot: {error}']
        if diffs:
            target = 'challenge' if state['phase'] == 'published' else state['phase']
            state['phase'] = work.checkpoint_to(state, target, 'source/reference drift: ' + '; '.join(diffs[:20]))
            state['checkpoint']['drift'] = True
            state.pop('published', None)
            state['rev'] += 1
            storage.write_json(_path(root), state)
    return state


def start(root: Path, launcher: str) -> str:
    with storage.lock(storage.base_dir(root)):
        if _path(root).exists():
            raise ValueError('A run is unfinished; run `status` to resume or `abandon` to archive it.')
        state = {'schema_version': SCHEMA_VERSION, 'phase': steps.ORDER[0], 'rev': 0,
                 'audit_id': time.strftime('audit-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:12],
                 'root': str(root)}
        storage.write_json(_path(root), state)
        _derive(root, state)
    return show(state, launcher)


def status(root: Path, launcher: str) -> str:
    with storage.lock(storage.base_dir(root)):
        state = _load(root)
        _schema(state)
        state = _refresh(root, state)
        _derive(root, state)
    return (f'{steps.DONE_MSG}\nReport: {exports.out_dir(state) / "report.md"}'
            if state['phase'] == 'published' else show(state, launcher))


def _step(state: dict, payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        raise ValueError('Payload must be a JSON object.')
    ledger.need(payload.get('run') == state['audit_id'] and payload.get('phase') == state['phase']
                and type(payload.get('rev')) is int and payload['rev'] == state['rev'],
                f'Stale/duplicate completion: current run={state["audit_id"]}, phase={state["phase"]}, '
                f'rev={state["rev"]}. Run status; do not replay consumed completions.')
    checks.check_commands(state, payload)
    steps.CHECKS[state['phase']](state, payload)
    return steps.advance(state, payload)


def done(root: Path, payload: Any, launcher: str, stage: Optional[str] = None) -> str:
    if stage:
        raise ValueError('Stateful: omit `--stage`; the script tracks the step.')
    with storage.lock(storage.base_dir(root)):
        original = _load(root)
        _schema(original)
        if original['phase'] == 'published':
            raise ValueError('Completion already committed; run status to repair publication, not done.')
        state = copy.deepcopy(original)
        # Check the old guard before drift handling so a replay cannot change state.
        ledger.need(isinstance(payload, dict) and payload.get('run') == state['audit_id']
                    and payload.get('phase') == state['phase'] and type(payload.get('rev')) is int
                    and payload['rev'] == state['rev'], 'Stale/duplicate completion. Run status for the current guard.')
        state = _refresh(root, state)
        if state['rev'] != original['rev']:
            _derive(root, state)
            return show(state, launcher)
        nxt = _step(state, copy.deepcopy(payload))
        state['phase'] = 'published' if nxt is None else nxt
        storage.write_json(_path(root), state)
        _derive(root, state)
    return (f'{steps.DONE_MSG}\nReport: {exports.out_dir(state) / "report.md"}'
            if nxt is None else show(state, launcher))


def abandon(root: Path) -> str:
    with storage.lock(storage.base_dir(root)):
        state = _load(root)
        if state.get('schema_version') != SCHEMA_VERSION:
            # Preserve the exact legacy JSON before deleting anything; never migrate it in place.
            out = root / ledger.ART / ('legacy-' + uuid.uuid4().hex)
            storage.write_json(out / 'workflow.json', state)
            (out / 'report.md').write_text('# INCOMPLETE\nLegacy run explicitly abandoned; no migration.\n', encoding='utf-8')
            _path(root).unlink()
            return f'Legacy run archived INCOMPLETE at {out}. Run start explicitly.'
        state['rev'] += 1
        state['phase'] = 'published'
        state['published'] = {'complete': False, 'reasons': ['explicitly abandoned by user']}
        storage.write_json(_path(root), state)
        _derive(root, state)
    return f'Run archived INCOMPLETE at {exports.out_dir(state)}.'
