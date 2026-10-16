"""Offline, network-blocked checks. Stub responses are NOT real runtime evidence."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import STEP, canonical, digest
from llm import CostLedger, preflight
from population import load_population
from runner import run_one
from run_runtime_smoke import RuntimeAuditLog
from run_two_decision_readiness import resolve_readiness, readiness_gates


class TwoDecisionOffline(unittest.TestCase):
    def setUp(self):
        self.guards = [patch('socket.socket', side_effect=AssertionError('Offline test forbids network')),
                       patch('socket.create_connection', side_effect=AssertionError('Offline test forbids network'))]
        for guard in self.guards:
            guard.start()
            self.addCleanup(guard.stop)
        self.path = STEP / '03_experiment/configs/two_decision_readiness_v1.json'
        _, smoke, self.cfg, _ = resolve_readiness(self.path)
        population, _ = load_population(self.cfg, self.path.parent)
        self.member = next(m for m in population if m['alias'] == smoke['selected_alias'])
        api = self.cfg['api']
        with patch.dict('os.environ', {api['endpoint_env']: 'https://invalid.example/chat/completions',
                api['model_env']: 'offline-stub-not-real-model', api['key_env']: 'offline-placeholder'}, clear=True):
            self.resolved = preflight(self.cfg, population_count=1)

    def stub_run(self):
        ledger, captured, sent = CostLedger(self.cfg), {}, []
        def open_stub(request, timeout):
            body = json.loads(request.data)
            sent.append(body)
            ctx = json.loads(body['messages'][-1]['content'])
            turn = ctx['round']
            decision = {'action': 'MOVE', 'arguments': {'amount': 1}, 'cognition': {
                'judgments': [], 'intent_updates': [{'op': 'create' if turn == 1 else 'continue',
                    'task_id': 'fixture_task', 'title': 'Fixture move', 'next_step': 'Move one unit',
                    'basis_refs': [ctx['Observation']['observation_id']]}], 'action_task_id': 'fixture_task'}}
            return io.BytesIO(canonical({'id': f'offline-stub-{turn}', 'model': 'offline-stub-not-real-model',
                'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(decision)}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}}))
        def validate(world, agents, log, budget):
            result = readiness_gates(self.member)(world, agents, log, budget)
            captured.update(world=world, agents=agents, rows=copy.deepcopy(log.rows), ledger=budget, report=result)
            return result
        with tempfile.TemporaryDirectory(prefix='two-decision-offline-') as directory:
            with patch('urllib.request.build_opener', return_value=SimpleNamespace(open=open_stub)):
                run = run_one(Path(directory) / 'offline_stub_only', [self.member], self.cfg, self.resolved,
                    ledger, self.cfg['seed'], 1, log_factory=RuntimeAuditLog, runtime_validator=validate)
            self.assertEqual(run['status'], 'completed', run.get('error'))
        return captured, sent

    def test_caps_and_no_retry(self):
        self.assertEqual(self.resolved['estimate']['first_attempt_calls'], 2)
        self.assertEqual(self.resolved['estimate']['first_attempt_reserved_tokens'], 73728)
        self.assertEqual(self.cfg['api']['max_retries'], 0)
        ledger = CostLedger(self.cfg)
        ledger.reserve(); ledger.reserve()
        with self.assertRaisesRegex(ValueError, 'budget_call_limit'):
            ledger.reserve()

    def test_shared_loop_feedback_in_second_wire_body(self):
        captured, sent = self.stub_run()
        self.assertEqual(len(sent), 2)
        ctx = json.loads(sent[1]['messages'][-1]['content'])
        self.assertEqual(ctx['P'], self.member['view'])
        self.assertEqual(ctx['X']['version'], 3)
        self.assertEqual(ctx['X']['cognition_revision'], 1)
        self.assertEqual(ctx['episodes'][0]['observation_id'], 'smoke:o2:resident_01')
        self.assertEqual(ctx['episodes'][0]['context_role'], 'mandatory_new_delivery')
        self.assertEqual(ctx['episodes'][0]['action_result']['request_id'], 'r1:move:resident_01')
        self.assertEqual(ctx['X']['tasks']['fixture_task']['last_result']['request_id'], 'r1:move:resident_01')
        self.assertEqual(captured['report']['chains'][1]['dispatch_messages_sha256'], digest(sent[1]['messages']))
        self.assertTrue(captured['report']['passed'])

    def test_auditor_rejects_tampered_feedback_hash_state_and_authorization(self):
        captured, _ = self.stub_run()
        for case in ('missing_feedback', 'duplicate', 'foreign', 'hash', 'state', 'budget'):
            with self.subTest(case=case):
                rows = copy.deepcopy(captured['rows'])
                p = [r['payload'] for r in rows if r['type'] == 'decision_input'][1]
                if case == 'missing_feedback':
                    p['context']['episodes'] = []
                elif case == 'duplicate':
                    p['context']['episodes'].append(copy.deepcopy(p['context']['episodes'][0]))
                elif case == 'foreign':
                    p['context']['episodes'][0]['action_result']['agent_id'] = 'unauthorized_actor'
                elif case == 'hash':
                    p['messages_sha256'] = 'invalid'
                elif case == 'state':
                    p['context']['X']['version'] = 99
                elif case == 'budget':
                    [r['payload'] for r in rows if r['type'] == 'context_budget_check'][1]['budget']['fits'] = False
                report = readiness_gates(self.member)(captured['world'], captured['agents'], SimpleNamespace(rows=rows), captured['ledger'])
                self.assertFalse(report['passed'])

    def test_missing_environment_no_fallback(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'missing environment variable'):
                preflight(self.cfg, population_count=1)


if __name__ == '__main__':
    unittest.main()
