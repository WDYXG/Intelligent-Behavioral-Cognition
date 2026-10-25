"""Protocol, privacy and replay checks with hand-computable offline actions."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from public_goods.environment.world import InvalidAction, World, scripted_demo


IDS = ("private-a", "private-b", "private-c", "private-d")


def contributions(values):
    return {pid: {"contribution": value} for pid, value in zip(IDS, values)}


def punishments(values=(0, 0, 0)):
    return {pid: {"points": list(values)} for pid in IDS}


def enter_p(world, contribution=0):
    for _ in range(10):
        world.resolve_contributions(contributions([contribution] * 4))
        world.advance()


class PayoffTests(unittest.TestCase):
    def test_hand_calculated_N_payoffs(self):
        for values, expected in [([0] * 4, [20] * 4), ([20] * 4, [32] * 4),
                                 ([0, 5, 10, 20], [34, 29, 24, 14])]:
            world = World("fixture", IDS)
            world.resolve_contributions(contributions(values))
            self.assertEqual([world.observe(pid)["feedback"]["period_income"] for pid in IDS], expected)

    def test_own_cost_can_make_income_negative_after_received_cap(self):
        world = World("fixture", IDS)
        enter_p(world)
        world.resolve_contributions(contributions([20] * 4))
        world.resolve_punishments(punishments([10, 10, 10]))
        for pid in IDS:
            feedback = world.observe(pid)["feedback"]
            self.assertEqual(feedback["first_stage_income"], 32)
            self.assertEqual(feedback["received_points"], 30)
            self.assertEqual(feedback["income_reduction"], 32)
            self.assertEqual(feedback["assigned_point_cost"], 30)
            self.assertEqual(feedback["period_income"], -30)
            self.assertEqual(feedback["phase_income"], -5)  # 25 initial credit minus 30.
            self.assertEqual(feedback["total_income"], 195)  # N=200; P=-5.
        world.advance()
        self.assertEqual(world.observe(IDS[0])["available_tokens"], 20)
        self.assertEqual(world.observe(IDS[0])["phase_income"], -5)

    def test_display_slot_targets_correct_recipient(self):
        world = World("fixture", IDS, 42)
        enter_p(world)
        world.resolve_contributions(contributions([0, 5, 10, 20]))
        displayed = world.observe(IDS[0])["other_contributions"]
        target_contribution = displayed[0]["contribution"]
        target_id = IDS[[0, 5, 10, 20].index(target_contribution)]
        actions = punishments()
        actions[IDS[0]]["points"] = [2, 0, 0]
        world.resolve_punishments(actions)
        self.assertEqual(world.observe(IDS[0])["feedback"]["period_income"], 32)
        for pid in IDS:
            self.assertEqual(world.observe(pid)["feedback"]["received_points"], 2 if pid == target_id else 0)
        self.assertEqual(world.observe(target_id)["feedback"]["income_reduction"], 6)

    def test_bonus_once_and_exact_fractional_accounting(self):
        world = World("fixture", IDS)
        enter_p(world, contribution=1)
        for pid in IDS:
            self.assertEqual(world.observe(pid)["total_income"], 231)  # 10×20.6 + 25.
            self.assertEqual(world.observe(pid)["phase_income"], 25)
        for _ in range(10):
            world.resolve_contributions(contributions([1] * 4))
            world.resolve_punishments(punishments())
            world.advance()
        self.assertEqual(world.stage, "finished")
        for pid in IDS:
            self.assertEqual(world.observe(pid)["total_income"], 437)
            self.assertEqual(world.observe(pid)["phase_income"], 231)


class PrivacyAndTimingTests(unittest.TestCase):
    def test_no_future_P_rules_until_N_is_over(self):
        world = World("fixture", IDS)
        for _ in range(10):
            text = json.dumps(world.observe(IDS[0]))
            for future in ["deduction", "punishment", "lump_sum", '"P"', "second"]:
                self.assertNotIn(future, text)
            world.resolve_contributions(contributions([0] * 4))
            if world.round == 10:
                self.assertEqual(world.observe(IDS[0])["phase"], "N")
            world.advance()
        view = world.observe(IDS[0])
        self.assertEqual(view["phase"], "P")
        self.assertEqual(view["rules"]["phase_lump_sum"], 25)
        self.assertEqual(len([x for x in view["history"] if x["type"] == "round_feedback"]), 10)

    def test_partial_or_invalid_batch_changes_no_agent_observation(self):
        world = World("fixture", IDS, 9)
        untouched = World("fixture", IDS, 9)
        original = {pid: world.observe(pid) for pid in IDS}
        bad = contributions([0, 1, 2, 3])
        bad[IDS[-1]]["contribution"] = 21
        for request in [{IDS[0]: {"contribution": 20}}, bad]:
            with self.assertRaises(InvalidAction):
                world.resolve_contributions(request)
            self.assertEqual(original, {pid: world.observe(pid) for pid in IDS})
            self.assertFalse(world.audit_log()[-1]["accepted"])
        actions = contributions([0, 1, 2, 3])
        world.resolve_contributions(actions)
        untouched.resolve_contributions(actions)
        self.assertEqual(world.trajectory(), untouched.trajectory())
        self.assertEqual(world.observe(IDS[0]), untouched.observe(IDS[0]))

    def test_iteration_order_cannot_change_settlement_or_anonymous_mapping(self):
        a, b = World("fixture", IDS, 88), World("fixture", IDS, 88)
        enter_p(a)
        enter_p(b)
        actions = contributions([1, 7, 13, 20])
        a.resolve_contributions(actions)
        b.resolve_contributions(dict(reversed(list(actions.items()))))
        self.assertEqual([a.observe(pid) for pid in IDS], [b.observe(pid) for pid in IDS])
        actions = {pid: {"points": [i, 0, 2]} for i, pid in enumerate(IDS)}
        a.resolve_punishments(actions)
        b.resolve_punishments(dict(reversed(list(actions.items()))))
        self.assertEqual(a.trajectory(), b.trajectory())

    def test_views_are_copies_and_contain_no_other_identity(self):
        world = World("fixture", IDS, 72)
        enter_p(world)
        world.resolve_contributions(contributions([0, 5, 10, 20]))
        before = world.observe(IDS[0])
        mutated = world.observe(IDS[0])
        mutated["history"].clear()
        mutated["other_contributions"][0]["contribution"] = 999
        self.assertEqual(world.observe(IDS[0]), before)
        world.resolve_punishments(punishments([1, 2, 3]))
        for pid in IDS:
            view = world.observe(pid)
            text = json.dumps(view)
            for identity in IDS:
                self.assertNotIn(identity, text)
            self.assertNotIn("researcher_only_display_order", text)
            self.assertEqual(view["feedback"]["assigned_point_cost"], 6)

    def test_anonymous_order_is_resampled_without_forcing_a_different_order(self):
        world = World("fixture", IDS, 123)
        orders = []
        for _ in range(10):
            world.resolve_contributions(contributions([0, 5, 10, 20]))
            orders.append(tuple(x["contribution"] for x in world.observe(IDS[0])["feedback"]["other_contributions"]))
            world.advance()
        self.assertGreater(len(set(orders)), 1)
        self.assertTrue(all(sorted(order) == [5, 10, 20] for order in orders))


class ValidationAndReplayTests(unittest.TestCase):
    def test_illegal_types_ranges_and_extra_fields_rejected(self):
        world = World("fixture", IDS)
        for value in [True, 1.0, "1", -1, 21, None]:
            with self.subTest(value=value), self.assertRaises(InvalidAction):
                world.resolve_contributions(contributions([value] * 4))
        actions = contributions([0] * 4)
        actions[IDS[0]]["private_message"] = "hidden"
        with self.assertRaises(InvalidAction):
            world.resolve_contributions(actions)
        with self.assertRaises(InvalidAction):
            world.resolve_punishments(punishments())
        with self.assertRaises(InvalidAction):
            world.advance()
        enter_p(world)
        world.resolve_contributions(contributions([20] * 4))
        before = world.observe(IDS[0])
        for points in [[1, 2], [1, 2, 3, 4], [0, -1, 0], [11, 0, 0], [True, 0, 0], [0.5, 0, 0]]:
            with self.assertRaises(InvalidAction):
                world.resolve_punishments(punishments(points))
            self.assertEqual(before, world.observe(IDS[0]))

    def test_complete_experiment_replays_and_mutations_are_detected(self):
        world = scripted_demo()
        events = json.loads(json.dumps(world.audit_log()))
        replayed = World.replay(events)
        self.assertEqual(world.trajectory(), replayed.trajectory())
        self.assertEqual(len(world.trajectory()), 80)
        for pid in world.participants:
            self.assertEqual(world.observe(pid), replayed.observe(pid))
        corrupted = deepcopy(events)
        corrupted[1]["result"]["feedback"][world.participants[0]]["period_income"] += 1
        with self.assertRaises(InvalidAction):
            World.replay(corrupted)

    def test_rejected_requests_can_be_replayed(self):
        world = World("fixture", IDS)
        with self.assertRaises(InvalidAction):
            world.resolve_contributions(contributions([0, 1, 2, 21]))
        world.resolve_contributions(contributions([0, 1, 2, 3]))
        replayed = World.replay(world.audit_log())
        self.assertEqual(replayed.trajectory(), world.trajectory())

    def test_finished_world_blocks_further_actions_and_export_preserves_files(self):
        world = scripted_demo()
        with self.assertRaises(InvalidAction):
            world.advance()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            world.export(path)
            with self.assertRaises(InvalidAction):
                world.export(path)
            partial = World("incomplete", IDS)
            with self.assertRaises(InvalidAction):
                partial.export(Path(directory) / "partial")

    def test_export_accepted_by_existing_evaluator(self):
        from public_goods.evaluation.human_reference import load_agent
        world = scripted_demo()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            world.export(path)
            person, peers = load_agent(path / "trajectory.jsonl")
        self.assertEqual(len(person), 80)
        self.assertEqual(len(peers), 120)
        self.assertEqual(set(person.phase), {"N", "P"})


if __name__ == "__main__":
    unittest.main()
