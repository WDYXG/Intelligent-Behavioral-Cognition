"""Community adapters: existing rules plus bounded, auditable open social acts.

OPEN is NOT a universal natural-language physics engine. Free text can initiate
communication/contact/negotiation or inspect authorized information. Material
operations require an explicit structured grounding checked by the old kernel.
No social act supplies another actor's consent, beliefs or response.
"""
from __future__ import annotations

import copy
import re

from common import require
from community_world import ActionRejected, CommunityWorld
from living_world import LivingCommunityWorld, validate_living_config


def validate_community_config(c: dict) -> None:
    require(c["observation"]["scope"] == "public_community_plus_own_private", "Unsupported community observation scope")
    w = c["world"]
    for name in ("initial_personal_credits", "initial_pool", "initial_common_need", "need_growth_per_round",
                 "labor_per_round", "withdrawal_expectation", "max_projects", "max_open_per_proposer",
                 "message_chars", "private_history_window", "quorum"):
        require(type(w[name]) is int and w[name] >= 0, f"Invalid world.{name}")
    require(1 <= w["quorum"] <= 12 and all(w[k] > 0 for k in ("labor_per_round", "max_projects", "message_chars", "max_open_per_proposer")), "Invalid world limits")
    require(type(w["approval_fraction"]) in (int, float) and 0 <= w["approval_fraction"] < 1, "Invalid approval fraction")
    templates = w["project_templates"]
    require(templates and len({t["id"] for t in templates}) == len(templates), "Missing/duplicate project templates")
    for t in templates:
        require(isinstance(t["id"], str) and isinstance(t["title"], str), "Invalid template identity")
        for key in ("cost", "labor_required", "need_reduction", "credit_reward_each", "service_units_each"):
            require(type(t[key]) is int and t[key] >= 0, f"Invalid project {key}")
        require(t["labor_required"] > 0, "Project requires positive work")


class SceneBridge:
    """Normalizes the legacy kernel; core Agent never knows its field names."""
    @classmethod
    def validate_config(cls, config: dict) -> None:
        validate_community_config(config)

    @classmethod
    def phase_names(cls, config: dict) -> list[str]:
        return ["action"]

    def describe(self, alias: str) -> dict:
        require(alias in self.aliases, "Unknown resident")
        return {"name": self.config.get("scene", {}).get("name", "community"),
                "origin": "experimental_environment_not_persona_evidence",
                "description": "Shared community with independent residents; no required collective outcome."}

    def capabilities(self, alias: str) -> dict:
        return copy.deepcopy(super().observe(alias, "capabilities")["content"]["community"]["action_space"])

    def begin_round(self, number: int) -> dict:
        transition = super().begin_round(number)
        transition["world_events"] = self.collect_world_events(transition["before"], self.state, "environment")
        transition["after"] = self.snapshot()
        return transition

    def collect_world_events(self, before: dict, after: dict, phase: str) -> list[dict]:
        events, pending = [], []
        streams = [("public", after["public_events"], len(before["public_events"]), list(self.aliases))]
        streams.extend((alias, after["private_events"][alias], len(before["private_events"][alias]), [alias]) for alias in self.aliases)
        speech = {"SAY", "SEND", "ASK_HELP", "RESPOND", "UPDATE_HELP", "CLOSE_HELP", "OPEN_SOCIAL"}
        for stream, records, offset, audience in streams:
            for index in range(offset, len(records)):
                record = records[index]
                step = re.search(r":step(\d+)$", record.get("request_id", ""))
                pending.append((int(step.group(1)) if step else 0, len(pending), stream, index, record, audience))
        for _, _, stream, index, record, audience in sorted(pending, key=lambda item: item[:2]):
            after["event_sequence"] = after.get("event_sequence", 0) + 1
            kind = record.get("action", record.get("type"))
            record.update({"event_id": f"world:{stream}:{index + 1}", "phase": record.get("phase", phase),
                           "world_sequence": after["event_sequence"],
                           "visibility": audience,
                           "epistemic_status": "reported_speech_not_verified" if kind in speech else "world_effect"})
            events.append(self._event_envelope(record))
        return events

    @staticmethod
    def _event_envelope(record: dict) -> dict:
        return {"event_id": record["event_id"], "world_sequence": record["world_sequence"], "round": record.get("round", 0), "phase": record["phase"],
                "visibility": copy.deepcopy(record["visibility"]), "epistemic_status": record["epistemic_status"],
                "request_id": record.get("request_id"),
                "payload": {k: copy.deepcopy(v) for k, v in record.items()
                            if k not in ("event_id", "world_sequence", "visibility", "epistemic_status", "phase", "round")}}

    def observe(self, alias: str, phase: str, feedback: dict | None = None, since: dict | None = None) -> dict:
        obs = super().observe(alias, phase, feedback, since)
        old = obs["content"]
        scene = copy.deepcopy(old["community"])
        public = scene.pop("public_events", [])
        scene["description"] = self.describe(alias)
        scene["action_space"] = self.capabilities(alias)
        # Public/private events are separate from authoritative World snapshots.
        obs["content"] = {"scene": scene,
            "self": {"resources": old["own_resources"], **{k: copy.deepcopy(v) for k, v in old.items()
                if k.startswith("own_") and k not in ("own_resources", "own_action_result")}},
            "events": sorted([self._event_envelope(e) for e in public + old["private_events"]], key=lambda e: e["world_sequence"]),
            "action_result": copy.deepcopy(feedback), "event_cursors": old["event_cursors"],
            "decision_phase": scene.get("decision_phase", "action"),
            "decision_interface": copy.deepcopy(old.get("decision_interface", {})),
            "help_inbox": copy.deepcopy(old.get("help_inbox", {}))}
        return obs

    def interpret_open_action(self, request: dict, before: dict) -> dict:
        raise ActionRejected("open_action_not_supported_by_legacy_scene", {"hint": "Use LivingCommunityScene for open acts."})

    def termination(self) -> dict:
        return {"ended": self.state["round"] >= self.config["rounds"], "reason": "configured_round_limit"}

    def metrics(self, transaction: dict) -> dict:
        after = transaction["after"]
        return {"round": after["round"], "phase": transaction["phase"], "scope": "descriptive_not_behavioral_validation",
                "pool": after["pool"], "project_count": len(after["projects"]),
                "daily_outcomes": copy.deepcopy(after.get("last_daily_outcome", {})) if transaction["phase"] == "action" else {}}


class LegacyCommunityScene(SceneBridge, CommunityWorld):
    pass


class LivingCommunityScene(SceneBridge, LivingCommunityWorld):
    @classmethod
    def validate_config(cls, config: dict) -> None:
        validate_community_config(config)
        validate_living_config(config)
        require("thinking" in config["api"], "Record explicit api.thinking")
        if config.get("schema_version") == "social_framework_v1":
            options = config["scene"]["open_actions"]
            require(set(options) == {"enabled", "text_chars", "time_cost", "interpreter"}
                    and type(options["enabled"]) is bool and type(options["text_chars"]) is int
                    and 1 <= options["text_chars"] <= 1000 and type(options["time_cost"]) is int and options["time_cost"] == 1
                    and options["interpreter"] == "bounded_social_v1", "Invalid open-action configuration")

    @classmethod
    def phase_names(cls, config: dict) -> list[str]:
        return [f"communication_{n}" for n in range(1, config["daily_life"]["communication_passes"] + 1)] + ["action"]

    def capabilities(self, alias: str) -> dict:
        result = super().capabilities(alias)
        options = self.config.get("scene", {}).get("open_actions", {})
        if options.get("enabled"):
            result["OPEN"] = {"text": f"natural-language intention or exact utterance, 1..{options['text_chars']} chars",
                "mode": "auto / communicate / contact / negotiate / investigate / execute",
                "audience": "public / self / one resident alias; no implicit recipients",
                "subject": "investigate: facilities / market / projects / self; otherwise null",
                "operation": "execute: {action, arguments} from current phase; otherwise null",
                "semantics": "Social acts cost 1 time, deliver text but never consent or resources. Investigation reads only authorized phase-opening information. execute uses the normal action costs and checks. OPEN not nested in PLAN."}
        return result

    def check_preconditions(self, state: dict, request: dict, before: dict) -> None:
        super().check_preconditions(state, request, before)
        require(request["action"] in self.capabilities(request["actor_id"]), "action_not_allowed_in_phase")
        if request["action"] == "OPEN":
            interpretation = self.interpret_open_action(request, before)
            if interpretation["mode"] == "execute":
                grounded = {**request, **interpretation["operation"]}
                require(grounded["action"] in self.capabilities(request["actor_id"]), "grounded_action_not_allowed_in_phase")
                self._validate_visible_request(grounded, before)
        elif request["action"] != "PLAN":
            self._validate_visible_request(request, before)
        # Resource/time checks stay in atomic execution against the progressively
        # settled trial state. PLAN references remain per-step, never pre-rejected
        # as a whole because one child points at an unavailable object.

    def interpret_open_action(self, request: dict, before: dict) -> dict:
        args = request["arguments"]
        options = self.config.get("scene", {}).get("open_actions", {})
        require(options.get("enabled") is True, "open_actions_disabled")
        require(set(args) == {"text", "mode", "audience", "subject", "operation"}, "open_action_fields_required")
        text = args["text"]
        require(isinstance(text, str) and 0 < len(text.strip()) <= options["text_chars"], "open_text_length_invalid")
        mode = args["mode"]
        require(isinstance(mode, str) and mode in ("auto", "communicate", "contact", "negotiate", "investigate", "execute"), "invalid_open_mode")
        if mode == "auto":
            patterns = {"investigate": r"调查|查看|检查|inspect|investigate|examine",
                        "negotiate": r"协商|商量|谈判|negotiate|discuss terms",
                        "contact": r"联系|接触|邀请|contact|invite",
                        "communicate": r"告诉|询问|发言|讨论|tell|ask|say|discuss"}
            matches = [k for k, p in patterns.items() if re.search(p, text, re.I)]
            if len(matches) != 1:
                raise ActionRejected("open_intention_needs_clarification", {"interpreter": "bounded_social_v1",
                    "reason": "No unique supported interpretation; no action executed.",
                    "options": list(patterns) + ["execute with explicit operation"]})
            mode = matches[0]
        actor, audience = request["actor_id"], args["audience"]
        require(isinstance(audience, str) and audience in ["public", "self", *self.aliases], "invalid_open_audience")
        recipients = list(self.aliases) if audience == "public" else [actor] if audience in ("self", actor) else [actor, audience]
        if mode == "execute":
            operation = args["operation"]
            require(args["subject"] is None and audience == "self", "execute_requires_self_audit_audience_and_no_subject")
            require(isinstance(operation, dict) and set(operation) == {"action", "arguments"}
                    and isinstance(operation["action"], str) and isinstance(operation["arguments"], dict), "explicit_grounded_operation_required")
            require(operation["action"] not in ("OPEN", "PLAN"), "nested_open_or_plan_not_supported")
        else:
            require(args["operation"] is None, "social_act_cannot_execute_material_operation")
            if mode == "investigate":
                require(audience == "self" and args["subject"] in ("facilities", "market", "projects", "self"), "investigation_requires_authorized_subject_and_self_audience")
            else:
                require(args["subject"] is None, "unexpected_open_subject")
                require(audience != "self" and audience != actor, "social_contact_requires_other_recipient")
        return {"mode": mode, "recipients": recipients, "source_text": text,
                "basis_observation_id": request.get("basis_observation_id"), "rule": "bounded_social_v1",
                "operation": copy.deepcopy(args["operation"]), "subject": args["subject"]}

    def execute_action(self, state: dict, request: dict, before: dict) -> tuple[dict, list[str]]:
        if request["action"] != "OPEN":
            return super().execute_action(state, request, before)
        interpreted = self.interpret_open_action(request, before)
        actor, mode = request["actor_id"], interpreted["mode"]
        if mode == "execute":
            grounded = {**request, **interpreted["operation"]}
            effects, targets = super().execute_action(state, grounded, before)
            return {**effects, "interpretation": interpreted, "grounded_action": grounded["action"]}, targets
        self._cost_time(state, actor, self.config["scene"]["open_actions"]["time_cost"])
        event = {"type": "OPEN_SOCIAL", "action": "OPEN_SOCIAL", "round": state["round"], "phase": request["phase"],
                 "request_id": request["request_id"], "actor": actor, "mode": mode, "text": interpreted["source_text"],
                 "interpretation_rule": interpreted["rule"], "other_agent_consent": "not_implied"}
        if mode == "investigate":
            # Deliberately inspect BEFORE, never earlier actors' sealed choices/results.
            subject = interpreted["subject"]
            report = ({"credits": before["balances"][actor], "inventory": before["inventory"][actor],
                       "needs": before["needs"][actor], "health": before["health"][actor]}
                      if subject == "self" else before[subject])
            event.update(type="inspection_report", action="INSPECT", subject=subject, findings=copy.deepcopy(report),
                         information_basis="authorized_phase_opening_state", basis_world_version=before["version"])
        targets = interpreted["recipients"]
        if request["arguments"]["audience"] == "public":
            state["public_events"].append(event)
        else:
            self._private(state, targets, event)
        if mode != "investigate" and len(targets) == 2:
            self._relationship(state, actor, next(a for a in targets if a != actor), "initiated_" + mode)
        return {"interpretation": interpreted, "time_used": 1, "event_recorded": True,
                "outcome": "authorized_inspection" if mode == "investigate" else "utterance_delivered_no_agreement_or_transfer",
                "findings": copy.deepcopy(event.get("findings"))}, targets

    def rejection_options(self, request: dict, before: dict, reason: str) -> list[str]:
        if request["action"] == "OPEN":
            return ["Clarify mode and audience; separate a message from its requested material effect.",
                    "Use a current-phase structured operation for resource changes; only its validated effect will occur.",
                    "Revise, defer or abandon this intention; no automatic substitute will be executed."]
        return super().rejection_options(request, before, reason)
