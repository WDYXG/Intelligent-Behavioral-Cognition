"""Checkpoint execution with real state continuity and replayable failures."""

import json
from pathlib import Path
import tempfile
import unittest

from public_goods.agents.runner import DecisionFailed
from public_goods.agents.state import initial_state
from public_goods.experiments.sequential_diagnostic import run_diagnostic, audit_saved_diagnostic
from public_goods.models.client import MockModel, ModelResponse


class SequentialDiagnosticTests(unittest.TestCase):
    def test_two_full_rounds_keep_private_state_feedback_and_original_horizon(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "run"
            result = run_diagnostic(out, MockModel())
            self.assertEqual((result["status"], result["attempts"], result["real_api_calls"]),
                             ("checkpoint_completed", 64, 0))
            for arm in ("B", "C"):
                base = out / arm
                summary = result["arms"][arm]
                self.assertEqual((summary["phase"], summary["round"], summary["stage"]), ("P", 2, "feedback"))
                self.assertFalse(summary["experiment_finished"])
                self.assertEqual(summary["model_person_rounds_settled"], 8)
                self.assertEqual(summary["state_transitions"], 24)
                self.assertFalse((base / "trajectory.jsonl").exists())
                states = json.loads((base / "final_agent_states.json").read_text())
                for state in states.values():
                    self.assertEqual(state["version"], 6)
                    self.assertEqual(state["P"], initial_state()["P"])
                    self.assertEqual(state["M"], initial_state()["M"])
                    self.assertEqual((state["X"]["round"], state["X"]["stage"]), (2, "punishment"))
                    self.assertEqual(state["episodes"][-1]["event"]["round"], 2)
                calls = [json.loads(s) for s in (base / "calls.jsonl").read_text().splitlines()]
                own = [json.loads(c["request"]["messages"][1]["content"]) for c in calls
                       if c["participant_id"] == "member-a" and c["step"] == "judgment"]
                self.assertEqual([p["agent_state"]["version"] for p in own], [0, 1, 3, 4])
                self.assertIsNone(own[0]["agent_state"]["X"])
                self.assertEqual(own[2]["agent_state"]["X"]["round"], 1)
                self.assertEqual(own[2]["agent_state"]["X"]["stage"], "punishment")
                for payload in own:
                    temporal = payload["capability_view"]["verified_context"]["temporal"]
                    self.assertEqual(payload["observation"]["rules"]["rounds"], 10)
                    self.assertEqual(temporal["rounds_remaining_after_current"], 10 - payload["observation"]["round"])
                    self.assertFalse(temporal["experiment_ends_after_current_round"])
            audit = audit_saved_diagnostic(out)
            self.assertTrue(audit["passed"])
            self.assertEqual(sum(x["calls_checked"] for x in audit["arms"].values()), 64)
            self.assertEqual(audit["real_api_calls_in_audit"], 0)
            with self.assertRaises(FileExistsError):
                run_diagnostic(out, MockModel())

    def test_partial_batch_failure_discards_candidates_and_preserves_raw_attempts(self):
        class FailThirdParticipant(MockModel):
            def complete(self, request):
                if len(self.requests) >= 4:
                    self.requests.append(request)
                    return ModelResponse("invalid")
                return super().complete(request)

        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "failed"
            with self.assertRaises(DecisionFailed):
                run_diagnostic(out, FailThirdParticipant())
            result = json.loads((out / "report.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["attempts"], 7)
            for arm in ("B", "C"):
                self.assertEqual(result["arms"][arm]["stages_committed"], 0)
                states = json.loads((out / arm / "final_agent_states.json").read_text())
                self.assertTrue(all(s == initial_state() for s in states.values()))
                self.assertFalse((out / arm / "model_segment.jsonl").exists())
            calls = [json.loads(s) for s in (out / "B/calls.jsonl").read_text().splitlines()]
            self.assertEqual([c["status"] for c in calls[-3:]], ["invalid_output"] * 3)
            with self.assertRaises(ValueError):
                audit_saved_diagnostic(out)

    def test_audit_rejects_corrupted_previous_private_state_in_recorded_request(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "run"
            run_diagnostic(out, MockModel())
            path = out / "B/calls.jsonl"
            calls = [json.loads(s) for s in path.read_text().splitlines()]
            payload = json.loads(calls[8]["request"]["messages"][1]["content"])
            payload["agent_state"]["version"] = 999
            calls[8]["request"]["messages"][1]["content"] = json.dumps(payload, ensure_ascii=False)
            path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in calls))
            with self.assertRaises(ValueError):
                audit_saved_diagnostic(out)

    def test_live_calls_require_authorization_before_artifacts_or_requests(self):
        model = MockModel()
        model.mode = "live"
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "blocked"
            with self.assertRaises(ValueError):
                run_diagnostic(out, model)
            self.assertFalse(out.exists())
            self.assertEqual(model.requests, [])


if __name__ == "__main__":
    unittest.main()
