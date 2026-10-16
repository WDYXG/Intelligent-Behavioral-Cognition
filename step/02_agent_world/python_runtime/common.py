"""Shared IO, fail-closed configuration and append-only audit logging (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STEP = Path(__file__).resolve().parents[2]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.name}, line {exc.lineno}, column {exc.colno}") from None


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def legacy_digest(value: Any) -> str:
    """Validate the integer-only JSON subset used by existing TS states before hashing.

    No general ECMAScript float serialization claim is made. Future fractional-number
    initializations require an explicit canonicalization adapter, not silent acceptance.
    """
    def check(obj: Any) -> None:
        if isinstance(obj, bool) or obj is None or isinstance(obj, str):
            return
        if isinstance(obj, int):
            require(abs(obj) <= 9007199254740991, "Unsafe legacy integer")
        elif isinstance(obj, list):
            for item in obj:
                check(item)
        elif isinstance(obj, dict):
            require(all(isinstance(k, str) and k.isascii() for k in obj), "Legacy non-ASCII key requires canonicalization adapter")
            for item in obj.values():
                check(item)
        else:
            raise ValueError("Legacy non-integer JSON number requires canonicalization adapter")
    check(value)
    return digest(value)


def file_hash(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    """New artifacts only; never overwrite an existing state or result."""
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def artifact_index(directory: Path) -> dict:
    return {p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
            for p in sorted(directory.iterdir()) if p.is_file()}


class EventLog:
    """Single writer lock covers concurrent API call logs; hashes use python_json_v1."""
    def __init__(self, path: Path):
        self.path = path
        self._handle = path.open("x", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        self.sequence = 0
        self.last_hash = None

    def add(self, kind: str, payload: dict) -> None:
        with self._lock:
            event = {"seq": self.sequence + 1, "time_utc": utc_now(), "type": kind,
                     "payload": payload, "previous_hash": self.last_hash}
            event["hash"] = digest(event)
            self._handle.write(canonical(event).decode("utf-8") + "\n")
            self._handle.flush()
            self.sequence += 1
            self.last_hash = event["hash"]

    def close(self) -> None:
        self._handle.close()


def validate_config(c: dict) -> None:
    from context_builder import validate_context_config
    validate_context_config(c)
    require(c.get("schema_version") in ("community_python_v1", "social_framework_v1"), "Unsupported config schema")
    require(c.get("learning_enabled") is False, "M consolidation is not implemented")
    require(c.get("backend") == "llm", "Python community requires llm; no offline fallback")
    from scene_adapter import load_scene_class
    scene_class = load_scene_class(c)
    scene_class.validate_config(c)
    phases = scene_class.phase_names(c)
    require(isinstance(phases, list) and phases and all(isinstance(p, str) and p for p in phases)
            and len(set(phases)) == len(phases), "Scene must declare unique phases")
    require(c["population"]["expected_count"] == 12, "This community requires all 12 existing Personas")
    require(c["reuse_authorization"]["policy"] == "existing_decision_view_no_expansion", "Unsupported Persona authorization")
    require(set(c["mechanisms"]) == {"x_update", "consolidator", "norm_inference"}
            and c["mechanisms"].get("x_update") in ("delivered_community_observation_v1", "observation_cognition_intents_v1")
            and c["mechanisms"].get("consolidator") == "disabled" and c["mechanisms"].get("norm_inference") == "disabled", "Unsupported mechanism configuration")
    if c.get("schema_version") == "social_framework_v1":
        from cognition import validate_cognition_config
        require(c["mechanisms"]["x_update"] == "observation_cognition_intents_v1", "Framework requires cognitive protocol")
        require(isinstance(c.get("scene"), dict) and "adapter" in c["scene"], "Explicit scene.adapter required")
        validate_cognition_config(c["cognition"])
    for name in ("rounds", "repetitions"):
        require(type(c[name]) is int and c[name] > 0, f"Invalid {name}")
    require(type(c["seed"]) is int and 0 <= c["seed"] <= 0xffffffff, "Invalid seed")
    for name in ("memory_window", "public_history_window"):
        require(type(c["observation"][name]) is int and c["observation"][name] >= 0, f"Invalid {name}")
    api = c["api"]
    required_api_fields = {"endpoint_env", "model_env", "key_env", "allow_remote_persona", "concurrency", "timeout_seconds", "max_retries", "backoff_seconds", "max_input_tokens", "max_output_tokens", "max_response_bytes", "temperature", "send_seed", "response_format", "input_usd_per_million", "output_usd_per_million"}
    require(required_api_fields <= set(api) <= required_api_fields | {"thinking"}, "Unsupported/missing API config fields; secrets belong only in environment variables")
    # Omission preserves legacy/provider-default behavior; explicit values are sent unchanged.
    if "thinking" in api:
        thinking = api["thinking"]
        require(isinstance(thinking, dict) and set(thinking) == {"type"}
                and thinking["type"] in ("enabled", "disabled"),
                'api.thinking must be {"type": "enabled"} or {"type": "disabled"}; omit it for provider defaults')
    for key in ("endpoint_env", "model_env", "key_env"):
        require(isinstance(api[key], str) and api[key].isascii() and api[key].isidentifier(), f"api.{key} must be an environment variable name, not a URL/model/key value")
    for key in ("concurrency", "timeout_seconds", "max_input_tokens", "max_output_tokens", "max_response_bytes"):
        require(type(api[key]) is int and api[key] > 0, f"Invalid api.{key}")
    require(type(api["max_retries"]) is int and 0 <= api["max_retries"] <= 3, "max_retries must be 0..3")
    require(type(api["backoff_seconds"]) in (int, float) and 0 <= api["backoff_seconds"] <= 60, "Invalid retry backoff")
    require(api["response_format"] in (None, "json_object"), "Unsupported response format")
    require(type(api["temperature"]) in (int, float) and math.isfinite(api["temperature"]) and 0 <= api["temperature"] <= 2, "Invalid temperature")
    require(type(api["allow_remote_persona"]) is bool and type(api["send_seed"]) is bool, "Invalid API flags")
    for name in ("max_calls", "max_tokens"):
        require(type(c["budget"][name]) is int and c["budget"][name] > 0, f"Invalid budget.{name}")
    # Price, USD cap, consent and environment are checked together before any request.


def sanitized_error(exc: BaseException) -> dict:
    """Do not dump urllib exception strings, URLs, request headers or environment values."""
    return {"class": type(exc).__name__, "message": str(exc) if isinstance(exc, ValueError) else "See failure stage and API error events; sensitive exception details omitted"}
