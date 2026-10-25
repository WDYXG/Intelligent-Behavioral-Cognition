"""Verify ownership, frozen initialization and provisional state boundaries."""

from copy import deepcopy
import json
import unittest

from public_goods.agents.state import AgentState, StateError, initial_state
from public_goods.environment.world import World


IDS = ("a", "b", "c", "d")


def belief(text="fixture judgment"):
    return {"facts": {"assessment": text, "evidence": ["/round"], "uncertainty": "low"}}


class AgentStateTests(unittest.TestCase):
    def setUp(self):
        self.world = World("state-fixture", IDS)
        self.store = AgentState()

    def observe(self):
        return self.world.observe(IDS[0])

    def test_unknown_layers_and_frozen_M_do_not_invent_personal_data(self):
        state = initial_state()
        self.assertEqual(set(state["P"]["layers"]), {f"L{i}" for i in range(1, 9)})
        self.assertTrue(state["P"]["frozen"])
        for layer in state["P"]["layers"].values():
            self.assertIsNone(layer["value"])
            self.assertEqual(layer["status"], "unknown")
            self.assertEqual(layer["sources"], [])
            self.assertTrue(layer["missing_reason"])
        self.assertTrue(state["M"]["frozen"])
        self.assertFalse(state["M"]["learning_enabled"])
        self.assertEqual(state["M"]["content"], {})
        self.assertIsNone(state["X"])
        self.assertEqual(state["version"], 0)
        self.assertEqual(state["episodes"], [])
        state["P"]["layers"]["L1"]["value"] = "not evidence"
        self.assertEqual(self.store.snapshot(), initial_state())

    def test_preparation_is_provisional_and_snapshots_are_detached(self):
        original = self.store.snapshot()
        observation, judgment = self.observe(), belief()
        transition = self.store.prepare(observation, judgment)
        self.assertEqual(self.store.snapshot(), original)
        observation["history"][0]["rounds"] = 99
        judgment["facts"]["assessment"] = "changed outside"
        self.store.commit(transition)
        self.assertEqual(self.store.snapshot()["X"]["judgment"], belief())
        self.assertEqual(self.store.snapshot()["episodes"][0]["event"]["rounds"], 10)
        transition["state"]["P"]["layers"]["L1"]["value"] = "after commit"
        snapshot = self.store.snapshot()
        snapshot["X"]["judgment"]["facts"]["assessment"] = "modified copy"
        snapshot["M"]["learning_enabled"] = True
        self.assertEqual(self.store.snapshot()["X"]["judgment"], belief())
        self.assertEqual(self.store.snapshot()["P"], original["P"])
        self.assertEqual(self.store.snapshot()["M"], original["M"])

    def test_repeated_history_is_not_added_again_and_event_ids_are_reproducible(self):
        first = self.store.prepare(self.observe(), belief())
        other = AgentState().prepare(self.observe(), belief())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(other, sort_keys=True))
        self.store.commit(first)
        episodes = self.store.snapshot()["episodes"]
        second = self.store.prepare(self.observe(), belief("revised judgment"))
        self.store.commit(second)
        self.assertEqual(self.store.snapshot()["episodes"], episodes)
        self.assertEqual(self.store.snapshot()["version"], 2)
        self.assertEqual(self.store.snapshot()["X"]["judgment"], belief("revised judgment"))

    def test_rewritten_truncated_and_type_changed_history_are_rejected(self):
        self.store.commit(self.store.prepare(self.observe(), belief()))
        original = self.store.snapshot()
        for replacement in ([], [{"type": "experiment_started", "rounds": 9}],
                            [{"type": "experiment_started", "rounds": 10.0}]):
            changed = self.observe()
            changed["history"] = replacement
            with self.subTest(history=replacement), self.assertRaises(StateError):
                self.store.prepare(changed, belief())
            self.assertEqual(self.store.snapshot(), original)

    def test_identical_event_values_at_different_positions_remain_distinct_events(self):
        observation = self.observe()
        observation["history"].append(deepcopy(observation["history"][0]))
        self.store.commit(self.store.prepare(observation, belief()))
        episodes = self.store.snapshot()["episodes"]
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["event"], episodes[1]["event"])
        self.assertNotEqual(episodes[0]["id"], episodes[1]["id"])
        self.assertEqual([e["observation_path"] for e in episodes], ["/history/0", "/history/1"])

    def test_commit_rejects_foreign_copied_and_replayed_transitions(self):
        transition = self.store.prepare(self.observe(), belief())
        other_store = AgentState()
        other_store.prepare(self.observe(), belief())
        with self.assertRaises(StateError):
            other_store.commit(transition)
        with self.assertRaises(StateError):
            self.store.commit(deepcopy(transition))
        with self.assertRaises(StateError):
            self.store.commit(json.loads(json.dumps(transition)))
        self.store.commit(transition)
        with self.assertRaises(StateError):
            self.store.commit(transition)
        self.assertEqual(other_store.snapshot(), initial_state())
        self.assertEqual(self.store.snapshot()["version"], 1)

    def test_changed_P_M_X_metadata_and_episodes_never_commit(self):
        for target in ("P", "M", "X", "episodes", "before_version", "observation_sha256"):
            store = AgentState()
            transition = store.prepare(self.observe(), belief())
            if target == "P":
                transition["state"]["P"]["layers"]["L7"]["value"] = {"cooperation": 1}
            elif target == "M":
                transition["state"]["M"]["learning_enabled"] = True
            elif target == "X":
                transition["state"]["X"]["judgment"]["facts"]["assessment"] = "injected"
            elif target == "episodes":
                transition["state"]["episodes"][0]["event"]["rounds"] = 1
            elif target == "before_version":
                transition[target] = 3
            else:
                transition[target] = "0" * 64
            with self.subTest(target=target), self.assertRaises(StateError):
                store.commit(transition)
            self.assertEqual(store.snapshot(), initial_state())

    def test_one_commit_invalidates_competing_stale_preparations(self):
        first = self.store.prepare(self.observe(), belief("first"))
        second = self.store.prepare(self.observe(), belief("second"))
        self.store.commit(first)
        with self.assertRaises(StateError):
            self.store.commit(second)
        self.assertEqual(self.store.snapshot()["X"]["judgment"], belief("first"))

    def test_failed_batch_discards_candidates_without_state_changes(self):
        self.store.commit(self.store.prepare(self.observe(), belief("committed")))
        original = self.store.snapshot()
        self.world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        self.world.advance()
        transition = self.store.prepare(self.observe(), belief("uncommitted"))
        self.store.discard_pending()
        with self.assertRaises(StateError):
            self.store.commit(transition)
        self.assertEqual(self.store.snapshot(), original)

    def test_N_to_P_and_both_P_stages_preserve_frozen_state_and_visible_history(self):
        frozen = self.store.snapshot()
        for _ in range(10):
            self.store.commit(self.store.prepare(self.observe(), belief()))
            self.world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
            self.world.advance()
        p_contribution = self.store.prepare(self.observe(), belief("P contribution"))
        self.assertEqual(p_contribution["state"]["X"]["phase"], "P")
        self.assertEqual(p_contribution["state"]["X"]["stage"], "contribution")
        self.assertEqual(len(p_contribution["state"]["episodes"]), 12)
        self.assertEqual(p_contribution["state"]["episodes"][-1]["event"]["type"],
                         "new_experiment_announced")
        self.store.commit(p_contribution)
        self.world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        self.store.commit(self.store.prepare(self.observe(), belief("P current action")))
        final = self.store.snapshot()
        self.assertEqual(final["version"], 12)
        self.assertEqual(final["X"]["stage"], "punishment")
        self.assertEqual(len(final["episodes"]), 12)
        self.assertEqual(final["P"], frozen["P"])
        self.assertEqual(final["M"], frozen["M"])

    def test_nonfinite_nonjson_and_invalid_scope_inputs_are_rejected(self):
        changes = ({"phase": "Q"}, {"round": True}, {"round": 0}, {"round": 11},
                   {"stage": "feedback"}, {"stage": "punishment"},
                   {"history": ["not an event"]}, {"phase_income": float("nan")},
                   {"history": tuple(self.observe()["history"])})
        for change in changes:
            observation = self.observe()
            observation.update(change)
            with self.subTest(change=change), self.assertRaises(StateError):
                self.store.prepare(observation, belief())
        self.assertEqual(self.store.snapshot(), initial_state())

    def test_feedback_ingestion_retains_last_outcome_without_new_judgment_or_learning(self):
        self.store.commit(self.store.prepare(self.observe(), belief()))
        before = self.store.snapshot()
        self.world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        feedback = self.observe()
        transition = self.store.prepare_feedback(feedback)
        self.assertEqual(self.store.snapshot(), before)
        self.store.commit(transition)
        after = self.store.snapshot()
        self.assertEqual(after["X"], before["X"])
        self.assertEqual(after["P"], before["P"])
        self.assertEqual(after["M"], before["M"])
        self.assertEqual(after["version"], before["version"] + 1)
        self.assertEqual(after["episodes"][-1]["event"], feedback["feedback"])
        wrong = deepcopy(feedback)
        wrong["feedback"]["period_income"] = 999
        with self.assertRaises(StateError):
            self.store.prepare_feedback(wrong)
        self.world.advance()
        with self.assertRaises(StateError):
            self.store.prepare_feedback(self.observe())


if __name__ == "__main__":
    unittest.main()
