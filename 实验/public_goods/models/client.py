"""Shared model contract and deterministic offline responses for this experiment."""

from collections import deque
from dataclasses import dataclass
import json
from typing import Protocol


class TemporaryModelError(RuntimeError):
    """Only explicitly transient provider failures may be retried."""


@dataclass(frozen=True)
class ModelRequest:
    messages: list
    max_output_tokens: int
    temperature: float
    seed: int


@dataclass(frozen=True)
class ModelResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    provider_request_id: str | None = None
    cost_cny_upper: str | None = None
    cached_input_tokens: int | None = None
    returned_model: str | None = None
    system_fingerprint: str | None = None
    finish_reason: str | None = None


class ModelClient(Protocol):
    model_id: str
    mode: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class MockModel:
    """Deterministic fixtures; their behavior is never reported as LLM results."""
    model_id = "offline-fixture"
    mode = "mock"

    def __init__(self, responses=()):
        self.responses = deque(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.responses:
            response = self.responses.popleft()
            if isinstance(response, Exception):
                raise response
            return ModelResponse(response)
        payload = json.loads(request.messages[1]["content"])
        if payload.get("step") == "judgment":
            judgment = {field: {"assessment": None, "evidence": [], "uncertainty": "unknown"}
                        for field in payload["judgment_schema"]["fields"]}
            judgment["facts"] = {"assessment": "本轮贡献范围为 0–20。",
                                 "evidence": ["/rules/contribution_range"], "uncertainty": "low"}
            return ModelResponse(json.dumps(judgment, ensure_ascii=False))
        view = payload.get("observation", payload)
        action = {"contribution": 10} if view["stage"] == "contribution" else {"points": [0, 0, 0]}
        return ModelResponse(json.dumps(action))
