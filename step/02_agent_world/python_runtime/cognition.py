"""Bounded, explicit agent judgments and persistent intentions, never P or M.

This stores the model's short report, not a claim to access its internal reasoning.
Evidence references establish input provenance, not the truth of a judgment.
"""
from __future__ import annotations

import copy
import re

from common import require

TERMINAL = {"abandoned", "completed_declared"}
OPS = {"create", "continue", "revise", "pause", "abandon", "complete"}


def validate_cognition_config(c: dict) -> None:
    require(set(c) == {"protocol", "max_tasks", "max_active_tasks", "max_updates_per_decision", "max_judgments",
                       "text_chars", "related_memories", "memory_event_limit"}, "Invalid cognition config fields")
    require(c["protocol"] == "judgments_intentions_v1", "Unsupported cognitive protocol")
    for k, v in c.items():
        if k != "protocol":
            require(type(v) is int and 0 < v <= 1000, f"Invalid cognition.{k}")
    require(c["max_active_tasks"] <= c["max_tasks"], "Active task bound exceeds total")


def output_contract() -> dict:
    return {"top_level_fields": ["action", "arguments", "cognition"],
            "cognition": {"judgments": [{"text": "short current assessment, NOT a verified fact",
                "confidence": "uncertain / tentative / confident", "basis_refs": ["visible observation/event id"]}],
                "intent_updates": [{"op": "create / continue / revise / pause / abandon / complete",
                    "task_id": "actor-local stable id; create a new unique id only for create",
                    "title": "short personal task", "next_step": "next intended step, not an executed act",
                    "basis_refs": ["visible observation/event id"]}],
                "action_task_id": "active task id after these updates, or null"},
            "rules": "Empty judgments/intent_updates and null task are legal. No mandatory goal. complete is self-declared, never objective success. SAY does not create tasks. All references must be present in this decision input."}


def visible_refs(context: dict) -> set[str]:
    refs = {context["Observation"]["observation_id"]}
    refs.update(e["event_id"] for e in context["Observation"]["content"].get("events", []))
    for memory in context["episodes"]:
        refs.add(memory["observation_id"])
        refs.update(e["event_id"] for e in memory.get("events", []))
    return refs


def apply_report(x: dict, report: dict, context: dict, config: dict, request_id: str) -> tuple[dict, str | None]:
    require(isinstance(report, dict) and set(report) == {"judgments", "intent_updates", "action_task_id"}, "invalid_cognitive_report")
    refs = visible_refs(context)
    def text(value):
        require(isinstance(value, str) and 0 < len(value.strip()) <= config["text_chars"], "invalid_cognitive_text")
    def basis(value):
        require(isinstance(value, list) and 0 < len(value) <= 12 and all(isinstance(v, str) and v in refs for v in value), "cognitive_reference_not_in_decision_input")
    require(isinstance(report["judgments"], list) and len(report["judgments"]) <= config["max_judgments"], "judgment_limit")
    require(isinstance(report["intent_updates"], list) and len(report["intent_updates"]) <= config["max_updates_per_decision"], "intent_update_limit")
    updated = copy.deepcopy(x)
    stamp = {"round": context["round"], "phase": context["phase"], "decision_ref": request_id,
             "cognition_revision": x.get("cognition_revision", 0) + 1,
             "basis_observation_id": context["Observation"]["observation_id"]}
    judgments = []
    for judgment in report["judgments"]:
        require(isinstance(judgment, dict) and set(judgment) == {"text", "confidence", "basis_refs"}, "invalid_judgment")
        text(judgment["text"]); basis(judgment["basis_refs"])
        require(judgment["confidence"] in ("uncertain", "tentative", "confident"), "invalid_confidence")
        judgments.append({**copy.deepcopy(judgment), **stamp, "epistemic_status": "agent_inference_not_world_fact_or_persona"})
    updated["judgments"] = judgments
    tasks = updated.setdefault("tasks", {})
    changed = set()
    for update in report["intent_updates"]:
        require(isinstance(update, dict) and set(update) == {"op", "task_id", "title", "next_step", "basis_refs"}, "invalid_intent_update")
        identity, op = update["task_id"], update["op"]
        require(isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", identity) is not None, "invalid_task_id")
        require(identity not in changed and isinstance(op, str) and op in OPS, "duplicate_or_invalid_task_update")
        changed.add(identity)
        text(update["title"]); text(update["next_step"]); basis(update["basis_refs"])
        if op == "create":
            require(identity not in tasks and len(tasks) < config["max_tasks"], "task_exists_or_capacity_reached")
            tasks[identity] = {"task_id": identity, "owner": context["agent_id"], "created": stamp,
                               "history": [], "attempts": [], "origin": "simulation_intention_not_persona"}
        else:
            require(identity in tasks and tasks[identity]["status"] not in TERMINAL, "task_missing_or_terminal")
        task = tasks[identity]
        status = {"pause": "paused", "abandon": "abandoned", "complete": "completed_declared"}.get(op, "active")
        task.update(title=update["title"], next_step=update["next_step"], status=status,
                    basis_refs=copy.deepcopy(update["basis_refs"]), updated=stamp,
                    completion_basis="actor_declaration_not_verified_success" if op == "complete" else None)
        task["history"].append({**copy.deepcopy(update), **stamp, "status": status})
    require(sum(t["status"] not in TERMINAL for t in tasks.values()) <= config["max_active_tasks"], "active_task_capacity_reached")
    linked = report["action_task_id"]
    require(linked is None or (isinstance(linked, str) and linked in tasks and tasks[linked]["status"] == "active"), "action_task_must_be_active_or_null")
    updated["cognition_revision"] = updated.get("cognition_revision", 0) + 1
    updated["interpretation_status"] = "explicit_actor_judgments_separate_from_delivered_observations"
    return updated, linked


def apply_feedback(x: dict, observation: dict) -> None:
    result = observation["content"].get("action_result")
    if not result:
        return
    task_id = result.get("task_id")
    task = x.get("tasks", {}).get(task_id) if isinstance(task_id, str) else None
    if task is None or any(a["request_id"] == result["request_id"] for a in task["attempts"]):
        return
    steps = result.get("effects", {}).get("steps", [])
    blocked = not result["accepted"] or any(not s["accepted"] for s in steps)
    task["attempts"].append({"request_id": result["request_id"], "accepted": result["accepted"],
        "partial_step_failure": any(not s["accepted"] for s in steps), "observation_id": observation["observation_id"],
        "round": observation["round"], "phase": result["phase"], "rejection_reason": result.get("rejection_reason")})
    task["last_feedback"] = copy.deepcopy(result)
    if task["status"] not in TERMINAL and task["status"] != "paused":
        task["status"] = "blocked" if blocked else "active"
    # GM success is evidence about one action, NOT completion of a multi-stage task.
