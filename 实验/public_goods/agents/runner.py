"""Shared execution, bounded retries and audit records for A/B/C."""

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from public_goods.environment.world import InvalidAction, World, validate_action
from public_goods.models.client import ModelClient, ModelRequest, ModelResponse, TemporaryModelError


class DecisionFailed(RuntimeError):
    pass


class BudgetExceeded(DecisionFailed):
    pass


@dataclass(frozen=True)
class RunConfig:
    seed: int = 20261019
    max_attempts: int = 3
    max_calls: int = 360
    max_input_bytes: int = 64000
    max_response_bytes: int = 8192
    max_output_tokens: int = 256
    max_judgment_tokens: int = 1024
    temperature: float = 0.7
    allow_live: bool = False

    def __post_init__(self):
        if type(self.allow_live) is not bool:
            raise ValueError("allow_live must be a boolean")
        for name in ("max_attempts", "max_calls", "max_input_bytes", "max_response_bytes", "max_output_tokens", "max_judgment_tokens"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")


def parse_json(text, max_bytes=8192):
    if type(text) is not str or len(text.encode("utf-8")) > max_bytes:
        raise InvalidAction("Response is not text or exceeds the response-size limit")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidAction(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise InvalidAction(f"Non-finite JSON number: {value}")

    try:
        action = json.loads(text, object_pairs_hook=unique_object, parse_constant=nonfinite)
    except json.JSONDecodeError as error:
        raise InvalidAction(f"Expected one JSON object: {error.msg}") from error
    return action


def parse_action(text, stage, max_bytes=8192):
    action = parse_json(text, max_bytes)
    validate_action(stage, action)
    return action


class BaseRunner:
    implementation = None
    source_files = ()

    def _config_metadata(self):
        return {}

    def _after_commit(self):
        pass

    def _decide(self, pid, observation):
        raise NotImplementedError

    def __init__(self, world: World, model: ModelClient, config=None, output=None):
        self.config = config or RunConfig()
        if model.mode not in ("mock", "live") or (model.mode == "live" and not self.config.allow_live):
            raise DecisionFailed("Live model calls require separate authorization and a reviewed adapter")
        self.world, self.model = world, model
        self.fixture_only = model.mode == "mock"
        self.calls, self.summary = [], None
        self.output = Path(output) if output is not None else None
        self._event_cursor = 0
        self._started = False
        if self.output is not None:
            self.output.mkdir(parents=True, exist_ok=False)
            record = {"implementation": self.implementation, "execution_mode": model.mode, "fixture_only": self.fixture_only,
                      "model_id": model.model_id, "settings": asdict(self.config),
                      "group_id": world.group_id, **self._config_metadata(),
                      "protocol": "Herrmann2008 fixed groups, N10 then P10",
                      "code_sha256": {
                          name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
                          for name in ("environment/world.py", "agents/runner.py", "models/client.py", *self.source_files,
                                       *getattr(model, "source_files", ()))},
                      "provider": model.metadata() if hasattr(model, "metadata") else None,
                      "budget_note": "Input bytes are an engineering capacity limit, not a tokenizer or a cost estimate. Mock responses have no measured model tokens/cost.",
                      "settings_status": "engineering trial settings; formal experiment settings are not frozen"}
            self._write_json("config.json", record)
            self._flush_events()

    def _write_json(self, name, record):
        if self.output is not None:
            (self.output / name).write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def _append(self, name, rows):
        if self.output is not None:
            with (self.output / name).open("a") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    def _flush_events(self):
        events = self.world.audit_log()
        self._append("events.jsonl", events[self._event_cursor:])
        self._event_cursor = len(events)

    def _call(self, pid, observation, payload, system_prompt, step, parse, max_tokens):
        error_message = None
        observation_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        for attempt in range(1, self.config.max_attempts + 1):
            if len(self.calls) >= self.config.max_calls:
                raise BudgetExceeded("Call budget exhausted; no replacement action was used")
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": observation_json}]
            if error_message is not None:
                messages.append({"role": "user", "content": "上一尝试未完成有效输出：" + error_message + "。请基于同一输入返回符合当前结构的 JSON。"})
            input_bytes = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
            if input_bytes > self.config.max_input_bytes:
                raise BudgetExceeded("Input capacity exceeded; observations were not silently truncated")
            # Preserve A's existing call IDs/seeds; distinguish B/C's two steps.
            step_suffix = "" if self.implementation == "A" else f":{step}"
            call_id = f"{self.world.group_id}:{pid}:{observation['phase']}:{observation['round']}:{observation['stage']}{step_suffix}:{attempt}"
            seed = int(hashlib.sha256(f"{self.config.seed}:{call_id}".encode()).hexdigest()[:8], 16)
            request = ModelRequest(messages, max_tokens, self.config.temperature, seed)
            record = {"call_id": call_id, "participant_id": pid, "phase": observation["phase"], "round": observation["round"],
                      "stage": observation["stage"], "step": step, "attempt": attempt, "mode": self.model.mode,
                      "model_id": self.model.model_id, "request": asdict(request), "input_bytes": input_bytes,
                      "response": None, "status": "pending"}
            started = time.monotonic()
            sent_before = getattr(self.model, "sent_calls", 0)
            try:
                if hasattr(self.model, "build_payload"):
                    record["provider_request"] = self.model.build_payload(request)
                response = self.model.complete(request)
                if not isinstance(response, ModelResponse):
                    raise TypeError("Model client must return ModelResponse")
                record["response"] = asdict(response)
                if response.finish_reason not in (None, "stop"):
                    raise InvalidAction(f"Provider finish_reason is {response.finish_reason}; output was not accepted")
                if step == "judgment":
                    record["normalizations"] = []
                    result = parse(response.text, normalizations=record["normalizations"])
                else:
                    result = parse(response.text)
                record.update({"status": "valid", "action" if step == "decision" else "judgment": result})
            except (InvalidAction, TemporaryModelError) as error:
                error_message = str(error)
                record.update({"status": "invalid_output" if isinstance(error, InvalidAction) else "transient_error",
                               "error": error_message})
            except Exception as error:
                record.update({"status": "fatal_error", "error": f"{type(error).__name__}: {error}"})
                raise
            finally:
                record["network_requests"] = getattr(self.model, "sent_calls", 0) - sent_before
                record["elapsed_seconds"] = time.monotonic() - started
                self.calls.append(record)
                self._append("calls.jsonl", [record])
            if record["status"] == "valid":
                return result
        raise DecisionFailed(f"No valid output after {self.config.max_attempts} attempts for {call_id}")

    def run(self):
        if self._started:
            raise DecisionFailed("A runner cannot be run twice")
        self._started = True
        status, failure = "completed", None
        try:
            while self.world.stage != "finished":
                if self.world.stage == "feedback":
                    self.world.advance()
                else:
                    # Capture every observation before making any decision call.
                    snapshots = {pid: self.world.observe(pid) for pid in self.world.participants}
                    actions = {pid: self._decide(pid, snapshots[pid]) for pid in self.world.participants}
                    if self.world.stage == "contribution":
                        self.world.resolve_contributions(actions)
                    else:
                        self.world.resolve_punishments(actions)
                    self._after_commit()
                self._flush_events()
            replayed = World.replay(self.world.audit_log())
            if replayed.trajectory() != self.world.trajectory():
                raise DecisionFailed("World replay disagrees with the scoring trajectory")
            self._append("trajectory.jsonl", self.world.trajectory())
        except Exception as error:
            status, failure = "failed", f"{type(error).__name__}: {error}"
            raise
        finally:
            self._flush_events()
            responses = [c["response"] for c in self.calls if c["network_requests"]]
            complete_usage = bool(responses) and all(r and r["input_tokens"] is not None and r["output_tokens"] is not None for r in responses)
            complete_cost = bool(responses) and all(r and r["cost_cny_upper"] is not None for r in responses)
            self.summary = {"status": status, "failure": failure, "implementation": self.implementation, "fixture_only": self.fixture_only,
                            "execution_mode": self.model.mode, "model_id": self.model.model_id,
                            "simulated_calls": len(self.calls) if self.fixture_only else 0,
                            "real_api_calls": sum(c["network_requests"] for c in self.calls),
                            "invalid_outputs": sum(c["status"] == "invalid_output" for c in self.calls),
                            "transient_errors": sum(c["status"] == "transient_error" for c in self.calls),
                            "valid_decisions": sum(c["status"] == "valid" and c["step"] == "decision" for c in self.calls),
                            "valid_judgments": sum(c["status"] == "valid" and c["step"] == "judgment" for c in self.calls),
                            "evidence_path_normalizations": sum(len(c.get("normalizations", [])) for c in self.calls),
                            "settled_person_rounds": len(self.world.trajectory()),
                            "phase": self.world.phase, "round": self.world.round, "stage": self.world.stage,
                            "measured_model_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in responses) if complete_usage else None,
                            "model_cost_usd": None,
                            "model_cost_cny_upper": str(sum((Decimal(r["cost_cny_upper"]) for r in responses), Decimal(0))) if complete_cost else None,
                            "shared_budget": self.model.budget.snapshot() if hasattr(self.model, "budget") else None,
                            "replay_verified": status == "completed"}
            self._write_json("summary.json", self.summary)
        return self.summary


