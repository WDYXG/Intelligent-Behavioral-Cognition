"""Offline checks for the I-B entry/adapter, NOT evidence of a real I-B run."""
import copy
import unittest
from unittest.mock import patch

from common import STEP
from run_runtime_smoke import resolve_smoke
from scene_runtime_smoke import RuntimeSmokeScene
from population import load_population
from gm import GM
from agent import Agent
from observation import ObservationDispatcher
from context_builder import ContextBuilder
from llm import make_messages, preflight


class Capture:
    def add(self, kind, payload):
        pass


class RuntimeSmokeOffline(unittest.TestCase):
    def setUp(self):
        self.path = STEP / '03_experiment/configs/phase_ib_runtime_smoke_v1.json'
        self.spec, self.cfg, _ = resolve_smoke(self.path)
        self.scene = RuntimeSmokeScene(['resident_01'], self.cfg)
        self.scene.begin_round(1)

    def request(self, **changes):
        return {'request_id': 'test:move:resident_01', 'actor_id': 'resident_01', 'round': 1,
                'world_version': 1, 'phase': 'move', 'action': 'MOVE', 'arguments': {'amount': 1}, **changes}

    def test_same_config_authority_single_phase_single_attempt(self):
        self.assertEqual(self.cfg['api']['max_input_tokens'], 32768)
        self.assertEqual(self.cfg['api']['max_output_tokens'], 4096)
        self.assertEqual(self.cfg['api']['max_retries'], 0)
        self.assertEqual(self.scene.phases(), ['move'])
        self.assertNotIn('world', self.cfg)
        self.assertNotIn('daily_life', self.cfg)

    def test_actual_existing_authorized_view_fits_without_truncation(self):
        population, _ = load_population(self.cfg, self.path.parent)
        member = next(m for m in population if m['alias'] == self.spec['selected_alias'])
        agent = Agent(member)
        ObservationDispatcher([agent.alias]).deliver(self.scene, agent, 'move:opening', Capture())
        prepared = ContextBuilder(self.cfg, 'offline-no-dispatch', make_messages).prepare(agent.context_draft(self.cfg, 1, 17))
        self.assertEqual(prepared.context['P'], member['view'])
        self.assertTrue(prepared.audit['budget']['fits'])
        self.assertEqual(agent.snapshot()['P'], member['initial']['P'])

    def test_authorized_move_and_feedback_link(self):
        log = Capture()
        a = Agent({'alias': 'resident_01', 'initial': {'P': {}, 'M0': {}}, 'view': {}})
        dispatcher = ObservationDispatcher([a.alias])
        dispatcher.deliver(self.scene, a, 'move:opening', log)
        request = self.request()
        tx = GM(self.scene).commit([request], [a.alias], 'move')
        self.assertTrue(tx['results'][0]['accepted'])
        self.assertEqual(tx['results'][0]['result_id'], 'result:' + request['request_id'])
        self.assertEqual(self.scene.state['resource'], 1)
        self.assertEqual(self.scene.state['container'], 1)
        dispatcher.register(tx['world_events'], log)
        obs = dispatcher.deliver(self.scene, a, 'move:feedback', log, tx['results'][0])
        self.assertEqual(obs['content']['events'][0]['result_id'], tx['results'][0]['result_id'])
        self.assertEqual(obs['content']['self'], {'resource': 1, 'container': 1})

    def test_permission_denial_is_not_execution(self):
        self.scene.state['permission']['MOVE'] = False
        tx = GM(self.scene).commit([self.request()], ['resident_01'], 'move')
        self.assertFalse(tx['results'][0]['accepted'])
        self.assertIsNone(tx['results'][0]['executed_action'])
        self.assertEqual(tx['world_events'], [])
        self.assertEqual(self.scene.state['resource'], 2)

    def test_preflight_counts_one_not_twelve_no_network(self):
        api = self.cfg['api']
        env = {api['endpoint_env']: 'https://invalid.example/chat/completions',
               api['model_env']: 'fixture-model', api['key_env']: 'fixture-key'}
        with patch.dict('os.environ', env, clear=True):
            resolved = preflight(self.cfg, population_count=1)
        self.assertEqual(resolved['estimate']['first_attempt_calls'], 1)
        self.assertEqual(resolved['estimate']['first_attempt_reserved_tokens'], 36864)

    def test_missing_credentials_fail_without_network(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'missing environment variable'):
                preflight(self.cfg, population_count=1)


if __name__ == '__main__':
    unittest.main()
