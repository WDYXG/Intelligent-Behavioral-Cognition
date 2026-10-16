"""Single-action engineering fixture; not a community or behavioral experiment.

Only MOVE(amount=1) is offered so the test measures execution plumbing, not choice
quality. All quantities are explicit fixture assumptions, never Persona evidence.
"""
from __future__ import annotations

import copy
from common import require


class RuntimeSmokeScene:
    def __init__(self, aliases: list[str], config: dict):
        self.validate_config(config)
        require(len(aliases) == 1, "Runtime smoke requires exactly one Agent")
        self.aliases = list(aliases)
        self.state = {'round': 0, 'version': 0, 'owner': aliases[0], 'resource': 2,
                      'container': 0, 'events': [], 'permission': {'MOVE': True}}
        self._observation_sequence = 0

    @classmethod
    def validate_config(cls, config):
        require(config['rounds'] == config['repetitions'] == 1, 'Smoke is one round, one repetition')
        require(config.get('learning_enabled') is False, 'Smoke does not enable learning')
        require(config['scene']['adapter'] == 'scene_runtime_smoke:RuntimeSmokeScene', 'Wrong smoke adapter')
        require(config['kernel'] == 'phase_ib_runtime_smoke_v1', 'Wrong smoke kernel')

    @classmethod
    def phase_names(cls, config):
        return ['move']

    def phases(self):
        return ['move']

    def snapshot(self):
        return copy.deepcopy(self.state)

    def begin_round(self, number):
        require(number == 1 and self.state['round'] == 0, 'Smoke cannot start another cycle')
        before = self.snapshot()
        self.state.update(round=1, version=1)
        return {'before': before, 'after': self.snapshot(), 'world_events': []}

    def describe(self, alias):
        require(alias == self.state['owner'], 'Unknown fixture actor')
        return {'name': 'one_action_runtime_engineering_fixture',
                'origin': 'experimental_initial_conditions_not_persona',
                'purpose': 'Validate one authorized resource move. No social/behavioral claim.',
                'action_space': self.capabilities(alias), 'permissions': copy.deepcopy(self.state['permission']),
                'rule': 'MOVE with amount=1 transfers one owned unit into the fixture container; no other operation is authorized.'}

    def capabilities(self, alias):
        require(alias == self.state['owner'], 'Unknown fixture actor')
        return {'MOVE': {'arguments': {'amount': 1}, 'cost': 1, 'destination': 'fixture_container'}}

    def observe(self, alias, phase, feedback=None, since=None):
        scene = self.describe(alias)
        self._observation_sequence += 1
        cursor = (since or {}).get('world_sequence', 0)
        visible = [copy.deepcopy(e) for e in self.state['events']
                   if alias in e['visibility'] and e['world_sequence'] > cursor]
        return {'observation_id': f'smoke:o{self._observation_sequence}:{alias}', 'recipient': alias,
                'visibility': [alias], 'round': self.state['round'], 'world_version': self.state['version'], 'phase': phase,
                'content': {'scene': scene, 'self': {'resource': self.state['resource'], 'container': self.state['container']},
                            'events': visible, 'action_result': copy.deepcopy(feedback), 'decision_phase': 'move',
                            'event_cursors': {'world_sequence': len(self.state['events'])}}}

    def validate_phase(self, phase):
        require(phase == 'move' and self.state['round'] == 1, 'Invalid fixture phase')

    def interpret_open_action(self, request, before):
        raise ValueError('OPEN is not offered in this engineering fixture')

    def check_preconditions(self, state, request, before):
        require(request['actor_id'] == state['owner'], 'owner_permission_denied')
        require(request['action'] == 'MOVE' and state['permission']['MOVE'] is True, 'action_permission_denied')
        require(request['arguments'] == {'amount': 1} and type(request['arguments']['amount']) is int, 'invalid_MOVE_parameters')
        require(state['resource'] >= 1 and state['container'] == 0, 'resource_or_single_execution_limit')

    def execute_action(self, state, request, before):
        state['resource'] -= 1
        state['container'] += 1
        identity = f"smoke:event:{request['request_id']}"
        effects = {'parameters_executed': copy.deepcopy(request['arguments']), 'resource_delta': -1,
                   'container_delta': 1, 'authorization': 'owner_and_MOVE_permission_and_resources_checked'}
        state['events'].append({'event_id': identity, 'world_sequence': len(state['events']) + 1,
            'round': state['round'], 'phase': request['phase'], 'visibility': [request['actor_id']],
            'request_id': request['request_id'], 'result_id': 'result:' + request['request_id'],
            'epistemic_status': 'committed_world_change_not_speech', 'payload': copy.deepcopy(effects)})
        return effects, [request['actor_id']]

    def settle_phase(self, state, phase):
        return []

    def assert_invariants(self, state):
        require(type(state['resource']) is int and type(state['container']) is int
                and state['resource'] >= 0 and state['container'] >= 0
                and state['resource'] + state['container'] == 2, 'Fixture conservation violated')

    def collect_world_events(self, before, after, phase):
        return copy.deepcopy(after['events'][len(before['events']):])

    def rejection_options(self, request, before, reason):
        return ['Inspect the delivered MOVE permission, resource and amount=1 requirement.']

    def termination(self):
        return {'ended': True, 'reason': 'one_engineering_cycle_only'}

    def metrics(self, transaction):
        return {'scope': 'engineering_only', 'accepted': sum(r['accepted'] for r in transaction['results'])}
