"""DeepSeek adapter tests use injected responses; no external requests are made."""

from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from public_goods.agents.agent_a import AgentRunner
from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.runner import DecisionFailed, RunConfig
from public_goods.environment.world import World
from public_goods.models.client import MockModel, ModelRequest
from public_goods.models.deepseek import (BudgetLimitError, CNYBudget, DeepSeekClient,
                                         ConnectionNotSent, MAX_CONTEXT, ProviderError, cost_upper, load_key)


def request():
    return ModelRequest([{"role": "system", "content": "Return JSON."},
                         {"role": "user", "content": "Choose an action."}], 256, 0.7, 123)


def response(text='{"contribution": 10}', finish="stop"):
    return {"id": "offline-provider-fixture", "model": "deepseek-flash", "system_fingerprint": "fixture-fp",
            "choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 600,
                      "completion_tokens": 50, "total_tokens": 1050}}


class BudgetTests(unittest.TestCase):
    def test_peak_cost_includes_cache_and_output_in_exact_CNY(self):
        self.assertEqual(cost_upper(1000, 50, 400), Decimal("0.001616"))
        self.assertEqual(cost_upper(MAX_CONTEXT, 1024), Decimal("2.105344"))
        for values in ((True, 1), (1, -1), (1, 1, 2), (1.0, 1)):
            with self.assertRaises(ValueError):
                cost_upper(*values)

    def test_reopening_keeps_spent_budget_and_cannot_silently_change_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path)
            for _ in range(4):
                budget.reserve(256)
                budget.settle(Decimal("2"))
            with self.assertRaises(BudgetLimitError):
                budget.reserve(256)
            budget.close()
            budget = CNYBudget(path)
            self.assertEqual(budget.spent, Decimal(8))
            with self.assertRaises(BudgetLimitError):
                budget.reserve(256)
            budget.close()
            with self.assertRaises(BudgetLimitError):
                CNYBudget(path, "11")
            with self.assertRaises(BudgetLimitError):
                CNYBudget(path, "9")

    def test_explicit_limit_change_preserves_costs_and_old_bytes_across_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path, "10")
            budget.reserve(256)
            budget.settle(Decimal("2"))
            budget.reserve(256)
            budget.quarantine_unresolved("Unresolved historical request; retain its full cost")
            original = path.read_bytes()
            unknown = budget.uncertain
            budget.set_limit("30", "User authorized a larger bounded pilot")
            self.assertTrue(path.read_bytes().startswith(original))
            self.assertEqual(budget.spent, Decimal("2"))
            self.assertEqual(budget.uncertain, unknown)
            self.assertEqual(budget.limit, Decimal("30"))
            self.assertEqual(json.loads(path.read_text().splitlines()[-1]), {
                "kind": "limit_changed", "previous_limit_cny": "10", "limit_cny": "30",
                "reason": "User authorized a larger bounded pilot"})
            budget.reserve(1024)
            budget.settle(Decimal("0.1"))
            expected = budget.snapshot()
            budget.close()
            for limit in (None, "30", Decimal("30.0")):
                reopened = CNYBudget(path, limit)
                try:
                    self.assertEqual(reopened.snapshot(), expected)
                    self.assertEqual(reopened.uncertain, unknown)
                finally:
                    reopened.close()
            before_mismatch = path.read_bytes()
            with self.assertRaises(BudgetLimitError):
                CNYBudget(path, "10")
            self.assertEqual(path.read_bytes(), before_mismatch)

    def test_limit_change_requires_reason_and_no_active_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path)
            try:
                before = path.read_bytes()
                for reason in (None, "", "  ", 123):
                    with self.assertRaises(BudgetLimitError):
                        budget.set_limit("30", reason)
                for limit in ("NaN", "Infinity", "-1", "0", "bad", True):
                    with self.assertRaises(ValueError):
                        budget.set_limit(limit, "A reason")
                self.assertEqual(path.read_bytes(), before)
                budget.reserve(256)
                before = path.read_bytes()
                with self.assertRaises(BudgetLimitError):
                    budget.set_limit("30", "User authorized more budget")
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(budget.limit, Decimal("10"))
            finally:
                budget.close()

    def test_limit_cannot_drop_below_spent_plus_unknown_and_replay_checks_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path)
            budget.reserve(256)
            budget.settle(Decimal("2"))
            budget.reserve(256)
            budget.quarantine_unresolved("Keep historical unknown usage")
            try:
                before = path.read_bytes()
                with self.assertRaises(BudgetLimitError):
                    budget.set_limit("4", "Reduce budget")
                self.assertEqual(path.read_bytes(), before)
                occupied = budget.spent + budget.uncertain
                budget.set_limit(occupied, "Cap at the amount already occupied")
                with self.assertRaises(BudgetLimitError):
                    budget.reserve(256)
            finally:
                budget.close()
            reopened = CNYBudget(path)
            self.assertEqual(reopened.limit, occupied)
            reopened.close()
            # A forged adjustment must not bypass the chronological prior limit.
            with path.open("a") as stream:
                stream.write(json.dumps({"kind": "limit_changed", "previous_limit_cny": "10",
                                         "limit_cny": "30", "reason": "Mismatched prior limit"}) + "\n")
            with self.assertRaises(BudgetLimitError):
                CNYBudget(path)

    def test_new_budget_accepts_an_explicit_finite_positive_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path, "30")
            self.assertEqual(budget.limit, Decimal("30"))
            budget.close()
            reopened = CNYBudget(path)
            self.assertEqual(reopened.limit, Decimal("30"))
            reopened.close()

    def test_unfinished_request_stays_reserved_across_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path)
            budget.reserve(1024)
            held = budget.held
            budget.close()
            budget = CNYBudget(path)
            self.assertEqual(budget.held, held)
            with self.assertRaises(BudgetLimitError):
                budget.reserve(256)
            budget.close()

    def test_two_process_handles_cannot_spend_the_same_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path)
            try:
                with self.assertRaises(BlockingIOError):
                    CNYBudget(path)
            finally:
                budget.close()

    def test_manual_recovery_retains_unknown_cost_and_enforces_total_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.jsonl"
            budget = CNYBudget(path)
            budget.reserve(256)
            held = budget.held
            budget.quarantine_unresolved("Prior transport failure; keep full unknown upper bound")
            budget.close()
            budget = CNYBudget(path)
            try:
                self.assertEqual(budget.uncertain, held)
                self.assertEqual(budget.snapshot()["unresolved_reservation_cny"], str(held))
                for _ in range(3):
                    budget.reserve(256)
                    budget.settle(Decimal(2))
                with self.assertRaises(BudgetLimitError):
                    budget.reserve(256)
                self.assertEqual(budget.spent + budget.uncertain, Decimal("8.0992"))
            finally:
                budget.close()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.budget = CNYBudget(Path(self.directory.name) / "budget.jsonl")

    def tearDown(self):
        self.budget.close()
        self.directory.cleanup()

    def test_payload_disables_thinking_uses_json_and_does_not_send_seed_or_key(self):
        seen = []
        def transport(payload):
            self.assertGreater(self.budget.held, 0)  # Reservation must precede network dispatch.
            seen.append(payload)
            return response()
        client = DeepSeekClient("test-secret", self.budget, transport=transport)
        result = client.complete(request())
        self.assertEqual(seen[0]["thinking"], {"type": "disabled"})
        self.assertEqual(seen[0]["response_format"], {"type": "json_object"})
        self.assertEqual(seen[0]["temperature"], .7)
        self.assertEqual(seen[0]["max_tokens"], 256)
        self.assertNotIn("seed", seen[0])
        self.assertNotIn("test-secret", json.dumps(seen))
        self.assertEqual(result.cost_cny_upper, "0.001616")
        self.assertEqual(result.cached_input_tokens, 400)
        self.assertEqual(result.system_fingerprint, "fixture-fp")
        self.assertEqual(self.budget.held, 0)
        self.assertEqual(client.sent_calls, 1)

    def test_network_failure_is_not_retried_and_does_not_leak_exception_details(self):
        def failing(payload):
            raise TimeoutError("Authorization: Bearer test-secret")
        client = DeepSeekClient("test-secret", self.budget, transport=failing)
        with self.assertRaises(ProviderError) as caught:
            client.complete(request())
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertEqual(client.sent_calls, 1)
        self.assertGreater(self.budget.held, 0)
        with self.assertRaises(BudgetLimitError):
            client.complete(request())
        self.assertEqual(client.sent_calls, 1)

    def test_tls_failure_before_http_send_has_zero_cost_and_no_credential_sent(self):
        client = DeepSeekClient("test-secret", self.budget)
        with patch("public_goods.models.deepseek.http.client.HTTPSConnection") as connection:
            connection.return_value.connect.side_effect = OSError("test-secret")
            with self.assertRaises(ConnectionNotSent) as caught:
                client.complete(request())
            self.assertNotIn("test-secret", str(caught.exception))
            connection.return_value.request.assert_not_called()
            self.assertEqual(self.budget.held, 0)
            self.assertEqual(self.budget.spent, 0)

    def test_missing_or_inconsistent_usage_halts_without_releasing_reservation(self):
        bad = response()
        bad["usage"]["prompt_cache_miss_tokens"] = 599
        client = DeepSeekClient("test-secret", self.budget, transport=lambda p: bad)
        with self.assertRaises(ProviderError):
            client.complete(request())
        self.assertGreater(self.budget.held, 0)
        self.assertEqual(self.budget.spent, 0)

    def test_budget_exhaustion_happens_before_dispatch(self):
        for _ in range(4):
            self.budget.reserve(256)
            self.budget.settle(Decimal(2))
        def must_not_send(payload):
            self.fail("Budget guard allowed network dispatch")
        client = DeepSeekClient("test-secret", self.budget, transport=must_not_send)
        with self.assertRaises(BudgetLimitError):
            client.complete(request())
        self.assertEqual(client.sent_calls, 0)

    def test_http_endpoint_and_headers_are_fixed_and_errors_are_not_followed(self):
        client = DeepSeekClient("test-secret", self.budget)
        with patch("public_goods.models.deepseek.http.client.HTTPSConnection") as connection:
            connection.return_value.getresponse.return_value.status = 302
            with self.assertRaisesRegex(ProviderError, "HTTP 302"):
                client.complete(request())
            connection.assert_called_once_with("api.deepseek.com", timeout=30)
            args, kwargs = connection.return_value.request.call_args
            self.assertEqual(args[:2], ("POST", "/chat/completions"))
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-secret")
            connection.return_value.close.assert_called_once()

    def test_http_success_parses_chunked_reads_and_records_usage(self):
        client = DeepSeekClient("test-secret", self.budget)
        raw = json.dumps(response()).encode()
        with patch("public_goods.models.deepseek.http.client.HTTPSConnection") as connection:
            http_response = connection.return_value.getresponse.return_value
            http_response.status = 200
            http_response.read1.side_effect = [raw[:40], raw[40:], b""]
            result = client.complete(request())
            self.assertEqual(result.input_tokens, 1000)
            self.assertEqual(result.cost_cny_upper, "0.001616")
            self.assertEqual(http_response.read1.call_count, 3)
            connection.return_value.close.assert_called_once()

    def test_invalid_http_json_is_not_logged_or_retried(self):
        client = DeepSeekClient("test-secret", self.budget)
        with patch("public_goods.models.deepseek.http.client.HTTPSConnection") as connection:
            http_response = connection.return_value.getresponse.return_value
            http_response.status = 200
            http_response.read1.side_effect = [b"test-secret: invalid response", b""]
            with self.assertRaises(ProviderError) as error:
                client.complete(request())
            self.assertNotIn("test-secret", str(error.exception))
            self.assertEqual(client.sent_calls, 1)
            self.assertGreater(self.budget.held, 0)

    def test_local_key_loading_and_default_plan_make_no_requests(self):
        path = Path(self.directory.name) / ".env"
        path.write_text('DEEPSEEK_API_KEY="local-test-secret"\n')
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(load_key(path), "local-test-secret")
            path.write_text("DEEPSEEK_API_KEY=\n")
            with self.assertRaises(ProviderError):
                load_key(path)
        from public_goods.run import main
        with patch("public_goods.models.deepseek.http.client.HTTPSConnection") as connection, patch("sys.stdout", new_callable=StringIO) as output:
            main(["deepseek-trial"])
            plan = json.loads(output.getvalue())
            self.assertEqual(plan["budget_limit_cny"], "10")
            self.assertEqual(plan["trial"]["totals"]["calls_without_retries"], 600)
            connection.assert_not_called()

    def test_partial_arm_plan_keeps_same_ledger_and_adjusts_totals_without_calls(self):
        from public_goods.run import main
        with patch("public_goods.models.deepseek.http.client.HTTPSConnection") as connection, patch("sys.stdout", new_callable=StringIO) as output:
            main(["deepseek-trial", "--arms", "B", "C"])
            plan = json.loads(output.getvalue())
            self.assertEqual(set(plan["trial"]["arms"]), {"B", "C"})
            self.assertEqual(plan["trial"]["totals"]["calls_without_retries"], 480)
            self.assertEqual(plan["trial"]["total_groups"], 2)
            self.assertEqual(Decimal(plan["output_only_peak_cap_cny_without_retries"]), Decimal("2.4576"))
            self.assertTrue(plan["ledger"].endswith("deepseek_budget_20261020.jsonl"))
            connection.assert_not_called()

    def test_authorized_budget_change_preview_is_read_only_and_requires_reason(self):
        from public_goods.run import main
        with patch("public_goods.models.deepseek.http.client.HTTPSConnection") as connection, patch("sys.stdout", new_callable=StringIO) as output:
            main(["deepseek-trial", "--budget-limit", "30", "--budget-reason", "User expanded the pilot budget"])
            plan = json.loads(output.getvalue())
            self.assertEqual(plan["budget_limit_cny"], "30")
            self.assertEqual(plan["requested_budget_change"]["reason"], "User expanded the pilot budget")
            self.assertEqual(plan["trial"]["totals"]["calls_without_retries"], 600)
            connection.assert_not_called()
        for arguments in (["--budget-limit", "30"], ["--budget-reason", "reason"],
                          ["--budget-limit", "nan", "--budget-reason", "reason"],
                          ["--budget-limit", "-1", "--budget-reason", "reason"]):
            with patch("sys.stderr", new_callable=StringIO), self.assertRaises(SystemExit):
                main(["deepseek-trial", *arguments])


class RunnerAdapterTests(unittest.TestCase):
    def test_all_three_arms_share_adapter_budget_and_record_usage_without_network(self):
        fixture = MockModel()
        def transport(payload):
            synthetic = ModelRequest(payload["messages"], payload["max_tokens"], payload["temperature"], 0)
            return response(fixture.complete(synthetic).text)
        with tempfile.TemporaryDirectory() as folder:
            budget = CNYBudget(Path(folder) / "budget.jsonl")
            client = DeepSeekClient("test-secret", budget, transport=transport)
            try:
                for name, cls, count in (("A", AgentRunner, 120), ("B", BRunner, 240), ("C", CRunner, 240)):
                    world = World("adapter-fixture", ["a", "b", "c", "d"])
                    config = RunConfig(max_calls=count * 3, allow_live=True)
                    output = Path(folder) / name
                    task = cls(world, client, config, output)
                    result = task.run()
                    self.assertEqual(result["real_api_calls"], count)  # Fake transport dispatch count in this test only.
                    self.assertEqual(result["simulated_calls"], 0)
                    self.assertEqual(result["measured_model_tokens"], count * 1050)
                    self.assertEqual(Decimal(result["model_cost_cny_upper"]), Decimal("0.001616") * count)
                    self.assertTrue(result["replay_verified"])
                    self.assertNotIn("test-secret", (output / "calls.jsonl").read_text())
                    self.assertNotIn("test-secret", (output / "config.json").read_text())
                    self.assertNotIn("seed", task.calls[0]["provider_request"])
                self.assertEqual(client.sent_calls, 600)
                self.assertEqual(budget.spent, Decimal("0.969600"))
            finally:
                budget.close()

    def test_truncated_json_is_retried_and_all_attempts_remain_billable(self):
        fixture = MockModel()
        seen = []
        def transport(payload):
            seen.append(payload)
            if len(seen) == 1:
                return response('{"contribution": 10}', finish="length")
            synthetic = ModelRequest(payload["messages"], payload["max_tokens"], .7, 0)
            return response(fixture.complete(synthetic).text)
        with tempfile.TemporaryDirectory() as folder:
            budget = CNYBudget(Path(folder) / "budget.jsonl")
            client = DeepSeekClient("test-secret", budget, transport=transport)
            try:
                task = AgentRunner(World("length", ["a", "b", "c", "d"]), client, RunConfig(allow_live=True))
                result = task.run()
                self.assertEqual(result["invalid_outputs"], 1)
                self.assertEqual(result["real_api_calls"], 121)
                self.assertEqual(budget.spent, Decimal("0.001616") * 121)
                self.assertEqual(task.calls[0]["response"]["finish_reason"], "length")
            finally:
                budget.close()

    def test_live_mode_still_requires_explicit_runner_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            budget = CNYBudget(Path(folder) / "budget.jsonl")
            try:
                client = DeepSeekClient("test-secret", budget, transport=lambda p: response())
                with self.assertRaises(DecisionFailed):
                    AgentRunner(World("guard", ["a", "b", "c", "d"]), client)
                self.assertEqual(client.sent_calls, 0)
            finally:
                budget.close()


if __name__ == "__main__":
    unittest.main()
