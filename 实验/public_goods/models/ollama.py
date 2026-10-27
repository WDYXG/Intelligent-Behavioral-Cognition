"""Real local inference through Ollama, using the shared model contract."""

from copy import deepcopy
import json
import math
from urllib import error, request
from urllib.parse import urlsplit

from public_goods.models.client import ModelResponse


class OllamaError(RuntimeError):
    pass


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OllamaError("Ollama unexpectedly redirected the local request")


def visible_evidence_paths(observation):
    """Enumerate actual raw-observation pointers, without interpreting values."""
    paths = []

    def visit(node, prefix):
        children = node.items() if type(node) is dict else enumerate(node) if type(node) is list else ()
        for key, value in children:
            token = str(key).replace("~", "~0").replace("/", "~1")
            path = prefix + "/" + token
            if len(path) <= 200:
                paths.append(path)
                visit(value, path)

    visit(observation, "")
    return sorted(paths)


def output_schema(messages, *, evidence_paths="free"):
    """Translate the existing output contract, without prescribing any choice."""
    try:
        payload = json.loads(messages[1]["content"])
        observation = payload.get("observation", payload)
        if payload.get("step") == "judgment":
            allowed_paths = visible_evidence_paths(observation) if evidence_paths == "visible" else None
            properties = {}
            for name in payload["judgment_schema"]["fields"]:
                assessment = {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 320}]}
                evidence = {"type": "array", "items": {"type": "string", "maxLength": 200}}
                if allowed_paths is not None:
                    evidence["items"] = {"$ref": "#/$defs/evidence_path"}
                    evidence["uniqueItems"] = True
                unknown = {"type": "object", "additionalProperties": False,
                    "required": ["assessment", "evidence", "uncertainty"],
                    "properties": {"assessment": assessment, "evidence": evidence,
                                   "uncertainty": {"const": "unknown"}}}
                stated = deepcopy(unknown)
                stated["properties"]["assessment"] = {"type": "string", "minLength": 1, "maxLength": 320}
                stated["properties"]["uncertainty"] = {"enum": ["low", "medium", "high"]}
                if name != "personal_norm":
                    stated["properties"]["evidence"]["minItems"] = 1
                properties[name] = {"anyOf": [unknown, stated]}
            result = {"type": "object", "additionalProperties": False,
                      "required": list(properties), "properties": properties}
            if allowed_paths is not None:
                result["$defs"] = {"evidence_path": {"type": "string", "enum": allowed_paths}}
            return result
        if observation["stage"] == "contribution":
            value = {"type": "integer", "minimum": 0, "maximum": 20}
            name = "contribution"
        elif observation["stage"] == "punishment":
            value = {"type": "array", "minItems": 3, "maxItems": 3,
                     "items": {"type": "integer", "minimum": 0, "maximum": 10}}
            name = "points"
        else:
            raise ValueError("Unknown decision stage")
        return {"type": "object", "additionalProperties": False,
                "required": [name], "properties": {name: value}}
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise OllamaError("Cannot derive the experiment output schema") from exc


class OllamaClient:
    # 'live' means an actual model, including local inference; not a mock fixture.
    mode = "live"
    source_files = ("models/ollama.py",)

    def __init__(self, model="qwen3.5:9b", base_url="http://127.0.0.1:11434", *,
                 num_ctx=32768, timeout=180, structured_output=False, evidence_paths="free"):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("This adapter requires a local HTTP Ollama address")
        if type(model) is not str or not model.strip():
            raise ValueError("An explicit installed model tag is required")
        if type(num_ctx) is not int or num_ctx < 4096:
            raise ValueError("num_ctx must be an integer of at least 4096")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(structured_output) is not bool:
            raise ValueError("structured_output must be a boolean")
        if evidence_paths not in ("free", "visible") or (evidence_paths == "visible" and not structured_output):
            raise ValueError("Visible evidence paths require structured output")
        self.model_id, self.base_url = model, base_url.rstrip("/")
        self.num_ctx, self.timeout = num_ctx, timeout
        self.structured_output = structured_output
        self.evidence_paths = evidence_paths
        self.sent_calls = 0
        self._identity = None
        # A localhost request must not be sent through an ambient HTTP proxy.
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def _http_json(self, path, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        req = request.Request(self.base_url + path, data=body,
                              headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                result = json.load(response)
        except error.HTTPError as exc:
            raise OllamaError(f"Local Ollama HTTP error {exc.code}; no automatic retry") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError("Cannot reach local Ollama or inference timed out; no automatic retry") from exc
        except (ValueError, UnicodeError) as exc:
            raise OllamaError("Local Ollama returned an invalid JSON response") from exc
        if type(result) is not dict or "error" in result:
            raise OllamaError("Local Ollama returned an error or invalid response object")
        return result

    def inspect(self):
        """Read installed metadata only; do not download or run a model."""
        version = self._http_json("/api/version").get("version")
        installed = self._http_json("/api/tags").get("models", [])
        found = [row for row in installed if row.get("name") == self.model_id]
        if len(found) != 1 or not found[0].get("digest"):
            raise OllamaError("The exact model tag is not installed locally")
        show = self._http_json("/api/show", {"model": self.model_id})
        if "completion" not in show.get("capabilities", []):
            raise OllamaError("The installed model does not advertise text completion")
        thinking = show.get("thinking")
        if "thinking" in show.get("capabilities", []) and (
                type(thinking) is not dict or False not in thinking.get("values", [])):
            raise OllamaError("Cannot verify support for the required non-thinking setting")
        limits = [v for k, v in show.get("model_info", {}).items() if k.endswith(".context_length")
                  and type(v) is int]
        if limits and self.num_ctx > min(limits):
            raise OllamaError("Requested context exceeds the model's advertised capacity")
        identity = {"ollama_version": version, "model": self.model_id, "digest": found[0]["digest"],
                    "details": show.get("details"), "capabilities": show.get("capabilities"),
                    "thinking": thinking, "model_info": show.get("model_info"),
                    "model_sampling_defaults": show.get("parameters")}
        if self._identity is not None and self._identity != identity:
            raise OllamaError("The installed model or server changed during this experiment")
        self._identity = identity
        return self.metadata()

    def metadata(self):
        return {"provider": "ollama", "transport": "local_http", "endpoint": self.base_url + "/api/chat",
                "identity": deepcopy(self._identity), "requested_num_ctx": self.num_ctx,
                "think": False, "format": "json_schema" if self.structured_output else "json", "stream": False,
                "evidence_paths": self.evidence_paths,
                "format_policy": "Optional constrained decoding of existing schema; Python still validates evidence and actions. This setting must be reported in cross-provider comparisons.",
                "seed_policy": "Local scheduling seed is logged, not forwarded to the model.",
                "cost_policy": "No remote API billing. Local electricity/hardware costs are not measured.",
                "context_policy": "Conservative UTF-8 input envelope plus output allowance; reject oversize inputs before inference. This is not a tokenizer measurement.",
                "timeout_seconds": self.timeout}

    def build_payload(self, model_request):
        if self._identity is None:
            raise OllamaError("Inspect and pin the local model before running inference")
        messages = model_request.messages
        if type(messages) is not list or not messages or any(
                type(m) is not dict or set(m) != {"role", "content"}
                or m["role"] not in ("system", "user", "assistant") or type(m["content"]) is not str
                for m in messages):
            raise ValueError("Only explicit text chat messages are supported")
        limit = model_request.max_output_tokens
        if type(limit) is not int or limit <= 0:
            raise ValueError("A positive output token limit is required")
        temperature = model_request.temperature
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature < 0:
            raise ValueError("Temperature must be finite and nonnegative")
        # Byte fallback text tokenizers usually use fewer tokens than UTF-8
        # bytes. Leave substantial room for wrappers; reject instead of trimming.
        envelope = sum(len(m["content"].encode("utf-8")) + 1024 for m in messages) + limit + 1024
        if envelope > self.num_ctx:
            raise OllamaError("Conservative input envelope exceeds num_ctx; history was not truncated")
        return {"model": self.model_id, "messages": deepcopy(messages), "think": False,
                "format": output_schema(messages, evidence_paths=self.evidence_paths) if self.structured_output else "json", "stream": False, "keep_alive": "5m",
                "options": {"temperature": temperature, "num_predict": limit, "num_ctx": self.num_ctx}}

    def complete(self, model_request):
        payload = self.build_payload(model_request)
        self.sent_calls += 1
        result = self._http_json("/api/chat", payload)
        if result.get("model") != self.model_id or result.get("done") is not True:
            raise OllamaError("Incomplete response or unexpected local model")
        message = result.get("message")
        if type(message) is not dict or type(message.get("content")) is not str:
            raise OllamaError("Missing local model answer")
        if message.get("thinking"):
            raise OllamaError("The model emitted thinking despite think=false")
        for key in ("prompt_eval_count", "eval_count"):
            if type(result.get(key)) is not int or result[key] < 0:
                raise OllamaError("Missing or invalid local token usage")
        if result["prompt_eval_count"] + model_request.max_output_tokens > self.num_ctx:
            raise OllamaError("Reported input leaves insufficient context for the requested output")
        # The common runner rejects length/unknown termination before accepting
        # an action. Keep the original answer, even when truncated or invalid.
        reason = result.get("done_reason")
        return ModelResponse(message["content"], input_tokens=result["prompt_eval_count"],
            output_tokens=result["eval_count"], returned_model=result["model"],
            system_fingerprint=f"ollama:{self._identity['ollama_version']}:{self._identity['digest']}",
            finish_reason=reason if reason in ("stop", "length") else "unknown")
