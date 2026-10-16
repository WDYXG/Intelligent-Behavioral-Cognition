"""Scene-independent P/M freeze, delivered memory, fallible judgments and tasks."""
from __future__ import annotations

import copy
import json

from common import canonical, digest, require, EventLog
from cognition import apply_feedback, apply_report
from context_builder import ContextDraft, build_draft, exposure_ids


class Agent:
    def __init__(self, member: dict):
        self.alias = member["alias"]
        # Immutable bytes are the only stored P/M representation; consumers receive copies.
        self._p = canonical(member["initial"]["P"])
        self._m = canonical(member["initial"]["M0"])
        self._view = canonical(member["view"])
        # Explicit empty-state runtime adapter; do not carry the old A/B score fields
        # into a community World, and do not modify the saved initialization artifact.
        self.X = {"version": 0, "observation_ids": [], "last_observation": None, "current_round": 0,
                  "runtime_adapter": "empty_source_to_scene_runtime_v2", "tasks": {}, "judgments": [], "cognition_revision": 0}
        self.episodes: list[dict] = []
        self._observation_ids: set[str] = set()
        # Only advance after a valid decision; local delivery alone is not model exposure.
        self._context_event_ids: set[str] = set()
        self._context_result_ids: set[str] = set()
        self.initial_frozen_hash = digest([json.loads(self._p), json.loads(self._m)])

    def snapshot(self) -> dict:
        return {"agent_id": self.alias, "P": json.loads(self._p), "M": json.loads(self._m), "X": copy.deepcopy(self.X),
                "context_access": {"event_ids_in_valid_decisions": sorted(self._context_event_ids),
                                   "result_ids_in_valid_decisions": sorted(self._context_result_ids)}}

    def assert_frozen(self) -> None:
        require(digest([json.loads(self._p), json.loads(self._m)]) == self.initial_frozen_hash, "P/M changed")

    def deliver(self, observation: dict, log: EventLog) -> None:
        require(observation["recipient"] == self.alias and observation["visibility"] == [self.alias], "Observation recipient/visibility mismatch")
        require(observation["observation_id"] not in self._observation_ids, "Duplicate observation")
        require(observation["round"] >= self.X["current_round"], "Observation time reversal")
        if self.X["last_observation"] is not None:
            require(observation["world_version"] >= self.X["last_observation"]["world_version"], "World observation version reversal")
        feedback = observation["content"].get("action_result")
        require(feedback is None or feedback.get("agent_id") == self.alias, "Wrong feedback recipient")
        event_ids = [e["event_id"] for e in observation["content"].get("events", [])]
        require(len(event_ids) == len(set(event_ids)), "Duplicate event in delivered Observation")
        for event in observation["content"].get("events", []):
            require(self.alias in event["visibility"] and event["round"] <= observation["round"], "Unauthorized/future WorldEvent")
        before = copy.deepcopy(self.X)
        obs = copy.deepcopy(observation)
        log.add("observation_delivered", obs)
        self.episodes.append(obs)
        self._observation_ids.add(obs["observation_id"])
        self.X.update({"version": before["version"] + 1,
            "observation_ids": before["observation_ids"] + [obs["observation_id"]],
            "current_round": obs["round"], "last_observation": obs,
            "observed_self": copy.deepcopy(obs["content"]["self"]),
            "interpretation_status": before.get("interpretation_status", "no_actor_judgment_yet")})
        apply_feedback(self.X, obs)
        log.add("state_transition", {"agent_id": self.alias, "writer": "StateUpdater", "rule": "delivered_scene_observation_v2",
            "cause_refs": [obs["observation_id"]], "allowed_paths": ["X"], "before": before, "after": copy.deepcopy(self.X)})
        self.assert_frozen()

    def decision_context(self, config: dict, round_no: int, seed: int) -> dict:
        """Compatibility view; Runner uses context_draft plus budget preparation."""
        return self.context_draft(config, round_no, seed).context

    def context_draft(self, config: dict, round_no: int, seed: int) -> ContextDraft:
        # Full P is never passed to the model-view builder; only the existing grant.
        return build_draft(config, self.alias, json.loads(self._view), json.loads(self._m),
            self.X, self.episodes, self._context_event_ids, self._context_result_ids, round_no, seed)

    def _record_context_access(self, context: dict, request_id: str, log: EventLog) -> None:
        events, results = exposure_ids(context)
        self._context_event_ids.update(events)
        self._context_result_ids.update(results)
        log.add("decision_context_access", {"agent_id": self.alias, "decision_ref": request_id,
            "context_sha256": digest(context), "event_ids": sorted(events), "result_ids": sorted(results),
            "meaning": "included_in_input_of_valid_decision_not_proof_of_belief_or_attention"})

    def accept_decision(self, decision: dict, context: dict, config: dict, request_id: str, log: EventLog) -> str | None:
        require(context["agent_id"] == self.alias
                and context["Observation"]["observation_id"] == self.X["last_observation"]["observation_id"]
                and context["X"]["version"] == self.X["version"]
                and context["X"]["cognition_revision"] == self.X["cognition_revision"], "Stale or foreign cognitive context")
        if not config.get("cognition"):
            self._record_context_access(context, request_id, log)
            return None
        before = copy.deepcopy(self.X)
        updated, linked = apply_report(self.X, decision["cognition"], context, config["cognition"], request_id)
        self.X = updated
        log.add("cognition_update", {"agent_id": self.alias, "round": context["round"], "phase": context["phase"],
            "decision_ref": request_id, "report": copy.deepcopy(decision["cognition"]),
            "epistemic_status": "model_report_not_verified_reasoning_or_world_truth"})
        log.add("state_transition", {"agent_id": self.alias, "writer": "CognitiveUpdater", "rule": "judgments_intentions_v1",
            "cause_refs": [context["Observation"]["observation_id"], request_id], "allowed_paths": ["X.judgments", "X.tasks", "X.cognition_revision", "X.interpretation_status"],
            "before": before, "after": copy.deepcopy(self.X)})
        self.assert_frozen()
        self._record_context_access(context, request_id, log)
        return linked
