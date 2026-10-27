"""Local transport contract, context guards and real-model execution boundaries."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from public_goods.agents.agent_bc import BRunner
from public_goods.agents.runner import DecisionFailed, RunConfig
from public_goods.environment.world import World
from public_goods.experiments.ollama_trial import plan, run_trial, smoke_world
from public_goods.models.client import MockModel, ModelRequest
from public_goods.models.ollama import OllamaClient, OllamaError, visible_evidence_paths


def fake_metadata(path, payload=None):
    if path == "/api/version":
        return {"version": "test-version"}
    if path == "/api/tags":
        return {"models": [{"name": "qwen3.5:9b", "digest": "pinned-test-digest"}]}
    if path == "/api/show":
        return {"capabilities": ["completion", "thinking"], "thinking": {"values": [False, True]},
                "model_info": {"test.context_length": 262144}, "details": {"quantization_level": "test"}}
    raise AssertionError("Unexpected inference in metadata-only operation")


def prepared_client(**kwargs):
    client = OllamaClient(**kwargs)
    with patch.object(client, "_http_json", side_effect=fake_metadata):
        client.inspect()
    return client


def answer(**overrides):
    result = {"model": "qwen3.5:9b", "done": True, "done_reason": "stop",
              "message": {"content": '{"contribution":10}'}, "prompt_eval_count": 100, "eval_count": 7}
    result.update(overrides)
    return result


class FakeLocalModel(MockModel):
    mode = "live"
    model_id = "test-local-fixture"
    num_ctx = 32768

    def __init__(self):
        super().__init__()
        self.sent_calls = 0
        self.inspections = 0

    def inspect(self):
        self.inspections += 1
        return {"test_fixture": True}

    def complete(self, request):
        self.sent_calls += 1
        return super().complete(request)


class OllamaTests(unittest.TestCase):
    def test_visible_paths_are_real_pointers_not_derived_view_aliases(self):
        from public_goods.agents.cognition import evidence_exists
        observation = {"stage": "contribution", "a/b": {"~key": [None, 7]}, "empty": {}}
        paths = visible_evidence_paths(observation)
        self.assertIn("/a~1b/~0key/1", paths)
        self.assertIn("/empty", paths)
        self.assertTrue(all(evidence_exists(observation, p) for p in paths))
        mock = MockModel()
        task = BRunner(World("visible", ("a", "b", "c", "d")), mock)
        view = task.world.observe("a")
        task._decide("a", view)
        client = prepared_client(structured_output=True, evidence_paths="visible")
        schema = client.build_payload(mock.requests[0])["format"]
        allowed = schema["$defs"]["evidence_path"]["enum"]
        self.assertEqual(allowed, visible_evidence_paths(view))
        self.assertNotIn("/current_stage/contributions/status", allowed)
        self.assertNotIn("/capability_view", allowed)
        self.assertTrue(all(evidence_exists(view, p) for p in allowed))
        self.assertEqual(schema["properties"]["facts"]["anyOf"][0]["properties"]["evidence"]["items"],
                         {"$ref": "#/$defs/evidence_path"})
        task._pending.clear()
        for updater in task.updaters.values():
            updater.discard()

    def test_late_smoke_history_and_current_action_stay_distinct(self):
        from public_goods.agents.capabilities import build_capability_view
        world = smoke_world("late", 1, "late-punishment")
        self.assertEqual((world.phase, world.round, world.stage), ("P", 10, "punishment"))
        view = world.observe("member-a")
        facts = build_capability_view(view)["verified_context"]
        self.assertEqual(facts["contribution_history"]["conditions"]["N"]["group_value_counts"],
                         [{"value": 8, "count": 1}, {"value": 10, "count": 39}])
        self.assertEqual(facts["contribution_history"]["conditions"]["P"]["observed_rounds"], 9)
        self.assertEqual(World.replay(world.audit_log()).observe("member-a"), view)
        model = FakeLocalModel()
        with tempfile.TemporaryDirectory() as temp:
            result = run_trial(Path(temp)/"late", model, allow_live=True, smoke_case="late-punishment")
            self.assertTrue(all(row["action"] == {"points": [0, 0, 0]} for row in result["arms"].values()))
            self.assertEqual(result["real_model_calls"], 5)
        with self.assertRaises(ValueError):
            plan("full", smoke_case="late-punishment")

    def test_optional_schema_uses_same_contract_without_fixing_actions(self):
        client = prepared_client(structured_output=True)
        world = World("schema", ("a", "b", "c", "d"))
        mock = MockModel()
        task = BRunner(world, mock)
        task._decide("a", world.observe("a"))
        judgment_format = client.build_payload(mock.requests[0])["format"]
        self.assertEqual(set(judgment_format["properties"]), {"facts", "behavior_expectation", "norm_recognition", "normative_expectation", "personal_norm"})
        self.assertFalse(judgment_format["additionalProperties"])
        stated = judgment_format["properties"]["facts"]["anyOf"][1]
        self.assertEqual(stated["properties"]["evidence"]["minItems"], 1)
        decision_format = client.build_payload(mock.requests[1])["format"]
        self.assertEqual(decision_format["properties"]["contribution"], {"type": "integer", "minimum": 0, "maximum": 20})
        self.assertFalse(decision_format["additionalProperties"])
        task._pending.clear()
        for updater in task.updaters.values():
            updater.discard()

    def test_explicit_local_json_nonthinking_payload_and_unchanged_messages(self):
        client = prepared_client()
        messages = [{"role": "system", "content": "只输出 JSON。"}, {"role": "user", "content": "贡献？"}]
        request = ModelRequest(messages, 256, 0.7, 123)
        original = deepcopy(messages)
        payload = client.build_payload(request)
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["options"], {"num_ctx": 32768, "num_predict": 256, "temperature": 0.7})
        self.assertEqual((payload["format"], payload["think"], payload["stream"]), ("json", False, False))
        self.assertNotIn("seed", payload["options"])
        payload["messages"][0]["content"] = "changed"
        self.assertEqual(messages, original)
        with patch.object(client, "_http_json", return_value=answer()):
            response = client.complete(request)
        self.assertEqual((response.input_tokens, response.output_tokens, response.finish_reason), (100, 7, "stop"))
        self.assertIn("pinned-test-digest", response.system_fingerprint)
        self.assertIsNone(response.cost_cny_upper)

    def test_oversize_history_is_rejected_before_sending_not_truncated(self):
        client = prepared_client(num_ctx=4096)
        request = ModelRequest([{"role": "user", "content": "历史" * 500}], 256, 0.7, 1)
        with patch.object(client, "_http_json") as transport:
            with self.assertRaisesRegex(OllamaError, "not truncated"):
                client.complete(request)
        transport.assert_not_called()
        self.assertEqual(client.sent_calls, 0)

    def test_model_pin_and_thinking_support_are_checked_without_inference(self):
        client = prepared_client()
        def changed(path, payload=None):
            result = fake_metadata(path, payload)
            if path == "/api/tags":
                result["models"][0]["digest"] = "changed-digest"
            return result
        with patch.object(client, "_http_json", side_effect=changed), self.assertRaisesRegex(OllamaError, "changed"):
            client.inspect()
        def thinking_only(path, payload=None):
            result = fake_metadata(path, payload)
            if path == "/api/show":
                result["thinking"]["values"] = [True]
            return result
        with patch.object(client, "_http_json", side_effect=thinking_only), self.assertRaisesRegex(OllamaError, "non-thinking"):
            client.inspect()

    def test_bad_server_responses_do_not_become_valid_actions(self):
        client = prepared_client()
        request = ModelRequest([{"role": "user", "content": "Return JSON."}], 256, 0.7, 1)
        for result in (answer(model="another-model"), answer(done=False), answer(prompt_eval_count=True),
                       answer(eval_count=-1), answer(message={"thinking": "unexpected", "content": "{}"}),
                       answer(prompt_eval_count=32700)):
            with self.subTest(result=result), patch.object(client, "_http_json", return_value=result):
                with self.assertRaises(OllamaError):
                    client.complete(request)

    def test_truncated_json_is_rejected_and_original_responses_retained(self):
        client = prepared_client()
        task = BRunner(World("length", ("a", "b", "c", "d")), client, RunConfig(allow_live=True, max_calls=6))
        with patch.object(client, "_http_json", return_value=answer(done_reason="length")):
            with self.assertRaises(DecisionFailed):
                task._decide("a", task.world.observe("a"))
        self.assertEqual(len(task.calls), 3)
        self.assertTrue(all(row["status"] == "invalid_output" and row["response"]["finish_reason"] == "length"
                            for row in task.calls))
        self.assertEqual(task.world.stage, "contribution")
        self.assertIsNone(task.states["a"].snapshot()["X"])

    def test_plan_is_five_smoke_calls_and_requires_explicit_execution(self):
        self.assertEqual(plan()["calls_without_retries"], 5)
        self.assertEqual(plan("full")["calls_without_retries"], 600)
        model = FakeLocalModel()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "blocked"
            with self.assertRaises(ValueError):
                run_trial(output, model)
            self.assertFalse(output.exists())
            self.assertEqual(model.inspections, 0)
            result = run_trial(Path(temp) / "smoke", model, allow_live=True)
            self.assertEqual(result["real_model_calls"], 5)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(all(not arm["action_executed"] and not arm["state_committed"] for arm in result["arms"].values()))

    def test_url_and_generation_options_are_validated_before_requests(self):
        for url in ("https://example.com", "http://localhost:11434/other", "http://user:pass@localhost:11434"):
            with self.assertRaises(ValueError):
                OllamaClient(base_url=url)
        for ctx in (True, 1024, 32768.0):
            with self.assertRaises(ValueError):
                OllamaClient(num_ctx=ctx)
        client = OllamaClient()
        with self.assertRaisesRegex(OllamaError, "pin"):
            client.build_payload(ModelRequest([{"role": "user", "content": "test"}], 256, 0.7, 1))
