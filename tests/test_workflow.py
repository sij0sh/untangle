"""Isolated public-driver regression tests; never run inspected-project commands."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workflowlib import checks, exports, flow, ledger, mapping, storage

LIVE_SKILL = ledger.SKILL_ROOT


class RunCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='untangle tests ')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'project with spaces'
        self.root.mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'src/a.py').write_text('def run(value):\n    if value:\n        return value\n    return None\n')
        self.skill = base / 'skill'
        shutil.copytree(LIVE_SKILL / 'references', self.skill / 'references')
        self.addCleanup(patch.stopall)
        patch.object(storage, 'STATE_DIR', base / 'engine state').start()
        patch.object(ledger, 'SKILL_ROOT', self.skill).start()
        flow.start(self.root, 'driver')

    def state(self):
        return storage.read_json(flow._path(self.root))

    def submit(self, payload):
        s = self.state()
        return flow.done(self.root, dict(run=s['audit_id'], phase=s['phase'], rev=s['rev'], **payload), 'driver')

    def inventory(self, steps=30):
        self.submit({'approved_by_user': True, 'limits': {'max_files': 100, 'max_bytes': 100000,
                                                        'max_map_steps': steps}, 'exclude': []})

    def evidence(self):
        return [{'kind': 'observed', 'where': 'src/a.py:1-4'}]

    def model(self, flows=2):
        roles = {p: {'units': ['M1']} for p in ledger.in_scope_files(self.state())}
        entries = [{'id': f'E{i}', 'where': 'src/a.py:1', 'flow': 'caller → owner → return/error',
                    'map_units': ['M1'], 'evidence': self.evidence()} for i in range(1, flows + 1)]
        exposure = {k: {'status': 'none', 'entries': [], 'reason': 'inspected source and config',
                        'evidence': self.evidence()} for k in mapping.EXPOSURES}
        if entries:
            exposure['exports'] = {'status': 'found', 'entries': [e['id'] for e in entries],
                                   'evidence': self.evidence()}
        return {'mapping': {'capabilities': [{'id': 'C1', 'purpose': 'provide a value'}],
            'units': [{'id': 'M1', 'purpose': 'return requested value', 'structural_home': ['module src/a'],
                       'capabilities': ['C1'], 'owner': 'run', 'contract': 'value in; optional value out',
                       'consumers': [{'who': 'public caller', 'why': 'needs value'}], 'incoming': [],
                       'outgoing': [], 'evidence': self.evidence()}], 'path_roles': roles,
            'exposure_checks': exposure},
            'units': [{'id': 'U1', 'responsibility': 'review available source', 'paths': sorted(roles),
                       'map_units': ['M1']}], 'entry_points': entries, 'conventions': [],
            'observables': [{'what': 'returned value/absence', 'status': 'contractual'}], 'exclusions': []}

    def seed(self, flows=2):
        p = self.model(flows)
        self.submit(dict(p, action='seed'))
        return p

    def trace_payload(self, disposition='traced', **extra):
        s = self.state()
        return dict(action='trace', flow=s['mapping']['queue'][0], disposition=disposition,
                    consumers='public caller', calls='caller → run', effects='return value',
                    failure_cleanup='no acquired resources; no operational exceptions',
                    evidence=self.evidence(), **extra)

    def finish_mapping(self, unknown=False):
        while self.state()['mapping']['queue']:
            self.submit(self.trace_payload('unknown' if unknown else 'traced',
                                           **({'reason': 'runtime selection unavailable'} if unknown else {})))
        self.submit({'action': 'finish'})

    def approve(self, **budgets):
        b = dict(zip(checks.BUDGETS, [80, 100, 3, 3, 3, 2]))
        b.update(budgets)
        self.submit({'approved_by_user': True, 'map_rev': self.state()['map_rev'],
                     'budgets': b, 'allowed_commands': []})

    def review_all(self):
        while self.state()['phase'] == 'dissect':
            s = self.state()
            item = s['queue'][0]
            it = {**s['units'], **s['flows']}[item]
            mode = {'reviewed': it['paths']} if it['kind'] == 'unit' else {'trace': ['src/a.py:1-4'], 'limitations': []}
            self.submit(dict(item=item, findings=[], commands_run=[], **mode))

    def synthesize(self):
        self.submit({'recommendations': [], 'dismissed': [], 'waves': []})


class MappingTests(RunCase):
    def test_dedicated_loop_preserves_separate_audit_status(self):
        self.inventory()
        self.seed()
        s = self.state()
        self.assertEqual(s['phase'], 'map')
        self.assertEqual(s['mapping']['queue'], ['E1', 'E2'])
        flow.status(self.root, 'driver')
        self.submit(self.trace_payload())
        self.assertEqual(self.state()['flows']['E1']['status'], 'pending')
        self.finish_mapping(unknown=True)
        s = self.state()
        self.assertEqual(s['phase'], 'scope')
        self.assertEqual(s['flows']['E2']['map_status'], 'unknown')
        self.assertEqual(s['flows']['E2']['status'], 'pending')
        self.assertEqual(s['counters']['map'], 4)
        evidence = s['flows']['E1']['mapping_trace']['evidence'][0]
        self.assertEqual(evidence['sha256'], s['ledger']['src/a.py']['sha256'])
        with self.assertRaisesRegex(ValueError, 'map_rev'):
            self.submit({'approved_by_user': True, 'map_rev': -1})
        self.approve()
        self.assertEqual(self.state()['queue'], ['U1', 'E1', 'E2'])

    def test_shared_owner_correction_reopens_all_prior_dispositions(self):
        self.inventory()
        p = self.seed(3)
        self.submit(self.trace_payload('unknown', reason='external runtime'))
        self.submit(self.trace_payload())
        p['mapping']['units'][0]['contract'] = 'corrected shared contract'
        self.submit(self.trace_payload(correction=p))
        s = self.state()
        self.assertEqual(s['map_rev'], 2)
        self.assertEqual(s['mapping']['queue'], ['E1', 'E2'])
        self.assertEqual(s['flows']['E3']['model_rev'], 2)
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.submit({'action': 'finish'})
        self.finish_mapping()
        self.assertEqual(self.state()['phase'], 'scope')

    def test_evidence_only_correction_does_not_reopen_completed_trace(self):
        self.inventory()
        p = self.seed()
        self.submit(self.trace_payload())
        p['mapping']['units'][0]['evidence'].append({'kind': 'inferred', 'where': 'src/a.py:2'})
        self.submit(self.trace_payload(correction=p))
        self.assertEqual(self.state()['mapping']['queue'], [])
        self.assertEqual(self.state()['map_rev'], 1)

    def test_finish_rejects_stale_trace_revision(self):
        self.inventory()
        self.seed(1)
        self.submit(self.trace_payload())
        s = self.state()
        s['flows']['E1']['model_rev'] = 0
        storage.write_json(flow._path(self.root), s)
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.submit({'action': 'finish'})

    def test_closure_validation_is_atomic_and_does_not_spend_steps(self):
        self.inventory()
        good = self.model()
        variants = []
        p = copy.deepcopy(good); p['mapping']['path_roles'] = {}; variants.append(p)
        p = copy.deepcopy(good); p['mapping']['units'][0]['evidence'][0]['where'] = 'src/a.py:9'; variants.append(p)
        p = copy.deepcopy(good); p['mapping']['units'][0]['owner'] = {'unknown': ''}; variants.append(p)
        p = copy.deepcopy(good); p['mapping']['units'][0]['capabilities'] = ['missing']; variants.append(p)
        p = copy.deepcopy(good); p['mapping']['exposure_checks'].pop('external_callers'); variants.append(p)
        p = copy.deepcopy(good); p['units'].append(dict(p['units'][0], id='U2')); variants.append(p)
        p = copy.deepcopy(good); p['mapping']['exposure_checks']['exports']['entries'] = []; variants.append(p)
        p = copy.deepcopy(good); p['mapping']['capabilities'][0]['parent'] = 'C1'; variants.append(p)
        before = self.state()
        for p in variants:
            with self.subTest(model=p), self.assertRaises(ValueError):
                self.submit(dict(p, action='seed'))
            self.assertEqual(self.state(), before)

    def test_overlapping_mapping_memberships_do_not_overlap_audit_partition(self):
        (self.root / 'other').mkdir()
        (self.root / 'other/b.ts').write_text('export const value: number = 1;\n')
        self.inventory()
        p = self.model(1)
        p['mapping']['capabilities'].append({'id': 'C2', 'purpose': 'coordinate features'})
        p['mapping']['units'].append(dict(p['mapping']['units'][0], id='M2',
                                          structural_home=['package other'], capabilities=['C1', 'C2']))
        p['mapping']['path_roles']['src/a.py']['units'] = ['M1', 'M2']
        p['mapping']['path_roles']['other/b.ts']['units'] = ['M2']
        p['units'][0]['map_units'] = ['M1', 'M2']
        self.submit(dict(p, action='seed'))
        profiles, _ = ledger.applicable_profiles(self.state())
        self.assertEqual(set(profiles), {'references/languages/python.md', 'references/languages/typescript.md'})
        self.finish_mapping()
        self.assertEqual(self.state()['phase'], 'scope')

    def test_no_executable_flows_is_valid_with_discovery_dispositions(self):
        (self.root / 'src/a.py').unlink()
        (self.root / 'README.md').write_text('Public data format: one integer per line.\n')
        self.evidence = lambda: [{'kind': 'observed', 'where': 'README.md:1'}]
        self.inventory()
        p = self.model(0)
        p['mapping']['units'][0].update(purpose='document data format', structural_home=['file README.md'])
        self.submit(dict(p, action='seed'))
        self.submit({'action': 'finish'})
        self.approve()
        self.assertEqual(self.state()['queue'], ['U1'])

    def test_trace_cannot_reopen_a_flow_removed_by_its_correction(self):
        self.inventory()
        self.seed(2)
        p = self.model(1)
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'unknown/removed'):
            self.submit(self.trace_payload(correction=p, reopen=['E2']))
        self.assertEqual(self.state(), before)

    def test_seed_cannot_import_audit_statuses_or_splits(self):
        self.inventory()
        p = self.model(0)
        p['units'][0].update(status='reviewed', children=['nonexistent'], passes=999)
        self.submit(dict(p, action='seed'))
        u = self.state()['units']['U1']
        self.assertEqual(u['status'], 'pending')
        self.assertEqual(u['passes'], 0)
        self.assertNotIn('children', u)
        self.submit({'action': 'finish'})
        self.assertEqual(self.state()['map_rev'], 1)

    def test_accepted_pending_trace_spends_allowance_but_does_not_claim_resolution(self):
        self.inventory(steps=2)
        self.seed(1)
        self.submit(self.trace_payload('pending', reason='no additional evidence yet'))
        self.assertEqual(self.state()['phase'], 'checkpoint')
        self.assertEqual(self.state()['flows']['E1']['map_status'], 'pending')
        self.assertEqual(self.state()['counters']['map'], 2)

    def test_model_rejects_non_text_ids_without_crashing(self):
        self.inventory()
        p = self.model()
        variants = []
        bad = copy.deepcopy(p); bad['mapping']['path_roles']['src/a.py']['units'] = [{}]; variants.append(bad)
        bad = copy.deepcopy(p); bad['mapping']['units'][0]['capabilities'] = [{}]; variants.append(bad)
        bad = copy.deepcopy(p); bad['mapping']['capabilities'][0]['parent'] = ['C1']; variants.append(bad)
        bad = copy.deepcopy(p); bad['entry_points'][0]['map_units'] = [{}]; variants.append(bad)
        bad = copy.deepcopy(p)
        bad['mapping']['capabilities'] = [{'id': 'C1', 'purpose': 'a', 'parent': 'C2'},
                                          {'id': 'C2', 'purpose': 'b', 'parent': 'missing'}]
        variants.append(bad)
        for bad in variants:
            with self.subTest(payload=bad), self.assertRaises(ValueError):
                self.submit(dict(bad, action='seed'))
        self.assertEqual(self.state()['counters']['map'], 0)

    def test_unknown_role_and_owner_are_explicit_not_automatic_credit(self):
        self.inventory()
        p = self.model(1)
        p['mapping']['path_roles']['src/a.py'] = {'unclassified': 'ownership unresolved'}
        p['mapping']['units'][0]['owner'] = {'unknown': 'deployment team unavailable'}
        self.submit(dict(p, action='seed'))
        self.finish_mapping(unknown=True)
        self.assertTrue(mapping.unknowns(self.state()))
        self.assertEqual(self.state()['units']['U1']['status'], 'pending')

    def test_no_progress_and_scope_revision_spend_cumulative_mapping_allowance(self):
        self.inventory(steps=3)
        self.seed(0)
        self.submit({'action': 'finish'})
        self.submit({'revise': 'review boundaries again'})
        self.submit(dict(self.model(0), action='seed'))
        s = self.state()
        self.assertEqual(s['phase'], 'checkpoint')
        self.assertEqual(s['counters']['map'], 3)
        self.submit({'approved_by_user': True, 'decision': 'extend', 'limits': {'max_map_steps': 5}})
        self.assertEqual(self.state()['phase'], 'map')
        self.submit({'action': 'finish'})
        self.assertEqual(self.state()['counters']['map'], 4)

    def test_reinventory_does_not_reset_counters_or_pass_usage(self):
        self.inventory()
        self.seed(0)
        self.finish_mapping()
        self.approve()
        self.review_all()
        self.submit({'checkpoint': 'rescan'})
        before = self.state()['counters'].copy()
        self.submit({'approved_by_user': True, 'decision': 'reinventory'})
        self.inventory()
        self.seed(0)
        self.assertEqual(self.state()['counters']['map'], before['map'] + 1)
        self.assertEqual(self.state()['counters']['units'], before['units'])
        self.assertEqual(self.state()['units']['U1']['passes'], 1)


class LifecycleTests(RunCase):
    def test_inventory_requires_actual_finite_limits_and_local_policy(self):
        for limits in ({'max_files': 2, 'max_bytes': 100},
                       {'max_files': True, 'max_bytes': 100, 'max_map_steps': 2}):
            with self.assertRaises(ValueError):
                self.submit({'approved_by_user': True, 'limits': limits, 'exclude': []})
        self.inventory()
        self.assertEqual(self.state()['policy']['root'], str(self.skill))
        self.assertNotIn('version', self.state()['policy'])

    def test_missing_required_reference_fails_before_analysis(self):
        (self.skill / 'references/principles.md').unlink()
        with self.assertRaisesRegex(ValueError, 'missing required local'):
            self.inventory()
        self.assertEqual(self.state()['phase'], 'inventory')

    def test_optional_missing_profile_is_a_disclosed_limitation(self):
        (self.skill / 'references/languages/python.md').unlink()
        self.inventory()
        profiles, limitations = ledger.applicable_profiles(self.state())
        self.assertEqual(profiles, [])
        self.assertTrue(any('python' in l for l in limitations))
        self.seed(0)
        out = exports.out_dir(self.state())
        self.assertIn('No local profile for python', (out / 'current.md').read_text())

    def test_output_changes_do_not_trigger_drift_but_source_delete_and_change_do(self):
        self.inventory()
        self.seed(1)
        out = exports.out_dir(self.state())
        (out / 'report.md').write_text('derived scratch')
        self.assertEqual(ledger.find_drift(self.state()), [])
        (self.root / 'src/a.py').write_text('changed source\n')
        self.assertIn('changed: src/a.py', ledger.find_drift(self.state()))
        (self.root / 'src/a.py').unlink()
        self.assertIn('deleted: src/a.py', ledger.find_drift(self.state()))

    def test_stale_run_phase_revision_and_duplicate_completions_are_rejected(self):
        s = self.state()
        p = {'run': s['audit_id'], 'phase': 'inventory', 'rev': 0, 'approved_by_user': True,
             'limits': {'max_files': 10, 'max_bytes': 10000, 'max_map_steps': 5}, 'exclude': []}
        for bad in (dict(p, run='other'), dict(p, phase='map'), dict(p, rev=True), dict(p, rev=1)):
            with self.assertRaisesRegex(ValueError, 'Stale'):
                flow.done(self.root, bad, 'driver')
        flow.done(self.root, p, 'driver')
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'Stale'):
            flow.done(self.root, p, 'driver')
        self.assertEqual(before, self.state())

    def test_pre_scope_checkpoint_extension_cannot_authorize_project_execution(self):
        self.inventory(steps=1)
        self.seed(1)
        self.assertEqual(self.state()['phase'], 'checkpoint')
        for change in ({'allowed_commands': ['pytest']}, {'budgets': {'max_units': 10}},
                       {'limits': {'max_map_steps': 1}}):
            with self.assertRaises(ValueError):
                self.submit(dict(approved_by_user=True, decision='extend', **change))
        self.submit({'approved_by_user': True, 'decision': 'extend', 'limits': {'max_map_steps': 4}})
        self.finish_mapping()
        self.assertEqual(self.state()['phase'], 'scope')

    def test_pre_scope_incomplete_publication_and_abandon_have_no_missing_scope_errors(self):
        self.inventory(steps=1)
        self.seed(1)
        out = exports.out_dir(self.state())
        self.submit({'approved_by_user': True, 'decision': 'publish_incomplete'})
        self.assertFalse(flow._path(self.root).exists())
        report = (out / 'report.md').read_text()
        self.assertIn('INCOMPLETE', report)
        self.assertIn('NOT APPROVED', report)
        flow.start(self.root, 'driver')
        out = exports.out_dir(self.state())
        flow.abandon(self.root)
        self.assertIn('INCOMPLETE', (out / 'report.md').read_text())

    def test_map_checkpoint_does_not_accept_claimed_command_execution(self):
        self.inventory()
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'not approved'):
            self.submit({'checkpoint': 'permission request', 'commands_run': ['pytest']})
        self.assertEqual(self.state(), before)

    def test_cli_end_to_end_with_explicit_space_containing_root(self):
        self.inventory()
        installed = Path(self.temp.name) / 'installed skill with spaces'
        shutil.copytree(LIVE_SKILL / 'scripts', installed / 'scripts')
        shutil.copytree(self.skill / 'references', installed / 'references')
        script = installed / 'scripts/workflow.py'
        canonical = installed / 'state' / storage.project_key(self.root) / 'workflow.json'

        def invoke(command, payload=None, guarded=True):
            if payload is not None and guarded:
                s = storage.read_json(canonical)
                payload = dict(run=s['audit_id'], phase=s['phase'], rev=s['rev'], **payload)
            result = subprocess.run([sys.executable, '-B', str(script), '--root', str(self.root), command],
                                    input=json.dumps(payload) if payload is not None else '',
                                    text=True, capture_output=True, cwd=self.temp.name)
            return result

        self.assertEqual(invoke('start').returncode, 0)
        rejected = invoke('done', {}, guarded=False)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn('Stale/duplicate', rejected.stderr)
        payloads = [
            {'approved_by_user': True, 'limits': {'max_files': 100, 'max_bytes': 100000, 'max_map_steps': 10}, 'exclude': []},
            dict(self.model(1), action='seed'),
            {'action': 'trace', 'flow': 'E1', 'disposition': 'traced', 'consumers': 'public caller',
             'calls': 'caller → run', 'effects': 'return', 'failure_cleanup': 'no resources', 'evidence': self.evidence()},
            {'action': 'finish'},
        ]
        for payload in payloads:
            result = invoke('done', payload)
            self.assertEqual(result.returncode, 0, result.stderr)
        s = storage.read_json(canonical)
        self.assertEqual(s['phase'], 'scope')
        result = invoke('done', {'approved_by_user': True, 'map_rev': s['map_rev'],
                        'budgets': dict(zip(checks.BUDGETS, [20, 10, 3, 3, 3, 2])), 'allowed_commands': []})
        self.assertEqual(result.returncode, 0, result.stderr)
        for payload in [
            {'item': 'U1', 'reviewed': ['src/a.py'], 'findings': []},
            {'item': 'E1', 'trace': ['src/a.py:1-4'], 'limitations': [], 'findings': []},
            {'recommendations': [], 'dismissed': [], 'waves': []},
            {'reviewer': 'self', 'objections': []},
        ]:
            result = invoke('done', payload)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DONE', result.stdout)
        self.assertFalse(canonical.exists())

    def test_export_failure_is_committed_then_status_repairs_without_advancing(self):
        self.inventory()
        old = self.state()
        p = dict(self.model(1), action='seed', run=old['audit_id'], phase=old['phase'], rev=old['rev'])
        with patch.object(exports, 'export', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError, 'COMMITTED.*run status'):
                flow.done(self.root, p, 'driver')
        committed = self.state()
        self.assertEqual(committed['rev'], old['rev'] + 1)
        with self.assertRaisesRegex(ValueError, 'Stale'):
            flow.done(self.root, p, 'driver')
        out = exports.out_dir(committed)
        (out / 'current.md').write_text('stale')
        (out.parent / 'audit-9999').mkdir()
        msg = flow.status(self.root, 'driver')
        self.assertIn(str(out), msg)
        self.assertEqual(committed, self.state())
        self.assertIn('MAP next action: trace', (out / 'current.md').read_text())
        self.assertEqual(storage.read_json(out / 'audit.json')['rev'], committed['rev'])

    def test_failed_canonical_save_does_not_export_uncommitted_state(self):
        self.inventory()
        original = self.state()
        with patch.object(storage, 'write_json', side_effect=OSError('save failed')):
            with patch.object(exports, 'export') as export:
                with self.assertRaises(OSError):
                    self.seed()
                export.assert_not_called()
        self.assertEqual(self.state(), original)

    def test_source_and_reference_drift_on_resume_requires_reinventory(self):
        for mutate in ('source', 'reference'):
            with self.subTest(mutate=mutate):
                self.inventory()
                self.seed(1)
                target = self.root / 'src/new.py' if mutate == 'source' else self.skill / 'references/languages/new.md'
                target.write_text('new\n')
                flow.status(self.root, 'driver')
                s = self.state()
                self.assertEqual(s['phase'], 'checkpoint')
                self.assertTrue(s['checkpoint']['drift'])
                with self.assertRaisesRegex(ValueError, 'drift requires'):
                    self.submit({'approved_by_user': True, 'decision': 'extend', 'limits': {'max_map_steps': 60}})
                self.submit({'approved_by_user': True, 'decision': 'reinventory'})

    def test_snapshot_read_failure_checkpoints_and_can_publish_incomplete(self):
        self.inventory()
        self.seed(1)
        out = exports.out_dir(self.state())
        with patch.object(ledger, 'find_drift', side_effect=PermissionError('unreadable source')):
            flow.status(self.root, 'driver')
            self.assertEqual(self.state()['phase'], 'checkpoint')
            self.assertIn('cannot validate snapshot', self.state()['checkpoint']['reason'])
            self.submit({'approved_by_user': True, 'decision': 'publish_incomplete'})
        self.assertIn('INCOMPLETE', (out / 'report.md').read_text())

    def test_legacy_state_is_preserved_refused_then_explicitly_archived(self):
        old = {'phase': 'map', 'legacy': ['keep me']}
        storage.write_json(flow._path(self.root), old)
        for action in (lambda: flow.status(self.root, 'driver'), lambda: flow.done(self.root, {}, 'driver')):
            with self.assertRaisesRegex(ValueError, 'Legacy'):
                action()
            self.assertEqual(self.state(), old)
        msg = flow.abandon(self.root)
        self.assertIn('archived INCOMPLETE', msg)
        archives = list((self.root / ledger.ART).glob('legacy-*/workflow.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(storage.read_json(archives[0]), old)
        flow.start(self.root, 'driver')
        self.assertEqual(self.state()['schema_version'], flow.SCHEMA_VERSION)


class AuditFeedbackTests(RunCase):
    def ready(self, unknown=False):
        self.inventory()
        self.seed(1)
        self.finish_mapping(unknown)
        self.approve()

    def test_end_to_end_static_publication_leaves_target_source_unchanged(self):
        before = (self.root / 'src/a.py').read_bytes()
        self.ready(unknown=True)
        self.submit({'item': 'U1', 'reviewed': ['src/a.py'], 'findings': [], 'commands_run': []})
        self.submit({'item': 'E1', 'trace': ['src/a.py:1-4'], 'limitations': ['external runtime selection unverified'],
                     'findings': [], 'commands_run': []})
        self.synthesize()
        out = exports.out_dir(self.state())
        result = self.submit({'reviewer': 'self', 'objections': []})
        self.assertIn('DONE', result)
        self.assertFalse(flow._path(self.root).exists())
        report = (out / 'report.md').read_text()
        self.assertIn('COMPLETE (static', report)
        self.assertIn('external runtime selection unverified', report)
        self.assertIn('self-review, not independent', report)
        self.assertEqual(before, (self.root / 'src/a.py').read_bytes())

    def test_required_missing_inspection_is_incomplete(self):
        self.ready()
        self.submit({'item': 'U1', 'blocked': 'source unavailable for careful inspection', 'findings': []})
        self.submit({'item': 'E1', 'blocked': 'required configuration unavailable', 'findings': []})
        self.synthesize()
        out = exports.out_dir(self.state())
        self.submit({'reviewer': 'self', 'objections': []})
        self.assertIn('INCOMPLETE', (out / 'report.md').read_text())

    def test_unapproved_project_command_rejected_without_mutation(self):
        self.ready()
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'not approved'):
            self.submit({'item': 'U1', 'reviewed': ['src/a.py'], 'findings': [], 'commands_run': ['pytest']})
        self.assertEqual(before, self.state())

    def test_map_challenge_preserves_history_but_invalidates_approval_and_reviews(self):
        self.ready()
        self.review_all()
        self.synthesize()
        old = self.state()
        self.submit({'reviewer': 'self', 'objections': [{'claim': 'wrong owner', 'verdict': 'accepted',
                     'reason': 'src/a.py:1 reveals shared owner', 'reopen': ['map', 'U1']}]})
        s = self.state()
        self.assertEqual(s['phase'], 'map')
        self.assertNotIn('scope', s)
        self.assertNotIn('recommendations', s)
        self.assertEqual(s['units']['U1']['status'], 'pending')
        self.assertEqual(s['units']['U1']['passes'], 1)
        self.assertEqual(s['history'][-1]['evidence']['units']['U1']['status'], 'reviewed')
        self.assertEqual(s['counters']['dissect'], old['counters']['dissect'])
        self.assertEqual(s['counters']['challenge'], 1)
        p = self.model(1)
        p['mapping']['units'][0]['owner'] = 'actual shared owner'
        self.submit(dict(p, action='seed'))
        self.assertEqual(self.state()['flows']['E1']['map_status'], 'pending')
        self.finish_mapping()
        self.approve()
        self.assertEqual(self.state()['queue'], ['U1', 'E1'])

    def test_finishing_remap_discards_invalidated_audit_splits(self):
        self.ready()
        self.submit({'item': 'U1', 'children': [
            {'id': 'U1a', 'responsibility': 'entry', 'paths': ['src/a.py:1-2']},
            {'id': 'U1b', 'responsibility': 'return', 'paths': ['src/a.py:3-4']}], 'findings': []})
        self.submit({'map_correction': 'review owner before continuing split work'})
        self.submit({'action': 'finish'})
        self.assertEqual(set(self.state()['units']), {'U1'})
        self.assertNotIn('children', self.state()['units']['U1'])
        self.assertEqual(self.state()['counters']['units'], 3)
        self.approve()
        self.assertEqual(self.state()['queue'], ['U1', 'E1'])

    def test_execution_permissions_extend_only_after_scope_and_are_only_recorded(self):
        self.ready()
        self.submit({'checkpoint': 'ask for an exact test command'})
        self.submit({'approved_by_user': True, 'decision': 'extend', 'allowed_commands': ['python -m unittest']})
        with patch('subprocess.run', side_effect=AssertionError('engine must not execute project commands')):
            self.submit({'item': 'U1', 'reviewed': ['src/a.py'], 'findings': [],
                         'commands_run': ['python -m unittest']})
        self.assertEqual(self.state()['commands_run'], ['python -m unittest'])

    def test_unrelated_extension_cannot_bypass_exhausted_challenge_budget(self):
        self.ready()
        self.review_all()
        self.synthesize()
        s = self.state()
        s['counters']['challenge'] = 2
        storage.write_json(flow._path(self.root), s)
        self.submit({'reviewer': 'self', 'objections': [{'claim': 'reconsider', 'verdict': 'accepted',
                     'reason': 'source evidence', 'reopen': ['synthesize']}]})
        with self.assertRaisesRegex(ValueError, 'extend challenge_rounds'):
            self.submit({'approved_by_user': True, 'decision': 'extend', 'allowed_commands': ['pytest']})
        self.submit({'approved_by_user': True, 'decision': 'extend', 'budgets': {'challenge_rounds': 3}})
        self.assertEqual(self.state()['phase'], 'synthesize')

    def test_dissect_contradiction_returns_to_map_before_finding_work(self):
        self.ready()
        self.submit({'map_correction': 'src/a.py:1 contradicts contract'})
        self.assertEqual(self.state()['phase'], 'map')
        self.assertNotIn('scope', self.state())
        self.assertEqual(self.state()['counters']['dissect'], 0)

    def test_interrupted_publication_retains_state_until_status_finishes(self):
        self.ready()
        self.review_all()
        self.synthesize()
        out = exports.out_dir(self.state())
        with patch.object(exports, 'render_report', side_effect=OSError('report interrupted')):
            with self.assertRaisesRegex(ValueError, 'COMMITTED'):
                self.submit({'reviewer': 'self', 'objections': []})
        self.assertEqual(self.state()['phase'], 'published')
        with self.assertRaisesRegex(ValueError, 'already committed'):
            self.submit({'reviewer': 'self', 'objections': []})
        flow.status(self.root, 'driver')
        self.assertFalse(flow._path(self.root).exists())
        self.assertIn('COMPLETE', (out / 'report.md').read_text())

    def test_publication_resume_cannot_claim_complete_after_source_drift(self):
        self.ready()
        self.review_all()
        self.synthesize()
        with patch.object(exports, 'render_report', side_effect=OSError('interrupted')):
            with self.assertRaises(ValueError):
                self.submit({'reviewer': 'self', 'objections': []})
        (self.root / 'src/a.py').write_text('changed\n')
        flow.status(self.root, 'driver')
        self.assertEqual(self.state()['phase'], 'checkpoint')
        self.assertNotIn('published', self.state())

    def test_remapping_after_exhausted_challenge_requires_approved_extension(self):
        self.ready()
        self.review_all()
        self.synthesize()
        s = self.state()
        s['counters']['challenge'] = 2
        storage.write_json(flow._path(self.root), s)
        self.submit({'reviewer': 'self', 'objections': [{'claim': 'owner wrong', 'verdict': 'accepted',
                     'reason': 'src/a.py:1 evidence', 'reopen': ['map']}]})
        self.assertEqual(self.state()['phase'], 'checkpoint')
        with self.assertRaisesRegex(ValueError, 'extend challenge_rounds'):
            self.submit({'approved_by_user': True, 'decision': 'extend', 'limits': {'max_map_steps': 50}})
        self.submit({'approved_by_user': True, 'decision': 'extend', 'budgets': {'challenge_rounds': 3}})
        self.assertEqual(self.state()['phase'], 'map')
        self.submit({'action': 'finish'})
        with self.assertRaisesRegex(ValueError, 'cannot be reset'):
            self.approve(challenge_rounds=2)
        self.approve(challenge_rounds=3)
        self.assertEqual(self.state()['counters']['challenge'], 3)

    def test_interrupted_abandon_still_finishes_incomplete_after_source_change(self):
        self.ready()
        out = exports.out_dir(self.state())
        with patch.object(exports, 'render_report', side_effect=OSError('interrupted')):
            with self.assertRaises(ValueError):
                flow.abandon(self.root)
        (self.root / 'src/a.py').write_text('changed after abandonment\n')
        flow.status(self.root, 'driver')
        self.assertFalse(flow._path(self.root).exists())
        self.assertIn('INCOMPLETE', (out / 'report.md').read_text())

    def test_traced_flow_with_explicit_unknown_effect_keeps_uncertainty_visible(self):
        self.inventory()
        self.seed(1)
        p = self.trace_payload()
        p['effects'] = {'unknown': 'dynamic external routing'}
        self.submit(p)
        self.submit({'action': 'finish'})
        self.assertTrue(any('dynamic external routing' in u for u in mapping.unknowns(self.state())))

    def test_scope_exclusions_are_reversible_after_map_reopen(self):
        (self.root / 'generated.py').write_text('generated = True\n')
        self.inventory()
        p = self.model(1)
        p['exclusions'] = [{'glob': 'generated.py', 'reason': 'generated from src/a.py'}]
        p['mapping']['path_roles'].pop('generated.py')
        p['units'][0]['paths'] = ['src/a.py']
        self.submit(dict(p, action='seed'))
        self.finish_mapping()
        self.approve()
        self.assertEqual(self.state()['ledger']['generated.py']['disposition'], 'excluded')
        self.submit({'map_correction': 'review excluded maintained input boundary'})
        self.assertEqual(self.state()['ledger']['generated.py']['disposition'], 'pending')
        p = self.model(1)
        self.submit(dict(p, action='seed'))
        self.assertIn('generated.py', self.state()['mapping']['path_roles'])

    def test_unknown_affected_track_a_requires_explicit_resolution(self):
        self.ready(unknown=True)
        f = {'id': 'F1', 'where': 'src/a.py:2', 'summary': 'simplify guard', 'mechanism': 'redundant nesting',
             'option': 'early return', 'evidence': 'observed', 'track': 'A',
             'policy': {'tier': 'reference', 'source': 'principles.md'}, 'current_behavior': 'optional value',
             'consumers': ['public caller'], 'map_units': ['M1'], 'flow_ids': ['E1'], 'uncertainty': 'none'}
        with self.assertRaisesRegex(ValueError, 'uncertainty_resolution'):
            self.submit({'item': 'U1', 'reviewed': ['src/a.py'], 'findings': [f]})
        f['track'] = 'gap'
        self.submit({'item': 'U1', 'reviewed': ['src/a.py'], 'findings': [f]})
        self.assertEqual(self.state()['findings']['F1']['track'], 'gap')

    def test_split_requires_exact_partition_and_parent_interactions(self):
        self.ready()
        kids = [{'id': 'U1a', 'responsibility': 'entry', 'paths': ['src/a.py:1-2']},
                {'id': 'U1b', 'responsibility': 'return', 'paths': ['src/a.py:3-4']}]
        with self.assertRaises(ValueError):
            self.submit({'item': 'U1', 'children': [kids[0], kids[0]], 'findings': []})
        self.submit({'item': 'U1', 'children': kids, 'findings': []})
        for kid in kids:
            self.submit({'item': kid['id'], 'reviewed': kid['paths'], 'findings': []})
        self.assertEqual(self.state()['queue'][0], 'U1')
        self.submit({'item': 'U1', 'interactions': 'children share only local value; unchanged boundary', 'findings': []})
        self.assertEqual(self.state()['queue'], ['E1'])


if __name__ == '__main__':
    unittest.main()
