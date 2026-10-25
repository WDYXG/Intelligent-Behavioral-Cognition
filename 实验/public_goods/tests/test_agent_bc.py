"""Check the norm ablation, private state and failure boundaries with offline models."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from public_goods.agents.agent_bc import (BRunner, CRunner, NORM_INSTRUCTION,
                                        judgment_schema, parse_judgment)
from public_goods.agents.cognition import (
    CognitiveUpdater, NORM_FIELDS, ORIGINAL_REVISION, SCOPE_REVISION, VERIFIED_REVISION,
    PROMPT_REVISION, prompt_pair, judgment_transition_context,
)
from public_goods.agents.state import AgentState, initial_state
from public_goods.agents.runner import BudgetExceeded, DecisionFailed, RunConfig
from public_goods.environment.world import InvalidAction, World
from public_goods.evaluation.human_reference import load_agent
from public_goods.models.client import MockModel, ModelResponse, TemporaryModelError


IDS = ("private-a", "private-b", "private-c", "private-d")
NORMS = NORM_FIELDS


def runner(kind=BRunner, model=None, config=None, output=None):
    return kind(World("paired-fixture", IDS, 17), model or MockModel(), config, output)


def unknown_judgment(norms=True, phase="N", stage="contribution", contract=4):
    return {field: {"assessment": None, "evidence": [], "uncertainty": "unknown"}
            for field in judgment_schema(phase, norms, stage, contract=contract)["fields"]}


def without_norms(value):
    if isinstance(value, dict):
        return {key: without_norms(item) for key, item in value.items() if key not in NORMS}
    if isinstance(value, list):
        return [without_norms(item) for item in value]
    return value


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        self.view = World("schema", IDS).observe(IDS[0])

    def test_preserved_prompts_and_v3_schema_keep_frozen_digests(self):
        def digest(value):
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        prompts = {
            ORIGINAL_REVISION: "93c8c14b96c11fea7a9415efaefc1cc43204874e022a38b7c41574b5ddfc435e",
            SCOPE_REVISION: "0185387814909468324d54acbbac64e5faa038f1aa1ae4d3663def398f891edb",
            VERIFIED_REVISION: "e7afc5077b6897a4f31c81ee33ae91be9f5d0fa9bb092a54c210fba7668eb352",
        }
        for revision, expected in prompts.items():
            self.assertEqual(digest(prompt_pair(revision)), expected)
        schemas = [
            "f90dfa51f20de9b6356d69c6839d554bd3417c00898300fb4a4a0b57f9c3660d",
            "d58f526adf07566fb3179cb71148fc10004a05d4881c7d864cc20a9aa1608865",
            "d58f526adf07566fb3179cb71148fc10004a05d4881c7d864cc20a9aa1608865",
            "df15a3008f13f6469b9148f396be40c0b2173ed282131de5fa8a54348e71ee29",
            "b9b0e37465df2f3990416b53b1567da8b489a11820d5d30642df017184a2cb8f",
            "d250ac88bd603b89f9d3f8b7e5851fd88e144c75994d3e4dcb375bffb139a4ea",
        ]
        actual = [digest(judgment_schema(phase, norms, stage, revision=VERIFIED_REVISION))
                  for norms in (False, True)
                  for phase, stage in (("N", "contribution"), ("P", "contribution"), ("P", "punishment"))]
        self.assertEqual(actual, schemas)

    def test_current_schema_predicts_only_the_unrevealed_stage_action(self):
        for norms in (False, True):
            current = judgment_schema("P", norms, "punishment")["fields"]
            previous = judgment_schema("P", norms, "punishment", revision=VERIFIED_REVISION)["fields"]
            self.assertEqual(set(current), set(previous))  # No output-field or cost expansion.
            self.assertIn("贡献已公开", current["behavior_expectation"])
            self.assertIn("尚未公开的扣减选择", current["behavior_expectation"])
            if norms:
                self.assertIn("本轮已公开", current["personal_norm"])
                self.assertIn("本轮已公开", current["enforcement_judgment"])
        first = judgment_schema("N", True)["fields"]
        self.assertIn("尚未公开的贡献", first["behavior_expectation"])

    def test_transition_context_preserves_old_text_and_labels_its_time_scope(self):
        previous = {"phase": "P", "round": 1, "stage": "contribution",
                    "judgment": {"facts": {"assessment": "旧判断：本轮贡献尚未公开"}}}
        frozen = deepcopy(previous)
        observation = {"phase": "P", "round": 1, "stage": "punishment"}
        context = judgment_transition_context(observation, previous)
        self.assertEqual(context["relation"], "contributions_revealed")
        self.assertEqual(context["previous_scope"], {k: previous[k] for k in observation})
        self.assertEqual(context["current_scope"], observation)
        self.assertEqual(previous, frozen)
        self.assertNotIn("旧判断：", json.dumps(context, ensure_ascii=False))
        next_round = judgment_transition_context(dict(observation, round=2, stage="contribution"), previous)
        self.assertEqual(next_round["relation"], "round_changed")
        new_condition = judgment_transition_context(observation, dict(previous, phase="N", round=10))
        self.assertEqual(new_condition["relation"], "condition_changed")
        self.assertEqual(judgment_transition_context(observation, None)["relation"], "no_previous")

    def test_unknown_and_subjective_norm_without_external_evidence_are_allowed(self):
        for norms in (True, False):
            value = unknown_judgment(norms)
            self.assertEqual(parse_judgment(json.dumps(value), self.view, norms), value)
            for assessment in (None, "尚无他人历史贡献记录，无法判断他人本轮会贡献多少。"):
                for evidence in ([], ["/history"], ["/history", "/rules"]):
                    value["behavior_expectation"] = {
                        "assessment": assessment, "evidence": evidence, "uncertainty": "unknown"}
                    with self.subTest(norms=norms, assessment=assessment, evidence=evidence):
                        self.assertEqual(parse_judgment(json.dumps(value), self.view, norms), value)
        value["facts"] = {"assessment": "Four players", "evidence": ["/rules/group_size"], "uncertainty": "low"}
        self.assertEqual(parse_judgment(json.dumps(value), self.view, False), value)
        value = unknown_judgment()
        value["personal_norm"] = {"assessment": "fixture preference", "evidence": [], "uncertainty": "high"}
        self.assertEqual(parse_judgment(json.dumps(value), self.view, True), value)

    def test_six_visible_references_are_allowed_without_changing_assessment(self):
        for norms in (True, False):
            value = unknown_judgment(norms)
            value["facts"] = {
                "assessment": "第1轮贡献阶段，有20枚代币，当前没有历史贡献记录。",
                "evidence": ["/round", "/stage", "/rules", "/history", "/phase", "/available_tokens"],
                "uncertainty": "low",
            }
            corrections = []
            self.assertEqual(parse_judgment(json.dumps(value), self.view, norms,
                                           normalizations=corrections), value)
            self.assertEqual(corrections, [])

    def test_ablation_requires_exact_fields_and_sanction_analysis_starts_in_P(self):
        for norms in (True, False):
            for phase in ("N", "P"):
                view = dict(self.view, phase=phase)
                valid = unknown_judgment(norms, phase)
                self.assertEqual("sanction_risk" in valid, phase == "P")
                self.assertEqual(parse_judgment(json.dumps(valid), view, norms), valid)
                wrong = unknown_judgment(not norms, phase)
                with self.assertRaises(InvalidAction):
                    parse_judgment(json.dumps(wrong), view, norms)
        with self.assertRaises(InvalidAction):
            parse_judgment(json.dumps(unknown_judgment(True, "P")), self.view, True)

    def test_evidence_cannot_reference_hidden_future_or_nonexistent_information(self):
        paths = ["/researcher_only_display_order", "/human_reference", "/rules/deduction_points_per_other_member",
                 "/history/999", "/history/-1", "/history/00", "/rules/~3", "rules", 1, {},
                 "/observation/researcher_only_display_order", "/observation/human_reference",
                 "/observation/rules/deduction_points_per_other_member", "/observation/history/999",
                 "/observation/history/-1", "/observation/history/00", "/observation/rules/~3",
                 "/observation/../rules", "/observation/observation/round"]
        for path in paths:
            value = unknown_judgment()
            value["facts"] = {"assessment": "claim", "evidence": [path], "uncertainty": "low"}
            with self.subTest(path=path), self.assertRaises(InvalidAction):
                parse_judgment(json.dumps(value), self.view, True)

    def test_enforcement_report_is_gated_by_current_action_and_legacy_contract_survives(self):
        for phase, stage in (("N", "contribution"), ("P", "contribution"), ("P", "punishment")):
            view = dict(self.view, phase=phase, stage=stage)
            for norms in (True, False):
                value = unknown_judgment(norms, phase, stage)
                self.assertEqual("norm_recognition" in value, norms)
                self.assertEqual("enforcement_judgment" in value, norms and stage == "punishment")
                self.assertEqual(parse_judgment(json.dumps(value), view, norms), value)
                legacy = unknown_judgment(norms, phase, stage, contract=3)
                self.assertNotIn("norm_recognition", legacy)
                self.assertNotIn("enforcement_judgment", legacy)
                self.assertEqual(parse_judgment(json.dumps(legacy), view, norms, contract=3), legacy)
                if norms:
                    with self.assertRaises(InvalidAction):
                        parse_judgment(json.dumps(legacy), view, norms)

    def test_updater_revalidates_C_field_boundary_and_decision_views_cannot_write_state(self):
        state = AgentState()
        updater = CognitiveUpdater(state, False)
        with self.assertRaises(InvalidAction):
            updater.prepare(self.view, unknown_judgment(True))
        self.assertEqual(state.snapshot(), initial_state())
        transition = updater.prepare(self.view, unknown_judgment(False))
        view = updater.readonly_view(transition["state"])
        self.assertNotIn("episodes", view)
        view["P"]["layers"]["L7"]["value"] = "invented"
        view["M"]["learning_enabled"] = True
        view["X"]["judgment"]["personal_norm"] = "injected"
        updater.commit(transition)
        self.assertEqual(state.snapshot()["P"], initial_state()["P"])
        self.assertEqual(state.snapshot()["M"], initial_state()["M"])
        self.assertEqual(state.snapshot()["X"]["judgment"], unknown_judgment(False))

    def test_malformed_json_and_belief_fields_are_rejected(self):
        invalid = ["null", "[]", "```json\n{}\n```", '{"facts": 1, "facts": 2}', '{"facts": NaN}']
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(InvalidAction):
                parse_judgment(text, self.view, True)
        changes = [{"assessment": True}, {"assessment": ""}, {"assessment": "x" * 321},
                   {"assessment": "unsupported", "uncertainty": "low"}, {"uncertainty": True},
                   {"uncertainty": "low"}, {"evidence": None},
                   {"extra": 0}, {"assessment": "claim", "uncertainty": "low", "evidence": ["/rules"] * 2}]
        for change in changes:
            value = unknown_judgment()
            value["facts"].update(change)
            with self.subTest(change=change), self.assertRaises(InvalidAction):
                parse_judgment(json.dumps(value), self.view, True)

    def test_visible_path_prefix_is_normalized_with_explicit_provenance(self):
        for norms in (True, False):
            value = unknown_judgment(norms)
            value["facts"] = {"assessment": "First round", "evidence": ["/observation/round"], "uncertainty": "low"}
            value["behavior_expectation"] = {
                "assessment": "No history is available", "evidence": ["/observation/history"], "uncertainty": "unknown"}
            original = json.dumps(value)
            expected = deepcopy(value)
            expected["facts"]["evidence"] = ["/round"]
            expected["behavior_expectation"]["evidence"] = ["/history"]
            corrections = []
            self.assertEqual(parse_judgment(original, self.view, norms, normalizations=corrections), expected)
            self.assertEqual(json.loads(original), value)
            self.assertEqual(corrections, [
                {"field": "facts", "index": 0, "from": "/observation/round", "to": "/round"},
                {"field": "behavior_expectation", "index": 0, "from": "/observation/history", "to": "/history"},
            ])
            canonical_corrections = []
            self.assertEqual(parse_judgment(json.dumps(expected), self.view, norms,
                                           normalizations=canonical_corrections), expected)
            self.assertEqual(canonical_corrections, [])

    def test_valid_original_path_takes_priority_over_prefix_normalization(self):
        view = deepcopy(self.view)
        view["observation"] = {"round": "distinct nested visible value"}
        value = unknown_judgment(False)
        value["facts"] = {
            "assessment": "Nested visible field", "evidence": ["/observation/round"], "uncertainty": "low"}
        corrections = []
        self.assertEqual(parse_judgment(json.dumps(value), view, False, normalizations=corrections), value)
        self.assertEqual(corrections, [])

    def test_normalization_does_not_hide_duplicates_or_later_invalid_references(self):
        for second in ("/round", "/observation/human_reference"):
            value = unknown_judgment(False)
            value["facts"] = {
                "assessment": "claim", "evidence": ["/observation/round", second], "uncertainty": "low"}
            corrections = []
            with self.subTest(second=second), self.assertRaises(InvalidAction):
                parse_judgment(json.dumps(value), self.view, False, normalizations=corrections)
            self.assertEqual(corrections, [
                {"field": "facts", "index": 0, "from": "/observation/round", "to": "/round"}])

    def test_path_and_response_capacity_limits_still_apply(self):
        view = deepcopy(self.view)
        view["x" * 200] = "visible but path is too long"
        value = unknown_judgment(False)
        value["facts"] = {
            "assessment": "claim", "evidence": ["/" + "x" * 200], "uncertainty": "low"}
        with self.assertRaises(InvalidAction):
            parse_judgment(json.dumps(value), view, False)
        with self.assertRaises(InvalidAction):
            parse_judgment(json.dumps(unknown_judgment(False)), self.view, False, max_bytes=10)

    def test_all_remaining_field_errors_are_reported_together(self):
        value = unknown_judgment()
        value["facts"] = {"assessment": "claim", "evidence": ["/hidden_information"], "uncertainty": "low"}
        value["behavior_expectation"] = {"assessment": "claim", "evidence": [], "uncertainty": "low"}
        value["normative_expectation"] = {"assessment": None, "evidence": ["/history"], "uncertainty": "low"}
        with self.assertRaises(InvalidAction) as caught:
            parse_judgment(json.dumps(value), self.view, True)
        for field in ("facts", "behavior_expectation", "normative_expectation"):
            self.assertIn(field, str(caught.exception))


class TwoStageTests(unittest.TestCase):
    def test_prompt_revisions_keep_group_ablation_and_strict_action_validation(self):
        for revision in (ORIGINAL_REVISION, SCOPE_REVISION, VERIFIED_REVISION, PROMPT_REVISION):
            tasks = [cls(World('revision-fixture', IDS), MockModel(), prompt_revision=revision)
                     for cls in (BRunner, CRunner)]
            for task in tasks:
                task._decide(IDS[0], task.world.observe(IDS[0]))
            for left, right in zip(tasks[0].calls, tasks[1].calls):
                lm, rm = left['request']['messages'], right['request']['messages']
                self.assertEqual(without_norms(json.loads(lm[1]['content'])), json.loads(rm[1]['content']))
                self.assertEqual(lm[0]['content'].removesuffix(NORM_INSTRUCTION), rm[0]['content'])
            for call in tasks[0].calls:
                payload = json.loads(call['request']['messages'][1]['content'])
                self.assertEqual('transition_context' in payload,
                                 revision == PROMPT_REVISION and call['step'] == 'judgment')
        from public_goods.agents.runner import parse_action
        with self.assertRaises(InvalidAction):
            parse_action('{"type":"json_object","contribution":10}', 'contribution')

    def test_complete_runs_counts_replay_scores_and_code_provenance(self):
        for kind in (BRunner, CRunner):
            with self.subTest(kind=kind.__name__), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "run"
                task = runner(kind, output=output)
                result = task.run()
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["simulated_calls"], 240)
                self.assertEqual(result["valid_decisions"], 120)
                self.assertEqual(result["valid_judgments"], 120)
                self.assertEqual(result["settled_person_rounds"], 80)
                self.assertEqual(result["implementation"], kind.implementation)
                self.assertEqual(result["real_api_calls"], 0)
                self.assertIsNone(result["measured_model_tokens"])
                self.assertIsNone(result["model_cost_usd"])
                self.assertTrue(result["replay_verified"])
                self.assertEqual(len({call["call_id"] for call in task.calls}), 240)
                people, peers = load_agent(output / "trajectory.jsonl")
                self.assertEqual((len(people), len(peers)), (80, 120))
                events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
                self.assertEqual(World.replay(events).trajectory(), task.world.trajectory())
                states = [json.loads(line) for line in (output / "judgments.jsonl").read_text().splitlines()]
                self.assertEqual(states, task.judgments)
                self.assertEqual(len(states), 120)
                transitions = [json.loads(line) for line in (output / "state_transitions.jsonl").read_text().splitlines()]
                self.assertEqual(transitions, task.state_transitions)
                self.assertEqual(len(transitions), 200)
                for pid, store in task.states.items():
                    state = store.snapshot()
                    self.assertEqual(state["version"], 50)
                    self.assertEqual(state["P"], initial_state()["P"])
                    self.assertEqual(state["M"], initial_state()["M"])
                    self.assertEqual([e["event"] for e in state["episodes"]], task.world.observe(pid)["history"])
                    self.assertEqual(state["episodes"][-1]["event"]["round"], 10)
                    self.assertEqual(state["episodes"][-1]["event"]["phase"], "P")
                config = json.loads((output / "config.json").read_text())
                for name, digest in config["code_sha256"].items():
                    path = Path(__file__).resolve().parents[1] / name
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_B_C_share_observations_call_schedule_sampling_and_limits(self):
        b, c = runner(BRunner), runner(CRunner)
        b.run()
        c.run()
        self.assertEqual(asdict(b.config), asdict(c.config))
        self.assertEqual(b.world.trajectory(), c.world.trajectory())
        for left, right in zip(b.calls, c.calls):
            self.assertEqual(left["call_id"], right["call_id"])
            for name in ("seed", "temperature", "max_output_tokens"):
                self.assertEqual(left["request"][name], right["request"][name])
            lm, rm = left["request"]["messages"], right["request"]["messages"]
            lp, rp = json.loads(lm[1]["content"]), json.loads(rm[1]["content"])
            self.assertEqual(lp["observation"], rp["observation"])
            self.assertEqual(without_norms(lp), rp)
            self.assertEqual(lm[0]["content"].removesuffix(NORM_INSTRUCTION), rm[0]["content"])
            self.assertEqual(len(lm), 2)
            self.assertEqual(len(rm), 2)

    def test_private_state_is_carried_only_after_commit_and_survives_phase_transition(self):
        class MarkedModel(MockModel):
            def complete(self, request):
                response = super().complete(request)
                payload = json.loads(request.messages[1]["content"])
                if payload["step"] == "judgment":
                    value = json.loads(response.text)
                    value["facts"]["assessment"] = f"unique fixture {len(self.requests)}"
                    if "personal_norm" in value:
                        value["personal_norm"] = {"assessment": f"private norm {len(self.requests)}",
                                                  "evidence": [], "uncertainty": "high"}
                    return ModelResponse(json.dumps(value))
                return response

        for kind in (BRunner, CRunner):
            task = runner(kind, MarkedModel())
            task.run()
            previous = {pid: None for pid in IDS}
            first_P = 0
            for call in task.calls:
                if call["step"] != "judgment":
                    continue
                pid = call["participant_id"]
                payload = json.loads(call["request"]["messages"][1]["content"])
                self.assertEqual(payload["agent_state"]["X"], previous[pid])
                if call["phase"] == "P" and call["round"] == 1 and call["stage"] == "contribution":
                    first_P += 1
                    self.assertEqual(previous[pid]["phase"], "N")
                    self.assertEqual(previous[pid]["round"], 10)
                previous[pid] = {key: call[key] for key in ("phase", "round", "stage", "judgment")}
            self.assertEqual(first_P, 4)
            self.assertEqual(task._previous, previous)

    def test_judgment_is_passed_to_decision_without_overriding_the_model_action(self):
        class JudgmentDrivenModel(MockModel):
            def complete(self, request):
                response = super().complete(request)
                payload = json.loads(request.messages[1]["content"])
                if payload["step"] == "judgment":
                    value = json.loads(response.text)
                    if "personal_norm" in value:
                        value["personal_norm"] = {"assessment": "fixture choose 6", "evidence": [], "uncertainty": "high"}
                    return ModelResponse(json.dumps(value))
                if payload["observation"]["stage"] == "contribution":
                    choice = 6 if "personal_norm" in payload["agent_state"]["X"]["judgment"] else 10
                    return ModelResponse(json.dumps({"contribution": choice}))
                return response

        for kind, choice in ((BRunner, 6), (CRunner, 10)):
            task = runner(kind, JudgmentDrivenModel())
            task.run()
            self.assertEqual({row["contribution"] for row in task.world.trajectory()}, {choice})
            for judgment, decision in zip(task.calls[::2], task.calls[1::2]):
                payload = json.loads(decision["request"]["messages"][1]["content"])
                self.assertEqual(payload["agent_state"]["X"]["judgment"], judgment["judgment"])

    def test_prompts_do_not_leak_other_identities_hidden_logs_or_future_rules(self):
        for kind in (BRunner, CRunner):
            task = runner(kind)
            task.run()
            for call in task.calls:
                messages = call["request"]["messages"]
                text = json.dumps(messages, ensure_ascii=False)
                for forbidden in (*IDS, "researcher_only_display_order", "human_reference", "group_id"):
                    self.assertNotIn(forbidden, text)
                if call["phase"] == "N":
                    for future in ("sanction_risk", "punishment", "deduction", "lump_sum", "惩罚"):
                        self.assertNotIn(future, text)
                if kind is CRunner:
                    for norm in NORMS:
                        self.assertNotIn(norm, text)

    def test_invalid_judgment_and_action_retry_only_the_failed_step(self):
        for kind in (BRunner, CRunner):
            valid = json.dumps(unknown_judgment(kind is BRunner))
            task = runner(kind, MockModel(["invalid", valid, '{"contribution": 99}', '{"contribution": 4}']))
            result = task.run()
            self.assertEqual(result["simulated_calls"], 242)
            self.assertEqual(result["invalid_outputs"], 2)
            self.assertEqual([c["step"] for c in task.calls[:4]], ["judgment", "judgment", "decision", "decision"])
            for first, second in ((0, 1), (2, 3)):
                self.assertEqual(task.calls[first]["request"]["messages"][1], task.calls[second]["request"]["messages"][1])
            self.assertEqual(task.world.trajectory()[0]["contribution"], 4)

    def test_C_rejects_norm_output_instead_of_silently_stripping_or_remembering_it(self):
        task = runner(CRunner, MockModel([json.dumps(unknown_judgment(True))]))
        self.assertEqual(task.run()["invalid_outputs"], 1)
        for request in task.model.requests:
            for norm in NORMS:
                self.assertNotIn(norm, json.dumps(request.messages))
        for state in task.judgments:
            self.assertEqual(without_norms(state), state)

    def test_last_person_failure_never_commits_partial_actions_or_private_state(self):
        for kind in (BRunner, CRunner):
            valid = json.dumps(unknown_judgment(kind is BRunner))
            for failing_step in ("judgment", "decision"):
                replies = [valid, '{"contribution": 10}'] * 3
                if failing_step == "decision":
                    replies.append(valid)
                replies += ["invalid"] * 3
                with self.subTest(kind=kind.__name__, step=failing_step), tempfile.TemporaryDirectory() as folder:
                    output = Path(folder) / "failed"
                    task = runner(kind, MockModel(replies), output=output)
                    before = [task.world.observe(pid) for pid in IDS]
                    with self.assertRaises(DecisionFailed):
                        task.run()
                    self.assertEqual(before, [task.world.observe(pid) for pid in IDS])
                    self.assertEqual(task.world.trajectory(), [])
                    self.assertEqual(task.judgments, [])
                    self.assertEqual(task._previous, dict.fromkeys(IDS))
                    self.assertEqual(task._pending, {})
                    self.assertTrue(all(store.snapshot() == initial_state() for store in task.states.values()))
                    self.assertEqual(task.state_transitions, [])
                    self.assertFalse((output / "trajectory.jsonl").exists())
                    self.assertFalse((output / "judgments.jsonl").exists())
                    self.assertEqual(json.loads((output / "summary.json").read_text())["status"], "failed")

    def test_budget_exhaustion_between_calls_retains_only_last_committed_state(self):
        for kind in (BRunner, CRunner):
            task = runner(kind, config=RunConfig(max_calls=9))
            with self.assertRaises(BudgetExceeded):
                task.run()
            self.assertEqual(len(task.calls), 9)
            self.assertEqual(len(task.world.trajectory()), 4)
            self.assertEqual(len(task.judgments), 4)
            self.assertEqual(task.world.round, 2)
            for state in task._previous.values():
                self.assertEqual(state["round"], 1)
            self.assertEqual(task._pending, {})
            self.assertTrue(all(store.snapshot()["version"] == 2 for store in task.states.values()))

    def test_failure_after_world_acceptance_preserves_world_event_and_blocks_scoring(self):
        class BrokenPersistence(BRunner):
            def _append(self, name, rows):
                if name == "state_transitions.jsonl":
                    raise OSError("fixture state log unavailable")
                return super()._append(name, rows)

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "failed"
            task = runner(BrokenPersistence, output=output)
            with self.assertRaisesRegex(OSError, "fixture state log"):
                task.run()
            events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
            self.assertEqual(World.replay(events).trajectory(), task.world.trajectory())
            self.assertEqual(len(task.world.trajectory()), 4)
            self.assertEqual(task.summary["status"], "failed")
            self.assertFalse(task.summary["replay_verified"])
            self.assertFalse((output / "trajectory.jsonl").exists())
            self.assertEqual(task._pending, {})

    def test_transient_decision_retry_and_fatal_failure_are_not_hidden(self):
        for kind in (BRunner, CRunner):
            valid = json.dumps(unknown_judgment(kind is BRunner))
            task = runner(kind, MockModel([valid, TemporaryModelError("offline timeout")]))
            self.assertEqual(task.run()["transient_errors"], 1)
            self.assertEqual(len(task.calls), 241)
            self.assertEqual([c["step"] for c in task.calls[:3]], ["judgment", "decision", "decision"])
            task = runner(kind, MockModel([valid, RuntimeError("offline error")]))
            with self.assertRaises(RuntimeError):
                task.run()
            self.assertEqual(task.calls[-1]["status"], "fatal_error")
            self.assertEqual(len(task.calls), 2)
            self.assertEqual(task.judgments, [])
            self.assertEqual(task._pending, {})

    def test_input_output_limits_live_models_and_overwrites_are_blocked(self):
        for kind in (BRunner, CRunner):
            task = runner(kind, config=RunConfig(max_input_bytes=1))
            with self.assertRaises(BudgetExceeded):
                task.run()
            self.assertEqual(task.model.requests, [])
            task = runner(kind, config=RunConfig(max_response_bytes=3, max_attempts=2))
            with self.assertRaises(DecisionFailed):
                task.run()
            self.assertEqual(len(task.calls), 2)
            model = MockModel()
            model.mode = "live"
            with self.assertRaises(DecisionFailed):
                runner(kind, model)
            self.assertEqual(model.requests, [])
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(FileExistsError):
                    runner(kind, output=folder)
            task = runner(kind)
            task.run()
            previous = deepcopy(task._previous)
            with self.assertRaises(DecisionFailed):
                task.run()
            self.assertEqual(task._previous, previous)


if __name__ == "__main__":
    unittest.main()
