"""DeepSeek text/JSON adapter with durable, shared CNY budget reservations.

No automatic HTTP retries. Unknown usage retains the full reservation and stops.
Rates are the official peak CNY prices checked on 2026-10-20; not invoice totals.
"""

from decimal import Decimal, InvalidOperation
import fcntl
import http.client
import json
import math
import os
from pathlib import Path
import time

from public_goods.models.client import ModelResponse


ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
MAX_CONTEXT = 1048576  # Conservative binary interpretation of the documented 1M context.
PRICING = {"currency": "CNY", "verified_on": "2026-10-20",
           "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
           "input_miss_per_million": "2", "input_hit_per_million": "0.04", "output_per_million": "8",
           "basis": "Peak rates; therefore a conservative cost bound, not the provider invoice."}


class BudgetLimitError(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


class ConnectionNotSent(ProviderError):
    """TLS connection failed before any HTTP request or credential was sent."""


def cost_upper(prompt, completion, cached=0):
    for value in (prompt, completion, cached):
        if type(value) is not int or value < 0:
            raise ValueError("Usage must contain nonnegative integer token counts")
    if cached > prompt:
        raise ValueError("Cached tokens exceed input tokens")
    return ((prompt - cached) * Decimal(PRICING["input_miss_per_million"])
            + cached * Decimal(PRICING["input_hit_per_million"])
            + completion * Decimal(PRICING["output_per_million"])) / 1000000


class CNYBudget:
    """One locked ledger shared across arms and retained across process restarts."""

    @staticmethod
    def _positive_limit(value):
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            raise ValueError("Budget limit must be a finite positive CNY amount") from None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Budget limit must be a finite positive CNY amount")
        return amount

    def __init__(self, path, limit=None):
        # Omission means adopt the durable current limit, or 10 for a new ledger.
        # An explicit limit is an assertion, never permission to rewrite history.
        requested = None if limit is None else self._positive_limit(limit)
        self.limit = requested if requested is not None else Decimal(10)
        self.spent, self.held, self.uncertain = Decimal(0), Decimal(0), Decimal(0)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+")
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file.seek(0)
            rows = [json.loads(line) for line in self.file if line.strip()]
            if not rows:
                self._write({"kind": "opened", "limit_cny": str(self.limit), "pricing": PRICING})
            else:
                header = rows[0]
                if (set(header) != {"kind", "limit_cny", "pricing"}
                        or header["kind"] != "opened" or header["pricing"] != PRICING):
                    raise BudgetLimitError("Existing budget/pricing differs; never reset a ledger silently")
                self.limit = self._positive_limit(header["limit_cny"])
                for row in rows[1:]:
                    self._apply(row)
                if requested is not None and requested != self.limit:
                    raise BudgetLimitError("Existing current budget differs; use an explicit set_limit event")
        except Exception:
            self.file.close()
            raise

    def _apply(self, row):
        if row["kind"] == "reserved":
            amount = Decimal(row["cny"])
            if self.held or not amount.is_finite() or amount <= 0 or self.spent + self.uncertain + amount > self.limit:
                raise BudgetLimitError("Invalid reservation ledger")
            self.held = amount
        elif row["kind"] == "settled":
            amount = Decimal(row["cny"])
            if not self.held or not amount.is_finite() or not 0 <= amount <= self.held:
                raise BudgetLimitError("Invalid settlement ledger")
            self.spent += amount
            self.held = Decimal(0)
        elif row["kind"] == "quarantined":
            amount = Decimal(row["cny"])
            if not self.held or amount != self.held or not row.get("reason"):
                raise BudgetLimitError("Invalid unresolved-cost quarantine")
            self.uncertain += self.held
            self.held = Decimal(0)
        elif row["kind"] == "limit_changed":
            amount = self._positive_limit(row["limit_cny"])
            self._validate_limit_change(amount, row.get("reason"))
            if self._positive_limit(row["previous_limit_cny"]) != self.limit:
                raise BudgetLimitError("Budget limit change has the wrong previous limit")
            self.limit = amount
        else:
            raise BudgetLimitError("Unsupported budget ledger event")

    def _validate_limit_change(self, amount, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise BudgetLimitError("An explicit reason is required for a budget limit change")
        if self.held:
            raise BudgetLimitError("Cannot change the budget while a request has unresolved usage")
        if amount < self.spent + self.uncertain:
            raise BudgetLimitError("Budget limit cannot be below known and quarantined costs")

    def set_limit(self, amount, reason):
        """Append an authorized limit change without releasing any prior costs."""
        amount = self._positive_limit(amount)
        self._validate_limit_change(amount, reason)
        row = {"kind": "limit_changed", "previous_limit_cny": str(self.limit),
               "limit_cny": str(amount), "reason": reason}
        self._write(row)
        self._apply(row)

    def _write(self, row):
        self.file.seek(0, 2)
        self.file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def reserve(self, output_cap):
        if self.held:
            raise BudgetLimitError("An earlier request has unresolved usage; reconcile it before continuing")
        amount = cost_upper(MAX_CONTEXT, output_cap)
        if self.spent + self.uncertain + amount > self.limit:
            raise BudgetLimitError("Not enough remaining CNY budget for the next request's worst-case reservation")
        row = {"kind": "reserved", "cny": str(amount)}
        self._write(row)  # Persist before sending any network request.
        self._apply(row)

    def settle(self, amount):
        if not self.held or not 0 <= amount <= self.held:
            raise BudgetLimitError("Reported cost exceeds reservation; stop and reconcile the account")
        row = {"kind": "settled", "cny": str(amount)}
        self._write(row)
        self._apply(row)

    def snapshot(self):
        return {"limit_cny": str(self.limit), "known_cost_upper_cny": str(self.spent),
                "unresolved_reservation_cny": str(self.held + self.uncertain),
                "active_request_reservation_cny": str(self.held),
                "quarantined_reservation_cny": str(self.uncertain),
                "remaining_unreserved_cny": str(self.limit - self.spent - self.held - self.uncertain),
                "pricing": PRICING}

    def quarantine_unresolved(self, reason):
        """Manual recovery: retain the entire unknown cost, never free its budget."""
        if not self.held or not isinstance(reason, str) or not reason.strip():
            raise BudgetLimitError("An active reservation and recovery reason are required")
        row = {"kind": "quarantined", "cny": str(self.held), "reason": reason}
        self._write(row)
        self._apply(row)

    def close(self):
        self.file.close()


def load_key(path=None):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    path = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not key and path.is_file():
        for line in path.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "DEEPSEEK_API_KEY":
                key = value.strip().strip("\"'")
    if not key or any(c.isspace() for c in key):
        raise ProviderError("Configure DEEPSEEK_API_KEY in the local .env file or environment")
    return key


class DeepSeekClient:
    model_id = MODEL
    mode = "live"
    source_files = ("models/deepseek.py",)

    def __init__(self, api_key, budget, *, transport=None):
        if not isinstance(api_key, str) or not api_key or any(c.isspace() for c in api_key):
            raise ProviderError("A local DeepSeek key is required")
        self._key, self.budget = api_key, budget
        self._transport = transport or self._post
        self.sent_calls = 0

    def metadata(self):
        return {"endpoint": ENDPOINT, "model_alias": MODEL,
                "documented_version_on_20261020": "DeepSeek-V4.1-Flash-1017",
                "thinking": "disabled", "response_format": "json_object", "stream": False,
                "seed_support": "Not documented; seed is logged locally but not sent to the provider.",
                "transport_retries": 0, "timeout_seconds": 120, "budget": self.budget.snapshot()}

    def build_payload(self, request):
        if type(request.max_output_tokens) is not int or not 1 <= request.max_output_tokens <= 1024:
            raise ValueError("Trial output cap must be between 1 and 1024")
        if type(request.temperature) not in (int, float) or not math.isfinite(request.temperature) or not 0 <= request.temperature <= 2:
            raise ValueError("Invalid sampling temperature")
        if not request.messages or any(set(m) != {"role", "content"} or m["role"] not in ("system", "user")
                                       or type(m["content"]) is not str for m in request.messages):
            raise ValueError("This trial accepts text system/user messages only")
        return {"model": MODEL, "messages": request.messages, "max_tokens": request.max_output_tokens,
                "temperature": request.temperature, "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"}, "stream": False}

    def _post(self, payload):
        connection = http.client.HTTPSConnection("api.deepseek.com", timeout=30)
        deadline = time.monotonic() + 120
        try:
            try:
                connection.connect()
            except Exception as error:
                raise ConnectionNotSent(f"TLS connection failed before HTTP send ({type(error).__name__})") from None
            connection.request("POST", "/chat/completions", body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                               headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
            sock = connection.sock
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderError(f"DeepSeek HTTP {response.status}; request stopped without transport retry")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                sock.settimeout(min(30, remaining))
                chunk = response.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 1048576:
                    raise ProviderError("Provider response exceeded the transport size limit")
            return json.loads(b"".join(chunks))
        finally:
            connection.close()

    def complete(self, request):
        payload = self.build_payload(request)
        self.budget.reserve(request.max_output_tokens)
        self.sent_calls += 1
        try:
            result = self._transport(payload)
        except ConnectionNotSent:
            self.budget.settle(Decimal(0))
            raise
        except ProviderError:
            raise
        except Exception:
            # Do not propagate HTTP bodies, headers or low-level exception details.
            raise ProviderError("Network/response failure; usage unresolved, reservation retained; no transport retry") from None
        try:
            usage = result["usage"]
            prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
            cached, missed = usage["prompt_cache_hit_tokens"], usage["prompt_cache_miss_tokens"]
            amount = cost_upper(prompt, completion, cached)
            if type(missed) is not int or missed < 0 or cached + missed != prompt:
                raise ValueError
            if (prompt > MAX_CONTEXT or completion > request.max_output_tokens
                    or type(usage["total_tokens"]) is not int or usage["total_tokens"] != prompt + completion):
                raise ValueError
            if usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0) != 0:
                raise ValueError
            choice = result["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, (str, type(None))) or len(result["choices"]) != 1:
                raise ValueError
            if not isinstance(result["id"], str) or not isinstance(result["model"], str):
                raise ValueError
            reason = choice["finish_reason"]
            if reason not in ("stop", "length", "content_filter", "tool_calls", "insufficient_system_resource"):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError("Invalid provider usage/response; reservation retained; stop and reconcile") from None
        self.budget.settle(amount)
        return ModelResponse(content or "", input_tokens=prompt, output_tokens=completion,
                             provider_request_id=result["id"], cost_cny_upper=str(amount), cached_input_tokens=cached,
                             returned_model=result["model"], system_fingerprint=result.get("system_fingerprint"),
                             finish_reason=reason)
