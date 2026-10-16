"""Local research state -> auditable decision view, without changing stored state.

No summarizer, Persona truncation, hidden World access, or decision fallback.
Mandatory delivery is independent of the replaceable historical-memory policy.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common import canonical, digest, file_hash, require
from cognition import TERMINAL, output_contract
from episodic_memory import MemoryAccessPolicy, MemoryChoice, load_policy


def validate_context_config(config: dict) -> None:
    setting = config.get("context", {})
    require(isinstance(setting, dict) and set(setting) <= {"memory_policy", "token_estimator"}, "Invalid context settings")
    policy = setting.get("memory_policy", {})
    require(isinstance(policy, dict) and set(policy) <= {"adapter", "options"}, "Invalid memory policy settings")
    if "options" in policy:
        require(isinstance(policy["options"], dict), "Memory policy options must be an object")
    estimator = setting.get("token_estimator", {"kind": "utf8_reservation_proxy_v1"})
    require(isinstance(estimator, dict), "Invalid token estimator settings")
    kind = estimator.get("kind")
    if kind == "utf8_reservation_proxy_v1":
        require(set(estimator) == {"kind"}, "Byte reservation proxy has no adjustable ratio")
    else:
        require(kind == "local_tokenizer_json_v1" and set(estimator) == {"kind", "path", "sha256", "model"},
                "Unsupported token estimator; local tokenizer requires path, sha256 and model")
        require(all(isinstance(estimator[k], str) and estimator[k] for k in ("path", "sha256", "model")), "Invalid tokenizer provenance")


@dataclass
class ContextDraft:
    context: dict
    audit: dict
    # Only optional, previously exposed historical records may be removed for fit.
    optional_priorities: dict[str, int]


@dataclass(frozen=True, init=False)
class PreparedDecision:
    context_bytes: bytes
    messages_bytes: bytes
    audit_bytes: bytes

    def __init__(self, context_bytes: bytes, messages_bytes: bytes, audit: dict):
        object.__setattr__(self, "context_bytes", bytes(context_bytes))
        object.__setattr__(self, "messages_bytes", bytes(messages_bytes))
        object.__setattr__(self, "audit_bytes", canonical(audit))

    @property
    def audit(self) -> dict:
        # frozen=True alone does not freeze nested dicts. Match context/messages:
        # store immutable bytes and expose detached views, including budget guards.
        return json.loads(self.audit_bytes)

    @property
    def context(self) -> dict:
        return json.loads(self.context_bytes)

    @property
    def messages(self) -> list[dict]:
        return json.loads(self.messages_bytes)


def exposure_ids(context: dict) -> tuple[set[str], set[str]]:
    records = [context["Observation"]["content"], *context["episodes"]]
    return ({e["event_id"] for record in records for e in record.get("events", [])},
            {record["action_result"]["request_id"] for record in records if record.get("action_result")})


def select_history(episodes: list[dict], tasks: dict, seen_events: set[str], seen_results: set[str],
                   policy: MemoryAccessPolicy) -> tuple[list[dict], dict, dict[str, int]]:
    """Exact ID+payload dedup precedes ranking, so duplicates use no retrieval slots.

    Current observation stays verbatim. Missing new events/feedback from earlier
    delivered observations are mandatory, even with a zero/empty retrieval policy.
    """
    require(bool(episodes), "Cannot build context before a delivered Observation")
    current = episodes[-1]
    actor = current["recipient"]
    all_events, all_results, origins = {}, {}, {}
    for obs in episodes:
        require(obs["recipient"] == actor and obs["visibility"] == [actor], "Foreign memory observation")
        require(obs["round"] <= current["round"] and obs["world_version"] <= current["world_version"], "Future memory observation")
        identities = [e["event_id"] for e in obs["content"].get("events", [])]
        require(len(identities) == len(set(identities)), "Duplicate event in delivered Observation; no silent rewrite")
        for event in obs["content"].get("events", []):
            identity = event["event_id"]
            require(actor in event["visibility"] and event["round"] <= obs["round"], "Unauthorized/future memory event")
            require(identity not in all_events or canonical(all_events[identity]) == canonical(event), "Conflicting payloads for one WorldEvent ID")
            all_events[identity] = event
            origins.setdefault(identity, []).append(obs["observation_id"])
        result = obs["content"].get("action_result")
        if result is not None:
            require(result.get("agent_id") == actor, "Wrong memory feedback recipient")
            identity = result["request_id"]
            require(identity not in all_results or canonical(all_results[identity]) == canonical(result), "Conflicting action feedback ID")
            all_results[identity] = result
    current_event_ids = {e["event_id"] for e in current["content"].get("events", [])}
    current_result = current["content"].get("action_result")
    consumed_events = set(current_event_ids)
    consumed_results = {current_result["request_id"]} if current_result else set()
    required, history, duplicates = [], [], []
    # Most recent delivered occurrence owns each unique past item. Output below is
    # restored to observation delivery order, and retains original event ordering.
    for obs in reversed(episodes[:-1]):
        required_events, old_events = [], []
        for event in obs["content"].get("events", []):
            identity = event["event_id"]
            if identity in consumed_events:
                duplicates.append({"event_id": identity, "removed_from": obs["observation_id"],
                                   "retained_in_current": identity in current_event_ids})
                continue
            consumed_events.add(identity)
            (old_events if identity in seen_events else required_events).append(copy.deepcopy(event))
        feedback = obs["content"].get("action_result")
        required_result = old_result = None
        if feedback is not None:
            rid = feedback["request_id"]
            if rid in consumed_results:
                duplicates.append({"request_id": rid, "removed_from": obs["observation_id"]})
            else:
                consumed_results.add(rid)
                if rid in seen_results:
                    old_result = copy.deepcopy(feedback)
                else:
                    required_result = copy.deepcopy(feedback)
        header = {k: copy.deepcopy(obs[k]) for k in ("observation_id", "round", "phase", "world_version")}
        if required_events or required_result is not None:
            required.append({**header, "events": required_events, "action_result": required_result,
                             "context_role": "mandatory_new_delivery"})
        if old_events or old_result is not None:
            history.append({**header, "events": old_events, "action_result": old_result})
    history.reverse()
    required.reverse()
    choices = policy.select(copy.deepcopy(history), copy.deepcopy(current), copy.deepcopy(tasks))
    require(isinstance(choices, list) and all(isinstance(c, MemoryChoice) for c in choices), "Memory policy must return MemoryChoice list")
    source = {o["observation_id"]: o for o in history}
    selected, priorities = [], {}
    for choice in choices:
        require(choice.observation_id in source and choice.observation_id not in priorities, "Unknown/duplicate memory observation selection")
        require(type(choice.priority) is int and type(choice.include_action_result) is bool, "Invalid memory priority/result selection")
        obs = source[choice.observation_id]
        available = {e["event_id"] for e in obs["events"]}
        require(isinstance(choice.event_ids, tuple) and len(set(choice.event_ids)) == len(choice.event_ids)
                and set(choice.event_ids) <= available, "Memory policy selected an undelivered event")
        require(not choice.include_action_result or obs["action_result"] is not None, "Memory policy selected absent feedback")
        priorities[choice.observation_id] = choice.priority
        selected.append({**copy.deepcopy(obs), "events": [copy.deepcopy(e) for e in obs["events"] if e["event_id"] in choice.event_ids],
                         "action_result": copy.deepcopy(obs["action_result"]) if choice.include_action_result else None,
                         "context_role": "optional_retrieval"})
    order = {obs["observation_id"]: i for i, obs in enumerate(episodes)}
    records = sorted(required + selected, key=lambda o: order[o["observation_id"]])
    return records, {"rule": "exact_event_id_and_payload_current_first_latest_history_owner_v1",
        "duplicates": duplicates, "event_source_observation_ids": origins,
        "mandatory_event_ids": sorted(set(all_events) - seen_events),
        "mandatory_result_ids": sorted(set(all_results) - seen_results),
        "available_history": [{"observation_id": o["observation_id"], "event_ids": [e["event_id"] for e in o["events"]],
                               "result_id": o["action_result"]["request_id"] if o["action_result"] else None} for o in history],
        "policy_choices": [{"observation_id": c.observation_id, "event_ids": list(c.event_ids),
                            "include_action_result": c.include_action_result, "priority": c.priority} for c in choices]}, priorities


def build_draft(config: dict, actor: str, authorized_p: dict, m: dict, x: dict, episodes: list[dict],
                seen_events: set[str], seen_results: set[str], round_no: int, seed: int) -> ContextDraft:
    validate_context_config(config)
    policy, policy_audit = load_policy(config)
    cognitive = config.get("cognition", {})
    closed = [k for k, task in x["tasks"].items() if task["status"] in TERMINAL]
    recent_closed = set(sorted(closed, key=lambda k: x["tasks"][k]["updated"]["cognition_revision"])[-2:])
    tasks = {k: {field: copy.deepcopy(value) for field, value in task.items() if field not in ("history", "attempts", "last_feedback")}
             for k, task in x["tasks"].items() if task["status"] not in TERMINAL or k in recent_closed}
    for identity, view in tasks.items():
        feedback = x["tasks"][identity].get("last_feedback")
        view["last_result"] = ({k: copy.deepcopy(feedback.get(k)) for k in
            ("request_id", "accepted", "rejection_reason", "possible_next_steps")} if feedback else None)
    records, retrieval_audit, priorities = select_history(episodes, tasks, seen_events, seen_results, policy)
    x_view = {k: copy.deepcopy(x[k]) for k in ("version", "cognition_revision", "current_round", "judgments", "interpretation_status") if k in x}
    x_view.update(tasks=tasks, archived_terminal_task_count=len(closed) - len(recent_closed), used_task_ids=list(x["tasks"]))
    context = {"agent_id": actor, "round": round_no, "seed": seed, "phase": episodes[-1]["content"]["decision_phase"],
        "P": copy.deepcopy(authorized_p), "M": copy.deepcopy(m), "X": x_view,
        "Observation": copy.deepcopy(episodes[-1]), "episodes": records,
        "persona_scope": "authorized historical survey self-reports; unknown unchanged; no inferred skills",
        "learning_enabled": False, "decision_protocol": cognitive.get("protocol", "action_only_v1"),
        "cognitive_limits": copy.deepcopy(cognitive),
        "output_contract": output_contract() if cognitive else {"top_level_fields": ["action", "arguments"]}}
    require(context["Observation"]["recipient"] == actor, "Foreign decision observation")
    audit = {"builder_version": "decision_context_v1", "policy": policy_audit, "retrieval": retrieval_audit,
        "local_state": {"authorized_P_sha256": digest(authorized_p), "M_sha256": digest(m), "X_sha256": digest(x),
            "episodes_sha256": digest(episodes), "observation_ids": [o["observation_id"] for o in episodes],
            "current_Observation_sha256": digest(episodes[-1]),
            "task_history_sha256": digest(x["tasks"])},
        "projection": {"P": "entire_existing_authorized_view_unchanged", "M": "entire_current_M_unchanged",
            "X": "versions_and_current_judgments; observations/events represented once outside X",
            "tasks": "all_live_plus_two_latest_terminal; current_status_and_last_result; full_history_remains_local",
            "task_ids_in_view": list(tasks), "terminal_task_ids_local_only": [k for k in closed if k not in recent_closed],
            "task_history_entries_local": sum(len(t.get("history", [])) for t in x["tasks"].values()),
            "task_attempt_entries_local": sum(len(t.get("attempts", [])) for t in x["tasks"].values()),
            "past_snapshots": "raw_self_scene_snapshots_remain_local; historical_event_and_feedback_selection_only",
            "Observation": "latest_delivered_observation_verbatim; unseen_prior_events_and_feedback_mandatory"}}
    return ContextDraft(context, audit, priorities)


class TokenEstimator:
    """Counts message CONTENT, never the escaped HTTP JSON envelope.

    Default is an explicit conservative byte-denominated reservation proxy, NOT a
    measured token count or a guarantee for arbitrary provider tokenizers. Optional
    local tokenizer is hash/model pinned, never downloaded. Chat framing still has
    an explicit allowance; provider usage is the only actual billed token count.
    """
    def __init__(self, config: dict, model: str):
        validate_context_config(config)
        setting = config.get("context", {}).get("token_estimator", {"kind": "utf8_reservation_proxy_v1"})
        self.kind = setting["kind"]
        self.tokenizer = None
        self.provenance = copy.deepcopy(setting)
        if self.kind == "local_tokenizer_json_v1":
            require(setting["model"] == model, "Local tokenizer model does not match configured API model")
            path = Path(setting["path"])
            require(path.is_absolute() and path.is_file(), "Tokenizer must be an existing absolute local file; no download fallback")
            require(file_hash(path).lower() == setting["sha256"].lower(), "Local tokenizer SHA-256 mismatch")
            try:
                from tokenizers import Tokenizer
            except ImportError:
                raise ValueError("Local tokenizer selected but tokenizers dependency is absent; no estimator fallback") from None
            self.tokenizer = Tokenizer.from_file(str(path))
            # Disallow tokenizer.json truncation/padding silently changing counts.
            self.tokenizer.no_truncation()
            self.tokenizer.no_padding()

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids) if self.tokenizer else len(text.encode("utf-8"))


def source_parts(context: dict, messages: list[dict]) -> dict[str, str]:
    """Diagnostic attribution, not another prompt. Task fields stay at X.tasks.

    Fragment serialization/token boundaries differ from full-message boundaries;
    report the reconciliation residual rather than pretending counts are additive.
    """
    x = copy.deepcopy(context["X"])
    tasks = {k: x.pop(k) for k in ("tasks", "archived_terminal_task_count", "used_task_ids") if k in x}
    observation = copy.deepcopy(context["Observation"])
    scene = observation.get("content", {}).get("scene", {})
    action_schema = scene.pop("action_space", None)
    rest = {k: v for k, v in context.items() if k not in ("P", "M", "X", "Observation", "episodes")}
    mandatory = [r for r in context["episodes"] if r["context_role"] == "mandatory_new_delivery"]
    retrieved = [r for r in context["episodes"] if r["context_role"] == "optional_retrieval"]
    parts = {"Persona_P": context["P"], "M": context["M"], "X": x,
             "current_Observation": observation, "pending_Observation_delivery": mandatory,
             "episodic_retrieval": retrieved, "task_intention_history_view": tasks,
             "fixed_instruction_schema_metadata": {"context_metadata": rest, "action_schema": action_schema}}
    result = {k: canonical(v).decode("utf-8") for k, v in parts.items()}
    result["fixed_instruction_schema_metadata"] += "\n" + messages[0]["content"]
    return result


class ContextBuilder:
    def __init__(self, config: dict, model: str, message_factory: Callable[[dict], list[dict]]):
        self.config, self.model, self.message_factory = config, model, message_factory

    def prepare(self, draft: ContextDraft) -> PreparedDecision:
        estimator = TokenEstimator(self.config, self.model)
        context, audit = copy.deepcopy(draft.context), copy.deepcopy(draft.audit)
        cap = self.config["api"]["max_input_tokens"]
        # Retained from the previous guard, now explicit and separate from content.
        framing_allowance = 1024
        removed = []
        messages = self.message_factory(context)
        initial = sum(estimator.count(m["content"]) for m in messages) + framing_allowance
        def measure_sections(view: dict, final_messages: list[dict]) -> dict:
            return {name: {"estimated_tokens_or_proxy_units": estimator.count(text),
                           "utf8_bytes": len(text.encode("utf-8")), "content_sha256": digest(text)}
                    for name, text in source_parts(view, final_messages).items()}
        initial_sections = measure_sections(context, messages)
        for identity, priority in sorted(draft.optional_priorities.items(), key=lambda item: (item[1], item[0])):
            if sum(estimator.count(m["content"]) for m in messages) + framing_allowance <= cap:
                break
            dropped = [r for r in context["episodes"] if r["observation_id"] == identity and r["context_role"] == "optional_retrieval"]
            context["episodes"] = [r for r in context["episodes"] if not (r["observation_id"] == identity and r["context_role"] == "optional_retrieval")]
            removed.append({"observation_id": identity, "priority": priority, "reason": "optional_history_budget_fit",
                            "event_ids": [e["event_id"] for r in dropped for e in r["events"]],
                            "result_ids": [r["action_result"]["request_id"] for r in dropped if r["action_result"]]})
            messages = self.message_factory(context)
        events, results = exposure_ids(context)
        require(set(audit["retrieval"]["mandatory_event_ids"]) <= events
                and set(audit["retrieval"]["mandatory_result_ids"]) <= results, "Context lost mandatory delivered information")
        require(digest(context["P"]) == audit["local_state"]["authorized_P_sha256"]
                and digest(context["M"]) == audit["local_state"]["M_sha256"], "Context changed authorized P/M")
        require(digest(context["Observation"]) == audit["local_state"]["current_Observation_sha256"], "Context changed current Observation")
        content_count = sum(estimator.count(m["content"]) for m in messages)
        sections = measure_sections(context, messages)
        audit.update({"budget_removed_history": removed, "final_event_ids": sorted(events), "final_result_ids": sorted(results),
            "context_sha256": digest(context), "messages_sha256": digest(messages),
            "budget": {"estimator": estimator.provenance, "actual_prompt_tokens": None,
                "is_actual_provider_token_count": False, "sections": sections, "sections_before_budget_fit": initial_sections,
                "unit": "utf8_byte_reservation_proxy" if estimator.tokenizer is None else "local_tokenizer_content_tokens",
                "message_content_estimate_or_proxy_units": content_count,
                "section_boundary_reconciliation": content_count - sum(s["estimated_tokens_or_proxy_units"] for s in sections.values()),
                "chat_framing_and_uncertainty_allowance": framing_allowance,
                "initial_input_reservation": initial, "input_reservation": content_count + framing_allowance,
                "configured_input_cap": cap, "fits": content_count + framing_allowance <= cap,
                "financial_input_reserved_per_attempt": cap,
                "financial_total_tokens_reserved_per_attempt": cap + self.config["api"]["max_output_tokens"],
                "wire_messages_utf8_bytes_not_tokens": len(canonical(messages)),
                "limitations": "UTF-8 mode is a conservative reservation proxy, not tokenization. Local tokenizer counts exclude provider chat framing. Neither proves actual provider usage; no P/current/new-delivery truncation."}})
        return PreparedDecision(canonical(context), canonical(messages), audit)
