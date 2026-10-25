"""Per-agent frozen P/M and transactional X/episodic state.

Only the cognitive updater owns this store. Model requests receive detached
views, not the store itself. A prepared state is provisional until the runner
has accepted the complete World batch. This is an in-process transaction
boundary, not a claim of durable recovery or of learned cognitive mechanisms.
"""

from copy import deepcopy
import hashlib
import json
import math


class StateError(ValueError):
    """Invalid state input or unauthorized/stale state transaction."""


def _require(condition, message):
    if not condition:
        raise StateError(message)


def _json_value(value):
    """Reject non-JSON objects rather than silently coercing their types."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        _require(math.isfinite(value), "State values must be finite JSON values")
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        _require(all(type(key) is str for key in value), "State object keys must be strings")
        for item in value.values():
            _json_value(item)
        return
    raise StateError("State values must use JSON types")


def _canonical(value):
    _json_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def initial_state():
    """Common missing-data initialization; no synthetic individual attributes."""
    return {
        "P": {
            "schema": "persona-v1",
            "initialization": "common_unknown",
            "frozen": True,
            "layers": {
                f"L{index}": {
                    "status": "unknown", "value": None, "sources": [],
                    "missing_reason": "No authorized individual evidence is used in this experiment.",
                }
                for index in range(1, 9)
            },
        },
        "M": {"schema": "long-term-state-v1", "learning_enabled": False,
              "frozen": True, "content": {}, "sources": []},
        "X": None,
        "version": 0,
        "episodes": [],
    }


def _episode(index, event):
    # Index distinguishes repeated events with identical values. Content hashes
    # verify a delivered event; they are not evidence of independent support.
    return {"index": index, "id": f"episode-{index}-{_digest(event)}",
            "observation_path": f"/history/{index}", "event": deepcopy(event)}


class AgentState:
    """An isolated state store with one-use, instance-local commit permission."""

    def __init__(self):
        self._state = initial_state()
        self._frozen = {key: _canonical(self._state[key]) for key in ("P", "M")}
        # Keep strong references: a recycled Python object ID cannot acquire
        # another transition's permission. Tokens never enter serialized logs.
        self._pending = {}

    def snapshot(self):
        return deepcopy(self._state)

    def prepare(self, observation, judgment):
        """Prepare an update from a delivered view without mutating state.

        The caller validates the judgment schema/evidence before calling this
        method. This store validates state ownership, history and frozen data;
        it does not claim that free-text judgments are factually correct.
        """
        _require(type(judgment) is dict, "Judgment must be an object")
        _json_value(judgment)
        return self._prepare(observation, judgment, "cognitive_update")

    def prepare_feedback(self, observation):
        """Record delivered feedback without inferring or revising a belief."""
        return self._prepare(observation, None, "feedback_ingest")

    def _prepare(self, observation, judgment, kind):
        _require(type(observation) is dict, "Observation must be an object")
        _json_value(observation)
        _require(observation.get("phase") in ("N", "P"), "Unknown observation phase")
        _require(type(observation.get("round")) is int and 1 <= observation["round"] <= 10,
                 "Observation round must be an integer from 1 to 10")
        if kind == "cognitive_update":
            _require(observation.get("stage") in ("contribution", "punishment"),
                     "Only decision-stage observations update X")
        else:
            _require(observation.get("stage") == "feedback", "Feedback ingestion requires delivered feedback")
            _require(type(observation.get("feedback")) is dict
                     and observation.get("history")
                     and observation["history"][-1] == observation["feedback"],
                     "Delivered feedback must match the latest visible event")
        _require(observation["stage"] != "punishment" or observation["phase"] == "P",
                 "Punishment is unavailable in the N phase")
        history = observation.get("history")
        _require(type(history) is list and all(type(event) is dict for event in history),
                 "Visible history must be a list of event objects")

        current = self._state
        for key in ("P", "M"):
            _require(_canonical(current[key]) == self._frozen[key], "Frozen P/M state changed")
        _require(len(history) >= len(current["episodes"]), "Visible history was truncated")
        for index, old in enumerate(current["episodes"]):
            _require(_episode(index, history[index]) == old, "Previously delivered history was rewritten")

        after = deepcopy(current)
        after["episodes"].extend(_episode(index, history[index])
                                 for index in range(len(current["episodes"]), len(history)))
        after["version"] += 1
        if kind == "cognitive_update":
            after["X"] = {key: observation[key] for key in ("phase", "round", "stage")}
            after["X"]["judgment"] = deepcopy(judgment)
        transition = {"kind": kind, "before_version": current["version"], "after_version": after["version"],
                      "observation_sha256": _digest(observation), "state": after}
        self._pending[id(transition)] = (transition, _digest(transition))
        return transition

    def commit(self, transition):
        """Commit one original, unchanged, current-version prepared object."""
        authorized = self._pending.pop(id(transition), None)
        _require(authorized is not None and authorized[0] is transition,
                 "Transition is foreign, stale, discarded or already committed")
        _require(_digest(transition) == authorized[1], "Prepared transition was modified")
        _require(type(transition["before_version"]) is int
                 and transition["before_version"] == self._state["version"]
                 and transition["after_version"] == self._state["version"] + 1,
                 "Transition version is stale or invalid")
        after = transition["state"]
        _require(after["version"] == transition["after_version"], "State version mismatch")
        for key in ("P", "M"):
            _require(_canonical(after[key]) == self._frozen[key], "Frozen P/M state cannot be updated")
        self._state = deepcopy(after)
        # Competing preparations from the previous version cannot be committed.
        self._pending.clear()

    def discard_pending(self):
        """Invalidate provisional updates without changing committed state."""
        self._pending.clear()
