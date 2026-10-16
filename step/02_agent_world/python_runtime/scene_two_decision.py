"""Two-cycle derivative of the I-B fixture; no scientific conditions or new loop."""
from common import require
from scene_runtime_smoke import RuntimeSmokeScene


class TwoDecisionScene(RuntimeSmokeScene):
    @classmethod
    def validate_config(cls, config):
        require(config['rounds'] == 2 and config['repetitions'] == 1, 'Readiness requires two cycles, one run')
        require(config.get('learning_enabled') is False, 'Learning must remain disabled')
        require(config['scene']['adapter'] == 'scene_two_decision:TwoDecisionScene', 'Wrong readiness adapter')
        require(config['kernel'] == 'two_decision_readiness_v1', 'Wrong readiness kernel')

    def begin_round(self, number):
        require(number in (1, 2) and number == self.state['round'] + 1, 'Invalid readiness cycle')
        before = self.snapshot()
        self.state.update(round=number, version=self.state['version'] + 1)
        return {'before': before, 'after': self.snapshot(), 'world_events': []}

    def describe(self, alias):
        description = super().describe(alias)
        description.update(name='two_decision_runtime_readiness_fixture',
                           purpose='Two consecutive authorized moves to check feedback delivery into the next decision; no behavioral claim.')
        return description

    def validate_phase(self, phase):
        require(phase == 'move' and self.state['round'] in (1, 2), 'Invalid readiness phase')

    def check_preconditions(self, state, request, before):
        # Reuse every original permission/amount/resource check. Only the original
        # single-execution guard is generalized to one move per numbered cycle.
        require(state['container'] == state['round'] - 1, 'one_move_per_cycle_required')
        super().check_preconditions({**state, 'container': 0}, request, before)

    def termination(self):
        return {'ended': self.state['round'] == 2, 'reason': 'two_engineering_cycles_only'}
