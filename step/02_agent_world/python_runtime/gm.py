"""Domain-independent sealed-phase settlement, extracted from the community GM."""
from __future__ import annotations

import copy

from common import digest, require
from scene_adapter import SceneAdapter


class GM:
    def __init__(self, scene: SceneAdapter):
        self.scene = scene
        self._cache: dict[tuple[int, str], tuple[str, dict]] = {}

    def commit(self, requests: list[dict], order: list[str], phase: str) -> dict:
        scene = self.scene
        signature = digest({"requests": requests, "order": order})
        round_no = scene.state["round"]
        key = (round_no, phase)
        if key in self._cache:
            previous, transaction = self._cache[key]
            require(previous == signature, "Phase reused with different requests")
            return copy.deepcopy(transaction)
        scene.validate_phase(phase)
        count = len(scene.aliases)
        require(len(requests) == len(order) == count and set(order) == set(scene.aliases), "Incomplete phase/order")
        require({r["actor_id"] for r in requests} == set(scene.aliases), "Duplicate/unknown actor")
        require(len({r["request_id"] for r in requests}) == count, "Duplicate request identity")
        before, working = scene.snapshot(), scene.snapshot()
        by_actor = {r["actor_id"]: r for r in requests}
        results, events = [], []
        for alias in order:
            request, trial = by_actor[alias], copy.deepcopy(working)
            result = {"request_id": request["request_id"], "agent_id": alias, "round": round_no, "phase": phase,
                      "result_id": "result:" + request["request_id"],
                      "accepted": False, "rejection_reason": None, "executed_action": None,
                      "world_version_before": before["version"], "world_version_after": before["version"] + 1,
                      "effects": {}, "affected_agents": [alias], "task_id": request.get("task_id")}
            try:
                require(request["world_version"] == before["version"] and request["round"] == round_no, "stale_world_or_round")
                require(request.get("phase", "action") == phase, "wrong_request_phase")
                scene.check_preconditions(trial, request, before)
                effects, affected = scene.execute_action(trial, request, before)
            except ValueError as exc:
                result["rejection_reason"] = str(exc)
                result["rejection_details"] = copy.deepcopy(getattr(exc, "details", {}))
                result["possible_next_steps"] = scene.rejection_options(request, before, str(exc))
            else:
                # Invariant failure aborts the phase, not just this actor's action.
                scene.assert_invariants(trial)
                require(set(affected) <= set(scene.aliases), "Invalid result audience")
                events.extend(scene.collect_world_events(working, trial, phase))
                working = trial
                result.update(accepted=True, executed_action=request["action"], effects=effects, affected_agents=affected)
            results.append(result)
        before_settlement = copy.deepcopy(working)
        system_effects = scene.settle_phase(working, phase)
        working["version"] += 1
        events.extend(scene.collect_world_events(before_settlement, working, phase))
        scene.assert_invariants(working)
        transaction = {"round": round_no, "phase": phase, "order": order, "before": before, "after": working,
                       "results": results, "system_effects": system_effects, "world_events": events}
        scene.state = working
        self._cache[key] = (signature, copy.deepcopy(transaction))
        return copy.deepcopy(transaction)
