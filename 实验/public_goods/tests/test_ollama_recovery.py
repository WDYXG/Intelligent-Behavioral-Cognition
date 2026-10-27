"""Repair exhausted citations without resampling accepted prefix actions."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from public_goods import artifacts

from public_goods.agents.runner import DecisionFailed
from public_goods.evaluation.preflight import audit_saved_run
from public_goods.experiments.memory_diagnostic import rows
from public_goods.experiments.ollama_trial import run_trial, preview_resume, resume_trial
from public_goods.experiments.recover_trial import recover, OllamaPrefixReplay
from public_goods.models.client import MockModel, ModelResponse
from public_goods.models.ollama import OllamaClient
from public_goods.tests.test_ollama import fake_metadata


class FixtureOllama(OllamaClient):
    """No HTTP: valid fixture outputs, with one deliberately exhausted citation."""
    def __init__(self, *args, fail=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail = fail
        self.target_hits = 0

    def inspect(self):
        with patch.object(self, "_http_json", side_effect=fake_metadata):
            return super().inspect()

    def complete(self, request):
        self.sent_calls += 1
        payload = json.loads(request.messages[1]["content"])
        view = payload.get("observation", payload)
        response = MockModel().complete(request)
        if self.fail and payload.get("step") == "judgment" and view["phase"] == "N" and view["round"] == 2:
            self.target_hits += 1
            if self.target_hits >= 3:
                judgment = json.loads(response.text)
                judgment["facts"]["evidence"] = ["/current_stage/contributions/status"]
                response = ModelResponse(json.dumps(judgment, ensure_ascii=False))
        return response


class OllamaRecoveryTests(unittest.TestCase):
    def test_failed_compact_run_can_be_resumed_and_compacted_again(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source", Path(folder)/"repair"
            with self.assertRaises(DecisionFailed):
                run_trial(source, FixtureOllama(structured_output=True, fail=True), scope="full", allow_live=True)
            before = artifacts.read_bytes(source/"A/calls.jsonl")
            artifacts.compact_run(source)
            self.assertEqual(preview_resume(source)["arms"]["B"]["operation"], "repair_failed")
            with patch('public_goods.experiments.ollama_trial.OllamaClient', FixtureOllama):
                resume_trial(source, output)
            artifacts.compact_run(output)
            self.assertEqual(artifacts.read_bytes(output/"A/calls.jsonl"), before)
            for arm in 'ABC':
                self.assertTrue(audit_saved_run(output/arm)['passed'])

    def test_restart_preserves_all_accepted_calls_and_terminal_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source", Path(folder)/"repair"
            model = FixtureOllama(num_ctx=65536, structured_output=True, fail=True)
            with self.assertRaises(DecisionFailed):
                run_trial(source, model, scope="full", allow_live=True)
            original = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in source.rglob('*') if p.is_file()}
            preview = preview_resume(source)
            self.assertEqual(preview["arms"]["A"]["operation"], "reuse_completed")
            self.assertEqual(preview["arms"]["B"]["prefix_audit"]["pending_participants"], ["member-a", "member-b"])
            self.assertEqual(preview["arms"]["C"]["operation"], "start_unattempted")
            before = rows(source/"B/calls.jsonl")
            with patch('public_goods.experiments.ollama_trial.OllamaClient', FixtureOllama):
                result = resume_trial(source, output)
            self.assertEqual(result["status"], "completed_repaired")
            self.assertFalse(result["confirmatory_comparison"])
            self.assertEqual(result["arms"]["A"]["new_real_model_calls"], 0)
            self.assertEqual((source/"A/calls.jsonl").read_bytes(), (output/"A/calls.jsonl").read_bytes())
            self.assertEqual(rows(output/"B/original_terminal_failures.jsonl"), before[-3:])
            after = rows(output/"B/calls.jsonl")
            for old, replayed in zip(before[:-3], after):
                for key in ('call_id', 'request', 'provider_request', 'response', 'status', 'action', 'judgment', 'error'):
                    self.assertEqual(old.get(key), replayed.get(key), key)
                self.assertEqual(replayed['network_requests'], 0)
            for arm in ('A', 'B', 'C'):
                self.assertTrue(audit_saved_run(output/arm)['passed'])
            self.assertEqual(original, {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in source.rglob('*') if p.is_file()})
            with self.assertRaises(FileExistsError):
                with patch('public_goods.experiments.ollama_trial.OllamaClient', FixtureOllama):
                    resume_trial(source, output)

    def test_other_failure_types_are_not_silently_restarted(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/"source"
            with self.assertRaises(DecisionFailed):
                run_trial(source, FixtureOllama(structured_output=True, fail=True), scope="full", allow_live=True)
            path = source/"B/calls.jsonl"
            calls = rows(path)
            for call in calls[-3:]:
                call['error'] = 'Provider finish_reason is length; output was not accepted'
            path.write_text(''.join(json.dumps(c, ensure_ascii=False)+'\n' for c in calls))
            with self.assertRaisesRegex(ValueError, 'limited to exhausted evidence-path'):
                recover(source/"B", replay_class=OllamaPrefixReplay)
