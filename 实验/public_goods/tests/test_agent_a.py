"""Offline A-loop acceptance tests; never contact a model provider."""

import json
from pathlib import Path
import tempfile
import unittest

from public_goods.agents.agent_a import (AgentRunner, BudgetExceeded, DecisionFailed,
                                        RunConfig, parse_action)
from public_goods.models.client import MockModel, TemporaryModelError
from public_goods.environment.world import InvalidAction, World


IDS = ("private-a", "private-b", "private-c", "private-d")


def runner(model=None, config=None, output=None):
    return AgentRunner(World("A-test", IDS, 17), model or MockModel(), config, output)


class ParserTests(unittest.TestCase):
    def test_valid_actions_use_the_world_contract(self):
        self.assertEqual(parse_action('{"contribution": 0}', "contribution"), {"contribution": 0})
        self.assertEqual(parse_action('{"points": [0, 10, 2]}', "punishment"), {"points": [0, 10, 2]})

    def test_no_fences_duplicate_keys_guessing_clipping_or_nonfinite_numbers(self):
        invalid = ['```json\n{"contribution": 5}\n```', 'I choose {"contribution": 5}',
                   '{"contribution": 1, "contribution": 2}', '{"contribution": NaN}',
                   '{"contribution": Infinity}', '{"contribution": true}', '{"contribution": 5.0}',
                   '{"contribution": -1}', '{"contribution": 21}', '{"contribution": "5"}',
                   '{"contribution": 5, "reason": "good"}', 'null', '[]']
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(InvalidAction):
                parse_action(text, "contribution")
        with self.assertRaises(InvalidAction):
            parse_action('{"points": [0, 0]}', "punishment")


class LoopTests(unittest.TestCase):
    def test_complete_loop_counts_payoffs_and_evaluator_compatibility(self):
        from public_goods.evaluation.human_reference import load_agent
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run"
            task = runner(output=path)
            summary = task.run()
            self.assertEqual(summary["simulated_calls"], 120)
            self.assertEqual(summary["valid_decisions"], 120)
            self.assertEqual(summary["settled_person_rounds"], 80)
            self.assertEqual(summary["real_api_calls"], 0)
            self.assertTrue(summary["replay_verified"])
            self.assertIsNone(summary["model_cost_usd"])
            person, peers = load_agent(path / "trajectory.jsonl")
            self.assertEqual(len(person), 80)
            self.assertEqual(len(peers), 120)
            for pid in IDS:
                self.assertEqual(task.world.observe(pid)["total_income"], 545)  # 20×26 + 25.
            events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()]
            self.assertEqual(World.replay(events).trajectory(), task.world.trajectory())

    def test_prompts_do_not_contain_other_identities_or_future_rules(self):
        model = MockModel()
        task = runner(model)
        task.run()
        n_calls = 0
        for request in model.requests:
            messages = request.messages
            view = json.loads(messages[1]["content"])
            serialized = json.dumps(messages)
            for pid in IDS:
                self.assertNotIn(pid, serialized)
            self.assertNotIn("researcher_only_display_order", serialized)
            self.assertNotIn("human_reference", serialized)
            if view["phase"] == "N":
                n_calls += 1
                for future in ("punishment", "lump_sum", "deduction"):
                    self.assertNotIn(future, serialized)
            self.assertEqual(len(messages), 2)
        self.assertEqual(n_calls, 40)

    def test_invalid_output_retried_without_replacing_action(self):
        task = runner(MockModel(['{"contribution": 99}', '{"contribution": 7}']))
        summary = task.run()
        self.assertEqual(summary["simulated_calls"], 121)
        self.assertEqual(summary["invalid_outputs"], 1)
        self.assertEqual(task.world.trajectory()[0]["contribution"], 7)
        self.assertEqual(task.calls[0]["status"], "invalid_output")
        self.assertEqual(task.calls[1]["attempt"], 2)
        self.assertEqual(task.calls[0]["request"]["messages"][1], task.calls[1]["request"]["messages"][1])

    def test_last_person_failure_does_not_commit_earlier_actions(self):
        model = MockModel(['{"contribution": 10}'] * 3 + ['invalid'] * 3)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "failed"
            task = runner(model, output=path)
            before = {pid: task.world.observe(pid) for pid in IDS}
            with self.assertRaises(DecisionFailed):
                task.run()
            self.assertEqual(task.summary["status"], "failed")
            self.assertEqual(task.summary["simulated_calls"], 6)
            self.assertEqual(task.world.trajectory(), [])
            self.assertEqual(before, {pid: task.world.observe(pid) for pid in IDS})
            self.assertFalse((path / "trajectory.jsonl").exists())
            self.assertTrue((path / "calls.jsonl").exists())
            self.assertEqual(json.loads((path / "summary.json").read_text())["status"], "failed")

    def test_transient_error_retries_and_fatal_error_stops(self):
        task = runner(MockModel([TemporaryModelError("fixture timeout")]))
        self.assertEqual(task.run()["transient_errors"], 1)
        self.assertEqual(len(task.calls), 121)
        task = runner(MockModel([RuntimeError("fixture configuration error")]))
        with self.assertRaises(RuntimeError):
            task.run()
        self.assertEqual(len(task.calls), 1)
        self.assertEqual(task.calls[0]["status"], "fatal_error")
        self.assertEqual(task.summary["status"], "failed")

    def test_call_and_input_capacity_limits_block_before_next_call(self):
        task = runner(config=RunConfig(max_calls=2))
        with self.assertRaises(BudgetExceeded):
            task.run()
        self.assertEqual(len(task.model.requests), 2)
        self.assertEqual(task.world.trajectory(), [])
        task = runner(config=RunConfig(max_input_bytes=100))
        with self.assertRaises(BudgetExceeded):
            task.run()
        self.assertEqual(len(task.model.requests), 0)
        self.assertEqual(task.world.trajectory(), [])

    def test_response_limit_and_retry_cap_are_enforced(self):
        task = runner(config=RunConfig(max_response_bytes=3, max_attempts=2))
        with self.assertRaises(DecisionFailed):
            task.run()
        self.assertEqual(len(task.calls), 2)
        self.assertEqual(task.summary["invalid_outputs"], 2)
        self.assertEqual(task.world.trajectory(), [])

    def test_live_adapter_and_overwriting_and_rerunning_blocked(self):
        model = MockModel()
        model.mode = "live"
        with self.assertRaises(DecisionFailed):
            runner(model)
        self.assertEqual(model.requests, [])
        task = runner()
        task.run()
        with self.assertRaises(DecisionFailed):
            task.run()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileExistsError):
                runner(output=folder)


if __name__ == "__main__":
    unittest.main()
