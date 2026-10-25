"""Independent budget arithmetic and cross-record tampering checks."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.environment.world import World
from public_goods.evaluation.preflight import AcceptanceModel, audit_run, audit_saved_run, trial_budget
from public_goods.models.client import MockModel, ModelResponse


class BudgetTests(unittest.TestCase):
    def test_minimal_trial_counts_calls_and_caps_without_inventing_prices(self):
        value = trial_budget()
        self.assertEqual(value["total_groups"], 3)
        self.assertEqual([v["calls_without_retries"] for v in value["arms"].values()], [120, 240, 240])
        self.assertEqual(value["totals"], {
            "calls_without_retries": 600, "calls_with_max_retries": 1800,
            "requested_output_token_ceiling_without_retries": 337920,
            "requested_output_token_ceiling_with_retries": 1013760,
        })
        self.assertIsNone(value["input_tokens"])
        self.assertIsNone(value["estimated_cost"])
        self.assertEqual(value["arms"]["B"]["per_group_settings"], value["arms"]["C"]["per_group_settings"])

    def test_budget_scales_by_complete_groups_and_rejects_invalid_counts(self):
        base, larger = trial_budget(1), trial_budget(3)
        for key, value in base["totals"].items():
            self.assertEqual(larger["totals"][key], value * 3)
        for invalid in (0, -1, 1.5, True, "3"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                trial_budget(invalid)


class TraceAuditTests(unittest.TestCase):
    def test_history_indices_include_phase_events_instead_of_using_round_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            task = BRunner(World("history-indices", ["a", "b", "c", "d"]), MockModel(), output=Path(folder) / "run")
            task.run()
            call = next(c for c in task.calls if c["phase"] == "P" and c["round"] == 2 and c["step"] == "judgment")
            payload = json.loads(call["request"]["messages"][1]["content"])
            self.assertEqual(len(payload["observation"]["history"]), 13)
            self.assertEqual(payload["history_references"][-1],
                             {"path": "/history/12", "type": "round_feedback", "phase": "P", "round": 1})
            self.assertEqual(payload["history_references"][11]["type"], "new_experiment_announced")
            self.assertTrue(audit_run(task)["passed"])

    def test_normalized_evidence_preserves_raw_response_and_is_audited(self):
        class PrefixedModel(AcceptanceModel):
            def complete(self, request):
                response = super().complete(request)
                payload = json.loads(request.messages[1]["content"])
                if payload.get("step") == "judgment":
                    value = json.loads(response.text)
                    for belief in value.values():
                        belief["evidence"] = ["/observation" + p for p in belief["evidence"]]
                    return ModelResponse(json.dumps(value))
                return response

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            task = CRunner(World("normalize", ["a", "b", "c", "d"]), PrefixedModel("varied"), output=output)
            task.run()
            result = audit_saved_run(output)
            self.assertTrue(result["passed"])
            self.assertGreater(task.summary["evidence_path_normalizations"], 0)
            first = task.calls[0]
            self.assertIn("/observation/", first["response"]["text"])
            self.assertNotIn("/observation/", json.dumps(first["judgment"]))
            self.assertEqual(json.loads(task.calls[1]["request"]["messages"][1]["content"])["agent_state"]["X"]["judgment"], first["judgment"])
            first["normalizations"] = []
            with self.assertRaisesRegex(ValueError, "normalization log mismatch"):
                audit_run(task)

    def test_saved_audit_includes_rejected_attempts_and_detects_retry_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            task = BRunner(World("retry-audit", ["a", "b", "c", "d"]), MockModel(["invalid"]), output=output)
            task.run()
            result = audit_saved_run(output)
            self.assertTrue(result["passed"])
            self.assertEqual(result["calls_checked"], 241)
            self.assertEqual(result["valid_steps_checked"], 240)
            task.calls[0]["response"]["text"] = task.calls[1]["response"]["text"]
            with self.assertRaisesRegex(ValueError, "incorrectly rejected"):
                audit_run(task)

    def test_audit_rejects_visibility_state_and_action_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            task = BRunner(World("audit", ["a", "b", "c", "d"]), AcceptanceModel("varied"), output=Path(folder) / "run")
            task.run()
            self.assertTrue(audit_run(task)["passed"])
            original = deepcopy(task.calls)
            for changed, index in (("observation", 0), ("agent_state", 0), ("agent_state", 1),
                                   ("capability_view", 0), ("capability_view", 1),
                                   ("history_references", 0), ("judgment_schema", 0)):
                payload = json.loads(task.calls[index]["request"]["messages"][1]["content"])
                if changed == "observation":
                    payload[changed]["researcher_only"] = "hidden information"
                else:
                    payload[changed] = {"foreign_state": True}
                task.calls[index]["request"]["messages"][1]["content"] = json.dumps(payload)
                task.calls[index]["input_bytes"] = len(json.dumps(task.calls[index]["request"]["messages"], ensure_ascii=False).encode())
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    audit_run(task)
                task.calls = deepcopy(original)
            task.calls[1]["action"] = {"contribution": 19}
            with self.assertRaises(ValueError):
                audit_run(task)

    def test_saved_audit_rejects_initial_state_and_committed_feedback_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            task = BRunner(World("saved-state", ["a", "b", "c", "d"]), MockModel(), output=output)
            task.run()
            self.assertEqual(audit_saved_run(output)["versioned_state_transitions_checked"], 200)
            path = output / "config.json"
            original = path.read_text()
            config = json.loads(original)
            config["initial_agent_states"]["a"]["M"]["learning_enabled"] = True
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "Initial state"):
                audit_saved_run(output)
            path.write_text(original)
            path = output / "state_transitions.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[-1]["state"]["episodes"][-1]["event"]["received_points"] = 999
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            with self.assertRaisesRegex(ValueError, "state transition"):
                audit_saved_run(output)

    def test_long_judgments_remain_within_byte_capacity_and_C_field_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            task = CRunner(World("capacity", ["a", "b", "c", "d"]), AcceptanceModel("long_judgment"), output=Path(folder) / "run")
            task.run()
            self.assertTrue(audit_run(task)["passed"])
            for call in task.calls:
                self.assertLessEqual(call["input_bytes"], task.config.max_input_bytes)
                self.assertLessEqual(len(call["response"]["text"].encode()), task.config.max_response_bytes)
                if call["step"] == "judgment":
                    self.assertEqual({len(v["assessment"]) for v in call["judgment"].values()}, {320})
                    self.assertNotIn("personal_norm", call["judgment"])
                    self.assertNotIn("normative_expectation", call["judgment"])


if __name__ == "__main__":
    unittest.main()
