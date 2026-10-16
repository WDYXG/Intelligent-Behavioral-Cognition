"""Phase I-A only: synthetic static fixtures, no World loop, credentials or network.

Run with community-agent: python -B -m unittest discover -s
step/02_agent_world/python_runtime -p test_phase_ia.py -v
Gate prefixes A..I identify the frozen offline acceptance requirements.
"""
import ast
import copy
import io
import json
import socket
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from agent import Agent
from common import canonical, digest, file_hash, EventLog
from context_builder import ContextBuilder, TokenEstimator, build_draft, select_history
from episodic_memory import BoundedRelevantPolicy, MemoryChoice
from llm import LLMClient, CostLedger, make_messages
from observation import ObservationDispatcher
from gm import GM


def setUpModule():
    # Guard the entire suite, including tests that intentionally inspect dispatch.
    global guards
    guards = [patch('socket.socket', side_effect=AssertionError('I-A forbids network')),
              patch('socket.create_connection', side_effect=AssertionError('I-A forbids network')),
              patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('I-A forbids HTTP')),
              patch('urllib.request.urlopen', side_effect=AssertionError('I-A forbids HTTP'))]
    for guard in guards:
        guard.start()


def tearDownModule():
    for guard in reversed(guards):
        guard.stop()


def config():
    return {'learning_enabled': False, 'observation': {'memory_window': 2},
            'api': {'max_input_tokens': 100000, 'max_output_tokens': 128,
                    'temperature': 0, 'response_format': None, 'send_seed': False,
                    'max_retries': 0, 'timeout_seconds': 1, 'max_response_bytes': 4096,
                    'input_usd_per_million': 1, 'output_usd_per_million': 1},
            'budget': {'max_calls': 2, 'max_tokens': 300000, 'max_usd': 1}}


def event(identity='e1', audience=None, sequence=1, request=None):
    return {'event_id': identity, 'visibility': audience or ['a'], 'round': 1,
            'world_sequence': sequence, 'phase': 'fixture', 'request_id': request,
            'epistemic_status': 'reported_speech_not_verified', 'payload': {'text': 'fixture utterance'}}


def result(identity='q1', actor='a', accepted=True):
    return {'request_id': identity, 'agent_id': actor, 'round': 1, 'phase': 'fixture',
            'accepted': accepted, 'executed_action': 'SAY' if accepted else None,
            'rejection_reason': None if accepted else 'permission_denied', 'effects': {}, 'task_id': None}


def observation(identity='o1', events=None, feedback=None, actor='a'):
    return {'observation_id': identity, 'recipient': actor, 'visibility': [actor],
            'round': 1, 'world_version': 1, 'phase': 'fixture:opening',
            'content': {'decision_phase': 'fixture', 'scene': {
                'action_space': {'SAY': {}, 'TRANSFER': {}},
                'capability_view': {'TRANSFER': True}, 'execution_permissions': {'TRANSFER': False}},
                'self': {'resource': 1}, 'events': events or [], 'action_result': feedback, 'event_cursors': {}}}


def actor():
    return Agent({'alias': 'a', 'initial': {'P': {'authorized': None, 'target_only': 'NEVER_SEND'}, 'M0': {}},
                  'view': {'authorized': None}})


class Capture:
    def __init__(self):
        self.rows = []

    def add(self, kind, payload):
        self.rows.append({'type': kind, 'payload': copy.deepcopy(payload)})


class StaticObservationSource:
    """No environment advancement; only returns an already-constructed observation."""
    def __init__(self, obs):
        self.obs = obs
        self.state = {'round': 1, 'version': 1}

    def observe(self, alias, phase, feedback, since=None):
        view = copy.deepcopy(self.obs)
        view['content']['action_result'] = copy.deepcopy(feedback)
        return view


class StaticSettlement:
    """Tiny GM interface double, NOT a scientific Minimal Scene or a simulation."""
    aliases = ['a']

    def __init__(self):
        self.state = {'round': 1, 'version': 1, 'resources': 1, 'utterances': []}

    def snapshot(self):
        return copy.deepcopy(self.state)

    def validate_phase(self, phase):
        if phase != 'fixture':
            raise ValueError('invalid_fixture_phase')

    def check_preconditions(self, trial, request, before):
        if request['action'] == 'TRANSFER':
            raise ValueError('permission_denied')

    def execute_action(self, trial, request, before):
        trial['utterances'].append(request['arguments']['text'])
        return {'utterance_only': True}, ['a']

    def assert_invariants(self, state):
        assert state['resources'] == 1

    def collect_world_events(self, before, after, phase):
        return []

    def settle_phase(self, state, phase):
        return {}

    def rejection_options(self, request, before, reason):
        return ['inspect permissions']


class PhaseIA(unittest.TestCase):
    def setUp(self):
        self.cfg, self.log = config(), Capture()

    def draft(self, observations=None, seen_events=(), seen_results=()):
        agent = actor()
        for obs in observations or [observation()]:
            agent.deliver(obs, self.log)
        agent._context_event_ids.update(seen_events)
        agent._context_result_ids.update(seen_results)
        return agent.context_draft(self.cfg, 1, 17)

    def prepare(self, draft=None):
        return ContextBuilder(self.cfg, 'fixture-model', make_messages).prepare(draft or self.draft())

    def client(self):
        return LLMClient(self.cfg, {'model': 'fixture-model', 'endpoint': 'https://invalid.example', 'key': 'fixture-not-a-key'},
                         CostLedger(self.cfg), self.log)

    def test_A_authorized_view_excludes_source_target(self):
        prepared = self.prepare(self.draft([observation(events=[event()])]))
        self.assertIn('e1', prepared.audit['final_event_ids'])
        self.assertNotIn('NEVER_SEND', str(prepared.messages))
        self.assertEqual(prepared.context['P'], {'authorized': None})

    def test_A_foreign_observation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'recipient'):
            self.draft([observation(actor='b')])

    def test_A_mixed_private_history_rejected(self):
        history = [observation('old', [event(audience=['b'])]), observation('now')]
        with self.assertRaisesRegex(ValueError, 'Unauthorized'):
            select_history(history, {}, set(), set(), BoundedRelevantPolicy({'recent_limit': 5, 'related_limit': 0, 'event_limit': 5}))

    def test_A_feedback_owner_checked_at_delivery(self):
        a = actor()
        with self.assertRaisesRegex(ValueError, 'feedback recipient'):
            a.deliver(observation(feedback=result(actor='b')), self.log)
        self.assertEqual(a.episodes, [], 'Unauthorized delivery must not mutate state')

    def test_A_feedback_owner_checked_in_history(self):
        with self.assertRaisesRegex(ValueError, 'feedback recipient'):
            select_history([observation('old', feedback=result(actor='b')), observation('now')], {}, set(), set(),
                           BoundedRelevantPolicy({'recent_limit': 2, 'related_limit': 0, 'event_limit': 1}))

    def test_A_future_or_foreign_policy_reference_rejected(self):
        policy = Mock()
        policy.select.return_value = [MemoryChoice('old', ('undelivered-secret',), False, 1)]
        with self.assertRaisesRegex(ValueError, 'undelivered'):
            select_history([observation('old', [event()]), observation('now')], {}, {'e1'}, set(), policy)
        policy.select.assert_called_once()

    def test_B_required_inputs_preserved_with_empty_policy(self):
        self.cfg['context'] = {'memory_policy': {'options': {'recent_limit': 0, 'related_limit': 0, 'event_limit': 0}}}
        d = self.draft([observation('old', [event()], result()), observation('now', [event('e2', sequence=2)])])
        p = self.prepare(d)
        for key in ('P', 'M', 'X', 'Observation'):
            self.assertEqual(p.context[key], d.context[key], key + ' must remain intact')
        self.assertEqual(p.audit['final_event_ids'], ['e1', 'e2'])
        self.assertEqual(p.audit['final_result_ids'], ['q1'])
        self.assertEqual(p.context['Observation']['content']['scene']['execution_permissions'], {'TRANSFER': False})

    def test_B_required_overflow_blocks_dispatch_no_wait(self):
        self.cfg['api']['max_input_tokens'] = 1024
        client = self.client()
        p = client.prepare(self.draft([observation(events=[event()])]))
        self.assertFalse(p.audit['budget']['fits'])
        self.assertEqual(p.audit['final_event_ids'], ['e1'])
        with self.assertRaisesRegex(ValueError, 'exceeds input reservation'):
            client.decide('a', 1, p)
        self.assertEqual(client.ledger.calls, 0)
        self.assertFalse(any(r['type'] == 'api_request' for r in self.log.rows))

    def test_B_live_task_and_judgment_survive_optional_pruning(self):
        a = actor()
        a.deliver(observation('old', [event()]), self.log)
        a.deliver(observation('current'), self.log)
        a._context_event_ids.add('e1')
        a.X['judgments'] = [{'text': 'fixture uncertain judgment', 'basis_refs': ['old']}]
        a.X['tasks'] = {'t1': {'status': 'active', 'title': 'fixture task', 'next_step': 'inspect',
                             'history': [{'not_for_prompt': 'full local history'}], 'attempts': []}}
        before = a.snapshot()
        d = a.context_draft(self.cfg, 1, 17)
        self.cfg['api']['max_input_tokens'] = 1024
        p = self.prepare(d)
        self.assertFalse(p.audit['budget']['fits'])
        self.assertEqual(p.context['X']['tasks']['t1']['next_step'], 'inspect')
        self.assertEqual(p.context['X']['judgments'], before['X']['judgments'])
        self.assertEqual(a.snapshot(), before, 'Local full research state must remain untouched')

    def test_A_future_event_history_rejected(self):
        ev = event(); ev['round'] = 2
        with self.assertRaisesRegex(ValueError, 'future'):
            self.prepare(self.draft([observation(events=[ev])]))

    def test_C_communication_feedback_not_retrieved_twice(self):
        obs = [observation('comm_feedback', [event()], result()), observation('action_open', [event()], result())]
        p = self.prepare(self.draft(obs, ['e1'], ['q1']))
        self.assertEqual(p.context['episodes'], [])
        self.assertEqual(len(p.context['Observation']['content']['events']), 1)
        self.assertEqual(len(p.audit['retrieval']['duplicates']), 2)

    def test_C_history_event_and_request_dedup_latest_owner(self):
        p = self.prepare(self.draft([observation('old', [event()], result()),
                                     observation('newer', [event()], result()), observation('current')]))
        self.assertEqual(len(p.context['episodes']), 1)
        self.assertEqual(p.context['episodes'][0]['observation_id'], 'newer')

    def test_C_conflicting_event_payload_fails(self):
        changed = event(); changed['payload']['text'] = 'different'
        with self.assertRaisesRegex(ValueError, 'Conflicting payload'):
            self.prepare(self.draft([observation('old', [event()]), observation('new', [changed])]))

    def test_C_conflicting_result_payload_fails(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting action feedback'):
            self.prepare(self.draft([observation('old', feedback=result()), observation('now', feedback=result(accepted=False))]))

    def test_C_duplicate_current_event_fails_without_silent_rewrite(self):
        # Current Observation is verbatim by Spec; malformed duplicate delivery fails.
        with self.assertRaisesRegex(ValueError, 'Duplicate.*event'):
            self.prepare(self.draft([observation(events=[event(), event()])]))

    def test_D_proxy_margin_and_section_reconciliation(self):
        p = self.prepare()
        b = p.audit['budget']
        content = sum(len(m['content'].encode('utf-8')) for m in p.messages)
        self.assertEqual(b['input_reservation'], content + 1024)
        self.assertEqual(b['unit'], 'utf8_byte_reservation_proxy')
        self.assertFalse(b['is_actual_provider_token_count'])
        self.assertIsNone(b['actual_prompt_tokens'])
        self.assertEqual(content, sum(v['estimated_tokens_or_proxy_units'] for v in b['sections'].values()) + b['section_boundary_reconciliation'])
        self.assertEqual(set(b['sections']), {'Persona_P', 'M', 'X', 'current_Observation', 'pending_Observation_delivery',
                                             'episodic_retrieval', 'task_intention_history_view', 'fixed_instruction_schema_metadata'})
        self.assertGreater(b['wire_messages_utf8_bytes_not_tokens'], content)

    def test_D_exact_and_one_below_limit(self):
        d = self.draft()
        need = self.prepare(d).audit['budget']['input_reservation']
        for cap, fits in [(need + 1, True), (need, True), (need - 1, False)]:
            self.cfg['api']['max_input_tokens'] = cap
            self.assertEqual(self.prepare(d).audit['budget']['fits'], fits)

    def test_D_optional_pruning_order_and_required_preservation(self):
        d = self.draft([observation('h1', [event('e1')]), observation('h2', [event('e2', sequence=2)]), observation('now')], ['e1', 'e2'])
        full = self.prepare(d)
        required = copy.deepcopy(d); required.context['episodes'] = []; required.optional_priorities = {}
        self.cfg['api']['max_input_tokens'] = self.prepare(required).audit['budget']['input_reservation']
        p = self.prepare(d)
        expected = sorted(d.optional_priorities, key=lambda k: (d.optional_priorities[k], k))
        self.assertEqual([r['observation_id'] for r in p.audit['budget_removed_history']], expected)
        self.assertTrue(p.audit['budget']['fits'])
        for k in ('P', 'M', 'X', 'Observation'):
            self.assertEqual(p.context[k], full.context[k])
        self.assertEqual(p.context_bytes, self.prepare(d).context_bytes)

    def test_D_repeat_preparation_is_deterministic_and_draft_unchanged(self):
        d = self.draft([observation('old', [event()]), observation('now')], ['e1'])
        before = copy.deepcopy(d)
        a, b = self.prepare(d), self.prepare(d)
        self.assertEqual(a, b)
        self.assertEqual(d, before)

    def test_D_invalid_or_missing_tokenizer_never_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            self.cfg['context'] = {'token_estimator': {'kind': 'local_tokenizer_json_v1',
                 'path': str(Path(folder) / 'absent.json'), 'sha256': '0' * 64, 'model': 'fixture-model'}}
            with self.assertRaisesRegex(ValueError, 'no download fallback'):
                self.prepare()

    def test_D_hash_verified_local_tokenizer_offline(self):
        # Interface double only, NOT actual tokenizer integration or provider counts.
        calls = []
        class FixtureTokenizer:
            @staticmethod
            def from_file(path):
                calls.append(('path', path))
                return FixtureTokenizer()
            def no_truncation(self):
                calls.append('no_truncation')
            def no_padding(self):
                calls.append('no_padding')
            def encode(self, text, add_special_tokens):
                if add_special_tokens:
                    raise AssertionError('Unexpected special tokens')
                return types.SimpleNamespace(ids=list(range(len(text.split()))))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tokenizer.json'
            path.write_bytes(b'{"fixture":"interface-double-only"}')
            self.cfg['context'] = {'token_estimator': {'kind': 'local_tokenizer_json_v1', 'path': str(path),
                                  'sha256': file_hash(path), 'model': 'fixture-model'}}
            with patch.dict(sys.modules, {'tokenizers': types.SimpleNamespace(Tokenizer=FixtureTokenizer)}):
                estimator = TokenEstimator(self.cfg, 'fixture-model')
                self.assertEqual(estimator.count('hello world'), 2)
                p = self.prepare()
            self.assertIn('no_truncation', calls)
            self.assertIn('no_padding', calls)
            self.assertEqual(p.audit['budget']['unit'], 'local_tokenizer_content_tokens')
            self.assertEqual(p.audit['budget']['input_reservation'], sum(estimator.count(m['content']) for m in p.messages) + 1024)
            with patch.dict(sys.modules, {'tokenizers': None}):
                with self.assertRaisesRegex(ValueError, 'dependency is absent'):
                    TokenEstimator(self.cfg, 'fixture-model')
            with self.assertRaisesRegex(ValueError, 'model'):
                TokenEstimator(self.cfg, 'different-model')
            self.cfg['context']['token_estimator']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                TokenEstimator(self.cfg, 'fixture-model')

    def test_E_default_policy_options_and_invocation(self):
        self.cfg['context'] = {'memory_policy': {'options': {'recent_limit': 1, 'related_limit': 0, 'event_limit': 1}}}
        original = BoundedRelevantPolicy.select
        with patch.object(BoundedRelevantPolicy, 'select', autospec=True, side_effect=original) as select:
            p = self.prepare(self.draft([observation('h1', [event('e1')]),
                observation('h2', [event('e2'), event('e3', sequence=2)]), observation('now')], ['e1', 'e2', 'e3']))
        select.assert_called_once()
        self.assertEqual(p.audit['policy']['options'], {'recent_limit': 1, 'related_limit': 0, 'event_limit': 1})
        self.assertEqual(len(p.context['episodes']), 1)
        self.assertEqual(p.context['episodes'][0]['observation_id'], 'h2')
        self.assertEqual(len(p.context['episodes'][0]['events']), 1)

    def test_E_relevance_retrieves_old_related_episode(self):
        a, b = event('old'), event('recent')
        a['payload']['text'], b['payload']['text'] = 'water irrigation', 'music'
        current = observation(); current['content']['self'] = {'interest': 'water irrigation'}
        history = [{'observation_id': 'old', 'events': [a], 'action_result': None},
                   {'observation_id': 'recent', 'events': [b], 'action_result': None}]
        policy = BoundedRelevantPolicy({'recent_limit': 0, 'related_limit': 1, 'event_limit': 1})
        self.assertEqual(policy.select(history, current, {})[0].observation_id, 'old')

    def test_E_policy_cannot_inject_or_duplicate_feedback(self):
        for choices in [[MemoryChoice('secret', (), False, 0)],
                        [MemoryChoice('old', (), True, 0)],
                        [MemoryChoice('old', ('e1',), False, 0)] * 2]:
            policy = Mock(); policy.select.return_value = choices
            with self.assertRaises(ValueError):
                select_history([observation('old', [event()]), observation('now')], {}, {'e1'}, set(), policy)

    def test_E_P_M_frozen_and_no_task_from_speech(self):
        a = actor(); before = a.snapshot()
        a.deliver(observation(), self.log)
        context = a.context_draft(self.cfg, 1, 17).context
        a.accept_decision({'action': 'SAY', 'arguments': {'text': 'I will help'}}, context, self.cfg, 'q1', self.log)
        self.assertEqual(a.snapshot()['P'], before['P'])
        self.assertEqual(a.snapshot()['M'], before['M'])
        self.assertEqual(a.X['tasks'], {})

    def test_F_prepared_nested_audit_is_frozen(self):
        p = self.prepare(); before = copy.deepcopy(p.audit)
        p.audit['budget']['fits'] = False
        p.audit['final_event_ids'].append('forged')
        self.assertEqual(p.audit, before, 'PreparedDecision audit is not immutable')

    def test_F_prepared_input_views_are_copies(self):
        d = self.draft([observation(events=[event()])]); p = self.prepare(d)
        before = (p.context_bytes, p.messages_bytes)
        d.context['P']['authorized'] = 'changed'
        p.context['Observation']['content']['events'].clear()
        p.messages[0]['content'] = 'changed'
        self.assertEqual((p.context_bytes, p.messages_bytes), before)
        self.assertEqual(digest(p.context), p.audit['context_sha256'])

    def test_F_forged_prepared_messages_fail_before_reservation(self):
        from context_builder import PreparedDecision
        p = self.prepare(); messages = p.messages; messages[1]['content'] = '{}'
        forged = PreparedDecision(p.context_bytes, canonical(messages), p.audit)
        client = self.client()
        with self.assertRaisesRegex(ValueError, 'Prepared context changed'):
            client.decide('a', 1, forged)
        self.assertEqual(client.ledger.calls, 0)

    def test_F_cannot_bypass_overflow_by_mutating_audit(self):
        self.cfg['api']['max_input_tokens'] = 1024
        p = self.prepare()
        p.audit['budget']['fits'] = True
        client = self.client()
        with self.assertRaisesRegex(ValueError, 'exceeds input reservation'):
            client.decide('a', 1, p)
        self.assertEqual(client.ledger.calls, 0)

    def test_F_mock_transport_sends_prepared_bytes_without_retrieval(self):
        p = self.prepare()
        response = io.BytesIO(canonical({'choices': [{'message': {'content': '{"action":"SAY","arguments":{"text":"fixture"}}'}, 'finish_reason': 'stop'}],
                                         'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}))
        opener = Mock(); opener.open.return_value = response
        with patch('llm.urllib.request.build_opener', return_value=opener), \
             patch.object(BoundedRelevantPolicy, 'select', side_effect=AssertionError('retrieval after prepare')):
            decision = self.client().decide('a', 1, p)
        sent = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(sent['messages'], p.messages)
        self.assertEqual(decision['action'], 'SAY')
        self.assertEqual([r for r in self.log.rows if r['type'] == 'api_request'][0]['payload']['messages_sha256'], p.audit['messages_sha256'])

    def test_F_runner_prepares_once_before_dispatch_static(self):
        tree = ast.parse(Path(__file__).with_name('runner.py').read_text(encoding='utf-8'))
        phase = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_phase')
        calls = [n for n in ast.walk(phase) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        for name in ('context_draft', 'prepare'):
            self.assertEqual(sum(n.func.attr == name for n in calls), 1)
        self.assertFalse(any(n.func.attr in ('decision_context', 'retrieve', 'make_messages') for n in calls))
        submit = next(n for n in calls if n.func.attr == 'submit')
        self.assertEqual(ast.unparse(submit.args[-1]), 'prepared')

    def test_G_capability_is_not_execution_permission(self):
        context = self.prepare().context
        self.assertTrue(context['Observation']['content']['scene']['capability_view']['TRANSFER'])
        scene = StaticSettlement()
        request = {'request_id': 'q1', 'actor_id': 'a', 'round': 1, 'world_version': 1,
                   'phase': 'fixture', 'action': 'TRANSFER', 'arguments': {}}
        tx = GM(scene).commit([request], ['a'], 'fixture')
        self.assertFalse(tx['results'][0]['accepted'])
        self.assertIsNone(tx['results'][0]['executed_action'])
        self.assertEqual(scene.state['resources'], 1)

    def test_G_speech_not_resource_action_and_request_not_execution(self):
        scene = StaticSettlement()
        request = {'request_id': 'q1', 'actor_id': 'a', 'round': 1, 'world_version': 1,
                   'phase': 'fixture', 'action': 'SAY', 'arguments': {'text': 'I transferred all resources'}}
        self.assertEqual(scene.state['utterances'], [])
        tx = GM(scene).commit([request], ['a'], 'fixture')
        self.assertTrue(tx['results'][0]['accepted'])
        self.assertEqual(tx['results'][0]['executed_action'], 'SAY')
        self.assertEqual(scene.state['resources'], 1)

    def test_H_committed_feedback_delivery_link_and_privacy(self):
        a, dispatcher = actor(), ObservationDispatcher(['a', 'b'])
        ev = event(request='q1')
        dispatcher.register([ev], self.log)
        obs = dispatcher.deliver(StaticObservationSource(observation('feedback', [ev])), a, 'fixture:feedback', self.log, result())
        self.assertEqual(obs['content']['events'][0]['request_id'], 'q1')
        self.assertEqual(obs['recipient'], 'a')
        dispatcher.deliver(StaticObservationSource(observation('next', [ev])), a, 'fixture:opening', self.log)
        p = self.prepare(a.context_draft(self.cfg, 1, 17))
        self.assertEqual(p.audit['final_result_ids'], ['q1'])
        self.assertEqual(sum(len(r.get('events', [])) for r in p.context['episodes']), 0)
        b = Agent({'alias': 'b', 'initial': {'P': {}, 'M0': {}}, 'view': {}})
        with self.assertRaisesRegex(ValueError, 'Unauthorized'):
            dispatcher.deliver(StaticObservationSource(observation('b_obs', [ev], actor='b')), b, 'fixture', self.log)
        self.assertEqual(b.episodes, [])

    def test_H_missing_new_event_and_wrong_feedback_rejected(self):
        for feedback in (None, result(actor='b')):
            dispatcher, a = ObservationDispatcher(['a', 'b']), actor()
            dispatcher.register([event()], self.log)
            with self.assertRaises(ValueError):
                dispatcher.deliver(StaticObservationSource(observation()), a, 'fixture', self.log, feedback)
            self.assertEqual(a.episodes, [])

    def test_H_uncommitted_or_changed_event_rejected(self):
        dispatcher, a = ObservationDispatcher(['a']), actor()
        for registered in (False, True):
            if registered:
                dispatcher.register([event()], self.log)
            changed = event(); changed['payload']['text'] = 'altered'
            with self.assertRaisesRegex(ValueError, 'Uncommitted or altered'):
                dispatcher.deliver(StaticObservationSource(observation(events=[changed])), a, 'fixture', self.log)

    def test_I_readonly_fixture_trace_links_and_separation(self):
        from trace_readback import reconstruct_trace
        rows = self.trace_fixture()
        before = canonical(rows)
        report = reconstruct_trace(rows)
        self.assertEqual(canonical(rows), before)
        self.assertEqual([r['execution_status'] for r in report['chains']], ['executed', 'rejected', 'unexecuted'])
        first = report['chains'][0]
        self.assertEqual(first['feedback_observation_ids'], ['feedback'])
        self.assertEqual(first['next_decision_observation_id'], 'next')
        self.assertTrue(first['next_decision_includes_feedback'])
        self.assertEqual(first['world_event_ids'], ['e1'])
        self.assertEqual(report['gaps'], [])

    def trace_fixture(self):
        capture = Capture()
        o = observation(); p = self.prepare(self.draft([o]))
        capture.add('observation_delivered', o)
        capture.add('decision_input', {'agent_id': 'a', 'context': p.context, 'messages': p.messages,
                                     'context_sha256': p.audit['context_sha256'], 'messages_sha256': p.audit['messages_sha256']})
        for index, accepted in enumerate((True, False, None), 1):
            rid = 'q' + str(index)
            capture.add('action_request', {'request_id': rid, 'actor_id': 'a', 'basis_observation_id': o['observation_id'],
                                          'action': 'SAY', 'arguments': {}})
            if accepted is not None:
                res = result(rid, accepted=accepted)
                capture.add('world_transaction', {'results': [res]})
                if accepted:
                    capture.add('world_event', event(request=rid))
                capture.add('action_result', res)
            if index == 1:
                feedback_obs = observation('feedback', [event(request=rid)], result(rid))
                capture.add('observation_delivered', feedback_obs)
                previous = o
                o = observation('next', [event(request=rid)])
                capture.add('observation_delivered', o)
                p = self.prepare(self.draft([previous, feedback_obs, o]))
                capture.add('decision_input', {'agent_id': 'a', 'context': p.context, 'messages': p.messages,
                                              'context_sha256': p.audit['context_sha256'], 'messages_sha256': p.audit['messages_sha256']})
        return capture.rows

    def test_I_missing_links_are_gaps_not_invented(self):
        from trace_readback import reconstruct_trace
        rows = self.trace_fixture()
        rows = [r for r in rows if r['type'] != 'decision_input']
        report = reconstruct_trace(rows)
        self.assertTrue(any(g['reason'] == 'missing_prepared_decision' for g in report['gaps']))

    def test_I_delivered_feedback_not_in_next_input_is_explicit_gap(self):
        from trace_readback import reconstruct_trace
        rows = self.trace_fixture()
        p = [r['payload'] for r in rows if r['type'] == 'decision_input'][1]
        p['context']['episodes'] = []
        p['messages'] = make_messages(p['context'])
        p['context_sha256'], p['messages_sha256'] = digest(p['context']), digest(p['messages'])
        report = reconstruct_trace(rows)
        self.assertIn({'request_id': 'q1', 'reason': 'delivered_feedback_missing_from_next_input'}, report['gaps'])

    def test_I_forged_result_or_unauthorized_delivery_fails(self):
        from trace_readback import reconstruct_trace
        for mutation in ('result', 'delivery', 'hash'):
            rows = self.trace_fixture()
            if mutation == 'result':
                next(r for r in rows if r['type'] == 'action_result')['payload']['request_id'] = 'missing'
            elif mutation == 'hash':
                next(r for r in rows if r['type'] == 'decision_input')['payload']['messages_sha256'] = '0' * 64
            else:
                row = next(r for r in rows if r['type'] == 'observation_delivered' and r['payload']['content']['events'])
                row['payload']['content']['events'][0]['visibility'] = ['b']
            with self.assertRaises(ValueError, msg=mutation):
                reconstruct_trace(rows)

    def test_I_file_hash_chain_read_only(self):
        from trace_readback import read_trace
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.jsonl'
            with patch('common.utc_now', return_value='fixture-time'):
                log = EventLog(path)
                for row in self.trace_fixture():
                    log.add(row['type'], row['payload'])
                log.close()
            before = path.read_bytes()
            self.assertEqual(len(read_trace(path)['chains']), 3)
            self.assertEqual(path.read_bytes(), before)

    def test_I_changed_hash_chain_fails(self):
        from trace_readback import read_trace
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.jsonl'
            row = {'seq': 1, 'time_utc': 'fixture-time', 'type': 'ignored', 'payload': {}, 'previous_hash': None}
            row['hash'] = digest(row)
            row['payload']['changed'] = True
            path.write_bytes(canonical(row) + b'\n')
            with self.assertRaisesRegex(ValueError, 'Broken event chain'):
                read_trace(path)

    def test_I_prepared_memory_cannot_use_undelivered_private_event(self):
        from trace_readback import reconstruct_trace
        rows = self.trace_fixture()
        p = next(r for r in rows if r['type'] == 'decision_input')['payload']
        p['context']['episodes'] = [{'observation_id': 'secret-b', 'events': [event('secret', ['b'])]}]
        p['messages'] = make_messages(p['context'])
        p['context_sha256'], p['messages_sha256'] = digest(p['context']), digest(p['messages'])
        with self.assertRaisesRegex(ValueError, 'foreign memory'):
            reconstruct_trace(rows)


if __name__ == '__main__':
    unittest.main()
