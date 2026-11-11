"""Characterize audit gates retained by the mapping integration."""
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workflowlib import checks, work


def audit_state():
    return {'rev': 4, 'ledger': {'a.py': {'kind': 'file', 'lines': 4, 'sha256': 'abc',
            'disposition': 'pending', 'lang': 'python'}},
            'units': {'U': {'kind': 'unit', 'paths': ['a.py'], 'status': 'pending', 'depth': 0,
                            'passes': 0}}, 'flows': {}, 'findings': {}, 'queue': ['U'],
            'counters': {'dissect': 0, 'units': 1, 'synth': 0, 'challenge': 0},
            'scope': {'allowed_commands': [], 'budgets': dict(zip(checks.BUDGETS, [3, 4, 2, 3, 2, 2]))}}


class BaselineTests(unittest.TestCase):
    def test_range_coverage_never_credits_partial_file(self):
        s = audit_state()
        s['units']['U'].update(paths=['a.py:1-2'], status='reviewed')
        self.assertEqual(work.coverage_summary(s)['unreviewed'], ['a.py'])
        s['units']['V'] = dict(s['units']['U'], paths=['a.py:3-4'])
        self.assertEqual(work.coverage_summary(s)['reviewed'], 1)

    def test_parent_interaction_is_separate_work(self):
        s = audit_state()
        s['units']['U'].update(children=['C'], status='pending')
        s['units']['C'] = dict(s['units']['U'], paths=['a.py'], status='reviewed')
        self.assertEqual(work.coverage_summary(s)['pending'], ['U'])

    def test_dissect_budget_and_passes_checkpoint(self):
        s = audit_state()
        s['counters']['dissect'] = 3
        self.assertEqual(work.next_work(s), 'checkpoint')
        s = audit_state()
        s['units']['U']['passes'] = 2
        self.assertEqual(work.next_work(s), 'checkpoint')

    def test_scope_requires_approval_exact_map_and_commands(self):
        s = audit_state()
        s['map_rev'] = 2
        p = {'approved_by_user': True, 'map_rev': 2, 'budgets': s['scope']['budgets'],
             'allowed_commands': []}
        checks.check_scope(s, p)
        for change in ({'approved_by_user': False}, {'map_rev': 1}, {'allowed_commands': [42]}):
            with self.assertRaises(ValueError):
                checks.check_scope(s, dict(p, **change))

    def test_track_a_cannot_promote_gap_or_depend_on_b(self):
        s = audit_state()
        s['findings'] = {'F': {'track': 'gap'}}
        a = {'id': 'A', 'track': 'A', 'title': 'x', 'change': 'x', 'verification': 'planned',
             'findings': ['F'], 'preserves': 'x'}
        with self.assertRaisesRegex(ValueError, 'B or gap'):
            checks.check_synthesize(s, {'recommendations': [a]})
        s['findings']['F']['track'] = 'A'
        b = dict(a, id='B', track='B', pitch={k: 'decision needed' for k in checks.PITCH})
        a['depends_on'] = ['B']
        with self.assertRaisesRegex(ValueError, 'bargain'):
            checks.check_synthesize(s, {'recommendations': [a, b]})

    def test_findings_must_be_synthesized_or_dismissed(self):
        s = audit_state()
        s['findings'] = {'F': {}}
        with self.assertRaisesRegex(ValueError, 'neither used nor dismissed'):
            checks.check_synthesize(s, {'recommendations': []})
        checks.check_synthesize(s, {'recommendations': [], 'dismissed': [{'finding': 'F', 'reason': 'duplicate'}]})

    def test_unknown_challenge_target_rejected(self):
        with self.assertRaises(ValueError):
            checks.check_challenge(audit_state(), {'reviewer': 'self', 'objections': [
                {'claim': 'x', 'reason': 'x', 'verdict': 'accepted', 'reopen': ['missing']}]})

    def test_reopening_preserves_pass_usage(self):
        s = audit_state()
        s['units']['U'].update(status='reviewed', passes=2)
        s['queue'] = []
        work.reopen_items(s, ['U'], 'test')
        self.assertEqual(s['queue'], ['U'])
        self.assertEqual(s['units']['U']['passes'], 2)


if __name__ == '__main__':
    unittest.main()
