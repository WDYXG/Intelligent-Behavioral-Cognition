"""Paired diagnostic isolation, legal fixtures and failure preservation."""
import json
from pathlib import Path
import tempfile
import unittest
from public_goods.agents.cognition import NORM_FIELDS, ORIGINAL_REVISION, SCOPE_REVISION, PROMPT_REVISION
from public_goods.agents.runner import DecisionFailed
from public_goods.environment.world import World
from public_goods.experiments.paired_diagnostic import CASES, build_case, plan, run_diagnostic
from public_goods.models.client import MockModel


def strip(value):
    if isinstance(value, dict):
        return {k:strip(v) for k,v in value.items() if k not in NORM_FIELDS}
    if isinstance(value,list):
        return [strip(v) for v in value]
    return value


class PairedDiagnosticTests(unittest.TestCase):
    def test_resume_preserves_failed_probe_and_only_attempts_remaining_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = Path(folder)/'old', Path(folder)/'new'
            with self.assertRaises(DecisionFailed):
                run_diagnostic(old, MockModel(['invalid']*3), compare_revisions=True)
            before = (old/'report.json').read_bytes()
            model = MockModel()
            result = run_diagnostic(new, model, compare_revisions=True, resume_from=old)
            self.assertEqual(result['status'], 'completed_with_probe_failures')
            self.assertEqual(len(result['results']), 28)
            self.assertEqual(result['results'][0]['status'], 'failed')
            self.assertNotIn('action', result['results'][0])
            self.assertEqual(len(model.requests), 54)
            self.assertEqual(result['attempts'], 57)
            self.assertEqual((old/'report.json').read_bytes(), before)
            with self.assertRaises(ValueError):
                run_diagnostic(Path(folder)/'wrong', MockModel(), 2, compare_revisions=True, resume_from=old)
            with self.assertRaisesRegex(ValueError, 'prompt_revisions'):
                run_diagnostic(Path(folder)/'wrong-baseline', MockModel(), compare_revisions=True,
                               baseline_revision=ORIGINAL_REVISION, resume_from=old)

    def test_revision_comparison_repeats_complete_pairs_with_counterbalanced_order(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'comparison'
            report = run_diagnostic(out, MockModel(), 2, compare_revisions=True)
            self.assertEqual(report['attempts'], 112)
            self.assertEqual(len(report['results']), 56)
            self.assertEqual(len(report['contrasts']), 24)
            for repetition in range(2):
                for index, case in enumerate(CASES):
                    group = [r for r in report['results'] if r['case'] == case and r['repetition'] == repetition]
                    self.assertEqual(len({r['observation_sha256'] for r in group}), 1)
                    self.assertEqual({(r['arm'], r['prompt_revision']) for r in group},
                                     {(a, v) for a in ('B', 'C') for v in (SCOPE_REVISION, PROMPT_REVISION)})
                    for arm in ('B', 'C'):
                        pair = [r for r in group if r['arm'] == arm]
                        calls = [[json.loads(line) for line in (out/r['artifact']/'calls.jsonl').read_text().splitlines()] for r in pair]
                        for left, right in zip(*calls):
                            for key in ('seed', 'temperature', 'max_output_tokens'):
                                self.assertEqual(left['request'][key], right['request'][key])
                    self.assertNotEqual(group[0]['prompt_revision'], group[2]['prompt_revision'])
            self.assertEqual(plan(2, compare_revisions=True)['calls_with_max_retries'], 336)

    def test_explicit_original_baseline_and_invalid_comparison_selection(self):
        self.assertEqual(plan()['prompt_revisions'], [PROMPT_REVISION])
        self.assertEqual(plan(compare_revisions=True)['prompt_revisions'], [SCOPE_REVISION, PROMPT_REVISION])
        for baseline in (PROMPT_REVISION, 'unknown', False):
            with self.assertRaises(ValueError):
                plan(compare_revisions=True, baseline_revision=baseline)
        with self.assertRaises(ValueError):
            plan(baseline_revision=SCOPE_REVISION)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/'original'
            report = run_diagnostic(out, MockModel(), compare_revisions=True, baseline_revision=ORIGINAL_REVISION)
            self.assertEqual(report['attempts'], 56)
            self.assertEqual({r['prompt_revision'] for r in report['results']}, {ORIGINAL_REVISION, PROMPT_REVISION})
            saved = json.loads((out/'plan.json').read_text())
            self.assertEqual(saved['prompt_revisions'], [ORIGINAL_REVISION, PROMPT_REVISION])
            blocked = Path(folder)/'blocked'
            model = MockModel()
            with self.assertRaises(ValueError):
                run_diagnostic(blocked, model, baseline_revision=ORIGINAL_REVISION)
            self.assertFalse(blocked.exists())
            self.assertEqual(model.requests, [])

    def test_fixtures_replay_and_hide_researcher_information(self):
        for name in CASES:
            world,view=build_case(name)
            self.assertEqual(World.replay(world.audit_log()).observe('member-a'),view)
            for forbidden in ('researcher_only_display_order','member-b'):
                self.assertNotIn(forbidden,json.dumps(view))
            if name.startswith('N_'):
                for forbidden in ('punishment','deduction'):
                    self.assertNotIn(forbidden,json.dumps(view))
        low,high=[build_case(n)[1] for n in ('N_low_history','N_high_history')]
        self.assertEqual(low['round'],4)
        self.assertEqual(low['history'][-1]['group_total_contribution'],10)
        self.assertEqual(high['history'][-1]['group_total_contribution'],70)

    def test_received_contrast_includes_income_consequence(self):
        no,yes=[build_case(n)[1] for n in ('P_no_received','P_received')]
        self.assertEqual((yes['phase'],yes['stage'],yes['round']),('P','contribution',2))
        self.assertEqual(no['history'][:-1],yes['history'][:-1])
        self.assertEqual(no['history'][-1]['received_points'],0)
        self.assertEqual(yes['history'][-1]['received_points'],2)
        self.assertEqual(no['total_income']-yes['total_income'],6)
        self.assertEqual(no['history'][-1]['other_contributions'],yes['history'][-1]['other_contributions'])

    def test_pairs_share_context_and_proposals_are_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'run'
            report=run_diagnostic(out,MockModel())
            self.assertEqual((report['status'],report['attempts'],report['real_api_calls']),('completed',28,0))
            self.assertEqual(len(report['results']),14)
            self.assertEqual(len(report['contrasts']),6)
            self.assertEqual(report['semantic_review_status'],'not_reviewed')
            self.assertFalse(report['human_likeness_evaluated'])
            for name in CASES:
                base=out/'repeat-0'/name
                b,c=[[json.loads(s) for s in (base/a/'calls.jsonl').read_text().splitlines()] for a in ('B','C')]
                for left,right in zip(b,c):
                    lp,rp=[json.loads(x['request']['messages'][1]['content']) for x in (left,right)]
                    self.assertEqual(strip(lp),rp)
                    self.assertEqual(left['request']['seed'],right['request']['seed'])
                for arm in ('B','C'):
                    result=json.loads((base/arm/'probe.json').read_text())
                    self.assertFalse(result['action_executed'])
                    self.assertFalse(result['state_committed'])
                    self.assertFalse((base/arm/'trajectory.jsonl').exists())
                    self.assertFalse((base/arm/'state_transitions.jsonl').exists())
                    self.assertEqual([json.loads(s) for s in (base/arm/'events.jsonl').read_text().splitlines()],build_case(name)[0].audit_log())
            with self.assertRaises(FileExistsError):
                run_diagnostic(out,MockModel())

    def test_failed_probe_preserves_attempts_and_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'failed'
            with self.assertRaises(DecisionFailed):
                run_diagnostic(out,MockModel(['invalid']*3))
            report=json.loads((out/'report.json').read_text())
            self.assertEqual((report['status'],report['attempts']),('failed',3))
            self.assertEqual(len(report['results']),1)
            self.assertNotIn('action',report['results'][0])

    def test_plan_bounds_and_live_gate(self):
        self.assertEqual(plan()['calls_with_max_retries'],84)
        for bad in (0,4,True,1.5):
            with self.assertRaises(ValueError):
                plan(bad)
        model=MockModel()
        model.mode='live'
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'blocked'
            with self.assertRaises(ValueError):
                run_diagnostic(out,model)
            self.assertFalse(out.exists())
            self.assertEqual(model.requests,[])
