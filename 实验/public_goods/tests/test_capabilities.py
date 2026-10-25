"""Visible-rule derivation and information boundaries of the shared capability."""

from copy import deepcopy
import hashlib
import json
import unittest

from public_goods.agents.capabilities import (
    ORIGINAL_CAPABILITY_VERSION, SCOPE_CAPABILITY_VERSION, VERIFIED_CAPABILITY_VERSION,
    build_capability_view,
)
from public_goods.environment.world import World


IDS = ("a", "b", "c", "d")


def p_world():
    world = World("capability", IDS)
    for _ in range(10):
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        world.advance()
    return world


class CapabilityTests(unittest.TestCase):
    def test_complete_history_keeps_rare_values_and_separates_current_round(self):
        world = World("history", IDS)
        for round_ in range(10):
            world.resolve_contributions({pid: {"contribution": 8 if round_ == 0 and pid == "c" else 10} for pid in IDS})
            world.advance()
        observation = world.observe("a")
        original = deepcopy(observation)
        facts = build_capability_view(observation)["verified_context"]
        history = facts["contribution_history"]["conditions"]
        self.assertEqual(set(history), {"N"})
        self.assertEqual(history["N"]["group_value_counts"], [{"value": 8, "count": 1}, {"value": 10, "count": 39}])
        self.assertEqual(history["N"]["observed_person_rounds"], 40)
        self.assertFalse(history["N"]["all_observed_values_equal"])
        self.assertEqual(history["N"]["rounds"][0]["others_sorted"], [8, 10, 10])
        self.assertEqual(observation, original)
        world.resolve_contributions({pid: {"contribution": 7} for pid in IDS})
        now = build_capability_view(world.observe("a"))["verified_context"]
        self.assertEqual(now["contribution_history"]["conditions"], history)
        self.assertEqual(now["current_stage"]["contributions"]["own"], 7)
        world.resolve_punishments({pid: {"points": [0, 0, 0]} for pid in IDS})
        settled = build_capability_view(world.observe("a"))["verified_context"]["contribution_history"]["conditions"]
        self.assertEqual(settled["P"]["observed_person_rounds"], 4)

    def test_payment_frequency_and_sources_come_from_visible_rules(self):
        from public_goods.agents.cognition import evidence_exists
        world = p_world()
        observation = world.observe("a")
        payment = build_capability_view(observation)["verified_context"]["payment_schedule"]
        self.assertEqual(payment["round_endowment"]["amount"], 20)
        self.assertEqual(payment["round_endowment"]["frequency"], "each_round")
        self.assertEqual(payment["phase_payment"]["amount"], 25)
        self.assertEqual(payment["phase_payment"]["frequency"], "once_at_condition_start")
        for item in payment.values():
            self.assertTrue(all(evidence_exists(observation, path) for path in item["source_paths"]))
        observation["history"][-1]["lump_sum"] = 30
        with self.assertRaisesRegex(ValueError, "conflicts"):
            build_capability_view(observation)
        initial = build_capability_view(World("first", IDS).observe("a"))["verified_context"]
        self.assertNotIn("phase_payment", initial["payment_schedule"])
        self.assertEqual(initial["contribution_history"]["conditions"], {})

    def test_invalid_or_duplicate_history_is_rejected_instead_of_summarized(self):
        observation = p_world().observe("a")
        cases = []
        duplicate = deepcopy(observation)
        duplicate["history"].append(deepcopy(duplicate["history"][1]))
        cases.append(duplicate)
        for key, value in (("own_contribution", True), ("other_contributions", []),
                           ("group_total_contribution", 999), ("round", 0), ("phase", "future")):
            bad = deepcopy(observation)
            bad["history"][1][key] = value
            cases.append(bad)
        future = deepcopy(observation)
        future["history"][1].update(phase="P", round=1)
        cases.append(future)
        for bad in cases:
            with self.subTest(history=bad["history"][1]), self.assertRaises(ValueError):
                build_capability_view(bad)

    def test_old_revision_outputs_remain_exactly_reproducible(self):
        # Digests frozen from the original v2 module before adding verified context.
        expected = {
            ORIGINAL_CAPABILITY_VERSION: [
                "e22d6c76ccbfbcb1aa77d7e47ff84a0d4114b8676448a3621655304ad48d571d",
                "c96b81a75250f5cc0e7505ec327c1f783456e313b40e2a9f86cc8b0e09ed9d57",
                "b55b7341e9048414446187016ebe366a5e36b418e18c06855cb2ac36fc45db2c",
            ],
            SCOPE_CAPABILITY_VERSION: [
                "3cc7becbd0947831423e6193e1826c757051877826f2dc779d09596740bd8711",
                "bc19119ac3616ce9ba643b11eec1c69d20d7a4c1f9a603f6854860cfad900479",
                "8c862135991af21f621c45fe966182ca7f75acc7d7d0a0149707a7d971307497",
            ],
            VERIFIED_CAPABILITY_VERSION: [
                "61b34aaf475315d86edbbd16c6bfe9a34b2a96ace7b734cfd6451900e0e02cf4",
                "57e7ebd3507b10f3e6ab0b7ce9987023c51bfd11f7c0b35f77d4aedfbf796be0",
                "cd0bfda8a5828eb852b350b8ce7536ff2eae1eccb7de8a59433a5a23a95f1083",
            ],
        }
        world = p_world()
        observations = [World("capability", IDS).observe("a"), world.observe("a")]
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        observations.append(world.observe("a"))
        for revision, digests in expected.items():
            for observation, digest in zip(observations, digests):
                actual = build_capability_view(observation, revision=revision)
                encoded = json.dumps(actual, ensure_ascii=False, sort_keys=True).encode()
                with self.subTest(revision=revision, phase=observation["phase"], stage=observation["stage"]):
                    self.assertEqual(hashlib.sha256(encoded).hexdigest(), digest)

    def test_current_contribution_visibility_changes_at_both_stage_boundaries(self):
        from public_goods.agents.cognition import evidence_exists
        world = p_world()
        before = build_capability_view(world.observe("a"))["verified_context"]["current_stage"]
        self.assertEqual(before["contributions"]["status"], "not_yet_revealed")
        self.assertIsNone(before["contributions"]["others_by_position"])
        world.resolve_contributions({pid: {"contribution": value} for pid, value in zip(IDS, (7, 0, 12, 20))})
        observation = world.observe("a")
        original = deepcopy(observation)
        current = build_capability_view(observation)["verified_context"]["current_stage"]
        self.assertEqual(current["contributions"]["status"], "revealed")
        self.assertEqual(current["contributions"]["own"], 7)
        self.assertEqual(current["contributions"]["others_by_position"], observation["other_contributions"])
        self.assertEqual(sorted(row["contribution"] for row in current["contributions"]["others_by_position"]),
                         [0, 12, 20])
        self.assertTrue(all(evidence_exists(observation, p) for p in current["contributions"]["source_paths"]))
        current["contributions"]["others_by_position"][0]["contribution"] = 99
        self.assertEqual(observation, original)
        world.resolve_punishments({pid: {"points": [0, 0, 0]} for pid in IDS})
        world.advance()
        next_round = build_capability_view(world.observe("a"))["verified_context"]["current_stage"]
        self.assertEqual(next_round["round"], 2)
        self.assertEqual(next_round["contributions"]["status"], "not_yet_revealed")
        self.assertIsNone(next_round["contributions"]["own"])
        self.assertIsNone(next_round["contributions"]["others_by_position"])

    def test_revealed_stage_requires_delivered_values_not_history_substitution(self):
        world = p_world()
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        for key in ("own_contribution", "other_contributions"):
            observation = world.observe("a")
            del observation[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_capability_view(observation)

    def test_final_condition_has_further_rounds_until_its_tenth_round(self):
        world = p_world()
        first = build_capability_view(world.observe("a"))["verified_context"]["temporal"]
        self.assertEqual(first["rounds_remaining_after_current"], 9)
        self.assertTrue(first["experiment_ends_after_this_condition"])
        self.assertFalse(first["current_is_final_round_of_condition"])
        self.assertFalse(first["experiment_ends_after_current_round"])
        self.assertIn("当前不是最后一轮", first["statement"])
        for _ in range(9):
            world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
            world.resolve_punishments({pid: {"points": [0, 0, 0]} for pid in IDS})
            world.advance()
        last = build_capability_view(world.observe("a"))["verified_context"]["temporal"]
        self.assertEqual(last["current_round"], 10)
        self.assertEqual(last["rounds_remaining_after_current"], 0)
        self.assertTrue(last["current_is_final_round_of_condition"])
        self.assertTrue(last["experiment_ends_after_current_round"])
        self.assertIn("本轮是本条件最后一轮，完成后实验结束", last["statement"])
        self.assertNotIn("不是最后一轮", last["statement"])

    def test_initial_condition_does_not_invent_the_later_condition(self):
        world = World("capability", IDS)
        first = build_capability_view(world.observe("a"))["verified_context"]
        self.assertNotIn("latest_settled_round", first)
        self.assertEqual(first["temporal"]["rounds_remaining_after_current"], 9)
        self.assertIsNone(first["temporal"]["experiment_ends_after_this_condition"])
        for _ in range(9):
            world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
            world.advance()
        last = build_capability_view(world.observe("a"))["verified_context"]["temporal"]
        self.assertTrue(last["current_is_final_round_of_condition"])
        self.assertIsNone(last["experiment_ends_after_current_round"])

    def test_zero_self_related_points_cannot_identify_hidden_group_sending(self):
        zero_world, hidden_world = p_world(), p_world()
        for world in (zero_world, hidden_world):
            world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        zero_actions = {pid: {"points": [0, 0, 0]} for pid in IDS}
        hidden_actions = deepcopy(zero_actions)
        # Researcher-only fixture construction: b sends to c; focal a is uninvolved.
        position = hidden_world.audit_log()[-1]["result"]["researcher_only_display_order"]["b"].index("c")
        hidden_actions["b"]["points"][position] = 2
        zero_world.resolve_punishments(zero_actions)
        hidden_world.resolve_punishments(hidden_actions)
        self.assertNotEqual(zero_world.audit_log(), hidden_world.audit_log())
        self.assertEqual(zero_world.observe("a"), hidden_world.observe("a"))
        view = build_capability_view(zero_world.observe("a"))
        self.assertEqual(view, build_capability_view(hidden_world.observe("a")))
        scoped = view["verified_context"]["latest_settled_round"]
        self.assertEqual(scoped["own_received_points"], 0)
        self.assertEqual(scoped["own_sent_points_total"], 0)
        self.assertIsNone(scoped["group_any_punishment"])
        self.assertIsNone(scoped["other_to_other_points"])

    def test_positive_self_related_points_prove_some_group_sending(self):
        for incoming in (False, True):
            world = p_world()
            world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
            actions = {pid: {"points": [0, 0, 0]} for pid in IDS}
            sender, receiver = ("b", "a") if incoming else ("a", "b")
            order = world.audit_log()[-1]["result"]["researcher_only_display_order"][sender]
            actions[sender]["points"][order.index(receiver)] = 2
            world.resolve_punishments(actions)
            scoped = build_capability_view(world.observe("a"))["verified_context"]["latest_settled_round"]
            with self.subTest(incoming=incoming):
                self.assertTrue(scoped["group_any_punishment"])
                self.assertIsNone(scoped["other_to_other_points"])
                self.assertEqual(scoped["own_received_points"], 2 if incoming else 0)
                self.assertEqual(scoped["own_sent_points_total"], 0 if incoming else 2)

    def test_no_feedback_or_missing_fields_stay_unknown_instead_of_zero(self):
        world = p_world()
        self.assertIsNone(build_capability_view(world.observe("a"))["verified_context"]["latest_settled_round"])
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        world.resolve_punishments({pid: {"points": [0, 0, 0]} for pid in IDS})
        world.advance()
        observation = world.observe("a")
        del observation["history"][-1]["received_points"]
        del observation["history"][-1]["assigned_points_by_position"]
        scoped = build_capability_view(observation)["verified_context"]["latest_settled_round"]
        for key in ("own_received_points", "own_assigned_points_by_position", "own_sent_points_total", "group_any_punishment"):
            self.assertIsNone(scoped[key])
        self.assertEqual(scoped["round"], 1)
        self.assertEqual(observation["round"], 2)
        del observation["rules"]["rounds"]
        temporal = build_capability_view(observation)["verified_context"]["temporal"]
        self.assertIsNone(temporal["rounds_remaining_after_current"])
        self.assertIsNone(temporal["experiment_ends_after_current_round"])

    def test_latest_zero_feedback_does_not_erase_earlier_positive_history(self):
        world = p_world()
        for round_ in (1, 2):
            world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
            actions = {pid: {"points": [0, 0, 0]} for pid in IDS}
            if round_ == 1:
                order = world.audit_log()[-1]["result"]["researcher_only_display_order"]["b"]
                actions["b"]["points"][order.index("a")] = 2
            world.resolve_punishments(actions)
            world.advance()
        observation = world.observe("a")
        before = deepcopy(observation)
        context = build_capability_view(observation)["verified_context"]
        latest = context["latest_settled_round"]
        self.assertEqual((observation["round"], latest["round"]), (3, 2))
        self.assertEqual(latest["own_received_points"], 0)
        self.assertEqual(latest["own_sent_points_total"], 0)
        self.assertIsNone(latest["group_any_punishment"])
        previous = [row for row in observation["history"]
                    if row.get("type") == "round_feedback" and row.get("phase") == "P"]
        self.assertEqual([(row["round"], row["received_points"]) for row in previous], [(1, 2), (2, 0)])
        self.assertIn("该轮", latest["statement"])
        self.assertIn("其他历史仍以 observation.history 为准", context["feedback_scope"])
        self.assertEqual(observation, before)

    def test_verified_context_sources_resolve_in_delivered_observation(self):
        from public_goods.agents.cognition import evidence_exists
        world = p_world()
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        world.resolve_punishments({pid: {"points": [1, 0, 0]} for pid in IDS})
        observation = world.observe("a")
        context = build_capability_view(observation)["verified_context"]
        for field in ("temporal", "latest_settled_round"):
            self.assertTrue(all(evidence_exists(observation, path) for path in context[field]["source_paths"]))
        context["latest_settled_round"]["own_assigned_points_by_position"].append(9)
        self.assertEqual(len(observation["history"][-1]["assigned_points_by_position"]), 3)

    def test_distinguishes_personal_return_group_return_and_net_changes(self):
        observation = World("capability", IDS).observe("a")
        view = build_capability_view(observation)
        knowledge = view["shared_rule_knowledge"]["contribution_payoff"]
        self.assertEqual(knowledge["per_added_contribution_token"], {
            "own_return": 0.4, "own_net_change": -0.6,
            "group_total_return": 1.6, "group_net_change": 0.6,
        })
        self.assertEqual(set(knowledge["source_paths"]), {
            "/rules/group_size", "/rules/retained_token_income", "/rules/return_per_group_token",
        })

    def test_uses_observed_coefficients_instead_of_global_world_defaults(self):
        observation = World("capability", IDS).observe("a")
        observation["rules"].update(group_size=5, return_per_group_token=0.3, retained_token_income=0.8)
        numbers = build_capability_view(observation)["shared_rule_knowledge"]["contribution_payoff"]["per_added_contribution_token"]
        self.assertEqual(numbers, {
            "own_return": 0.3, "own_net_change": -0.5,
            "group_total_return": 1.5, "group_net_change": 0.7,
        })

    def test_n_contains_no_future_condition_knowledge_or_history_payload(self):
        observation = World("capability", IDS).observe("a")
        observation["history"].append({"private_marker": "this must not be copied"})
        # Even stray rules are not exposed before the corresponding condition.
        observation["rules"].update(p_world().observe("a")["rules"])
        text = json.dumps(build_capability_view(observation), ensure_ascii=False).lower()
        for token in ("punishment", "deduction", "惩罚", "lump_sum", "this must not be copied"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_condition_p_is_not_the_current_action_stage(self):
        world = p_world()
        contribution = build_capability_view(world.observe("a"))["task"]
        self.assertEqual((contribution["condition"], contribution["stage"]), ("P", "contribution"))
        self.assertIn("贡献", contribution["instruction"])
        self.assertEqual(set(contribution["action_schema"]), {"contribution"})
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        sending = build_capability_view(world.observe("a"))["task"]
        self.assertEqual((sending["condition"], sending["stage"]), ("P", "punishment"))
        self.assertIn("扣减点数", sending["instruction"])
        self.assertEqual(set(sending["action_schema"]), {"points"})

    def test_p_floor_precedes_own_sending_cost_and_identity_is_not_revealed(self):
        world = p_world()
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        observation = world.observe("a")
        view = build_capability_view(observation)
        knowledge = view["shared_rule_knowledge"]["deduction_payoff"]
        self.assertEqual(knowledge["period_income_formula"], observation["rules"]["period_income_formula"])
        self.assertEqual(knowledge["income_deduction_per_point_received"], 3)
        self.assertEqual(knowledge["cost_per_point_sent"], 1)
        self.assertTrue(knowledge["floor_formula_recognized"])
        self.assertEqual(knowledge["floor_before_own_sending_cost"], 0)
        self.assertIn("再减去本人发送点数的成本", knowledge["explanation"])
        self.assertIn("最终本轮收益为负", knowledge["explanation"])
        limits = "".join(item["statement"] for item in view["information_limits"])
        self.assertIn("同时发送", limits)
        self.assertIn("不能认定全组无人", limits)
        self.assertIn("位置不是持久身份", limits)

    def test_p_knowledge_is_gated_by_visible_rules_and_does_not_guess_a_floor(self):
        observation = p_world().observe("a")
        del observation["rules"]["period_income_formula"]
        del observation["rules"]["punishers_are_anonymous"]
        view = build_capability_view(observation)
        self.assertNotIn("deduction_payoff", view["shared_rule_knowledge"])
        self.assertNotIn("全组无人", json.dumps(view, ensure_ascii=False))
        observation["rules"]["period_income_formula"] = "first_stage_income - 3 * total_received_points - total_sent_points"
        knowledge = build_capability_view(observation)["shared_rule_knowledge"]["deduction_payoff"]
        self.assertFalse(knowledge["floor_formula_recognized"])
        self.assertNotIn("floor_before_own_sending_cost", knowledge)

    def test_visible_zero_sending_cost_does_not_imply_negative_income(self):
        observation = p_world().observe("a")
        observation["rules"].update(
            cost_per_point_sent=0,
            period_income_formula="max(0, first_stage_income - 3 * total_received_points) - 0 * total_sent_points",
        )
        knowledge = build_capability_view(observation)["shared_rule_knowledge"]["deduction_payoff"]
        self.assertTrue(knowledge["floor_formula_recognized"])
        self.assertEqual(knowledge["cost_per_point_sent"], 0)
        self.assertIn("最终本轮收益不会低于0", knowledge["explanation"])

    def test_input_unchanged_and_nested_output_has_no_alias(self):
        observation = World("capability", IDS).observe("a")
        observation["action_schema"]["fixture"] = {"nested": [1, 2]}
        before = deepcopy(observation)
        view = build_capability_view(observation)
        self.assertEqual(observation, before)
        view["task"]["action_schema"]["fixture"]["nested"].append(3)
        self.assertEqual(observation, before)
        observation["action_schema"]["fixture"]["nested"].append(4)
        self.assertEqual(view["task"]["action_schema"]["fixture"]["nested"], [1, 2, 3])
        json.dumps(view, allow_nan=False)

    def test_missing_rules_do_not_become_invented_knowledge(self):
        observation = World("capability", IDS).observe("a")
        del observation["rules"]["return_per_group_token"]
        self.assertNotIn("contribution_payoff", build_capability_view(observation)["shared_rule_knowledge"])

    def test_invalid_numeric_rules_and_task_states_fail_explicitly(self):
        observation = World("capability", IDS).observe("a")
        for key, bad in (("group_size", True), ("group_size", 0), ("return_per_group_token", float("nan")),
                         ("retained_token_income", -1), ("return_per_group_token", "0.4")):
            altered = deepcopy(observation)
            altered["rules"][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                build_capability_view(altered)
        observation["stage"] = "punishment"
        with self.assertRaises(ValueError):
            build_capability_view(observation)


if __name__ == "__main__":
    unittest.main()
