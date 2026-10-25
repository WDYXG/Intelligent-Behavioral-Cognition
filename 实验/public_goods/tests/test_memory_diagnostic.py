"""Historical replay, exact single-input intervention and failure preservation."""

import json
from pathlib import Path
import tempfile
import unittest

from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.cognition import STAGE_REVISION
from public_goods.agents.runner import DecisionFailed
from public_goods.environment.world import World
from public_goods.evaluation.preflight import audit_saved_run
from public_goods.experiments.memory_diagnostic import CHECKPOINTS, plan, rows, run_diagnostic, load_checkpoints
from public_goods.models.client import MockModel

IDS = ("member-a", "member-b", "member-c", "member-d")


class MemoryDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.sources = {arm: cls.root / arm for arm in ("B", "C")}
        for arm, runner in (("B", BRunner), ("C", CRunner)):
            runner(World("source-" + arm, IDS), MockModel(), output=cls.sources[arm], prompt_revision=STAGE_REVISION).run()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_only_old_x_changes_and_current_decision_keeps_new_x(self):
        output = self.root / "paired"
        report = run_diagnostic(output, MockModel(), self.sources, repetitions=2)
        self.assertEqual(report["status"], "completed")
        self.assertEqual((report["attempts"], report["real_api_calls"]), (96, 0))
        for repetition in range(2):
            for key in CHECKPOINTS:
                for arm in ("B", "C"):
                    pair = [r for r in report["results"] if r["repetition"] == repetition and r["checkpoint"] == list(key)
                            and r["arm"] == arm and r["variant"].startswith("v5_")]
                    self.assertEqual(len(pair), 2)
                    self.assertEqual(len({r["initial_focal_state_sha256"] for r in pair}), 1)
                    requests = []
                    for result in pair:
                        saved = rows(output / result["artifact"] / "calls.jsonl")
                        payload = json.loads(saved[0]["request"]["messages"][1]["content"])
                        decision = json.loads(saved[1]["request"]["messages"][1]["content"])
                        self.assertEqual(payload["agent_state"]["X"] is None, result["variant"] == "v5_omit")
                        self.assertIsNotNone(decision["agent_state"]["X"])
                        self.assertEqual(decision["agent_state"]["version"], payload["agent_state"]["version"] + 1)
                        self.assertFalse(result["action_executed"] or result["state_committed"])
                        payload["agent_state"]["X"] = None
                        saved[0]["request"]["messages"][1]["content"] = json.dumps(payload, sort_keys=True)
                        requests.append(saved[0]["request"])
                    self.assertEqual(*requests)

    def test_failures_and_all_attempts_remain_in_report(self):
        output = self.root / "failed"
        with self.assertRaises(DecisionFailed):
            run_diagnostic(output, MockModel(["invalid"] * 3), self.sources, repetitions=1)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual((report["status"], report["attempts"]), ("failed", 3))
        self.assertEqual(len(report["results"]), 1)
        self.assertNotIn("action", report["results"][0])

    def test_source_replay_and_live_gate(self):
        contexts = load_checkpoints(self.sources["B"], "B")
        self.assertEqual(set(contexts), set(CHECKPOINTS))
        for value in contexts.values():
            self.assertIsNotNone(value["store"].snapshot()["X"])
        for value in (0, 4, True, 1.5):
            with self.assertRaises(ValueError):
                plan(value)
        model = MockModel()
        model.mode = "live"
        with self.assertRaises(ValueError):
            run_diagnostic(self.root / "blocked", model, self.sources)
        self.assertEqual(model.requests, [])

    def test_new_full_runs_replay_with_both_memory_modes(self):
        for mode in ("retain", "omit"):
            folder = self.root / ("full-" + mode)
            BRunner(World(mode, IDS), MockModel(), output=folder, previous_x_mode=mode).run()
            self.assertTrue(audit_saved_run(folder)["passed"])

