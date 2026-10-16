"""Shared community resource/project kernel and GM; no Persona access or LLM calls."""
from __future__ import annotations

import copy

from common import require

ACTION_HELP = {
    "WAIT": {}, "SAY": {"text": "public message"}, "SEND": {"to": "resident alias", "text": "private message"},
    "TRANSFER": {"to": "resident alias", "amount": "positive integer credits"},
    "CONTRIBUTE": {"amount": "positive integer credits from own account to pool"},
    "WITHDRAW": {"amount": "positive integer credits from pool to own account"},
    "PROPOSE": {"template_id": "one of the published templates"},
    "VOTE": {"project_id": "published project id", "support": "boolean"},
    "WORK": {"project_id": "funded project id", "units": "positive integer labor"},
    "SET_BALANCE": {"note": "not permitted to residents; GM rejects"},
}


class ActionRejected(ValueError):
    """Actor-visible, structured rejection; details must contain only authorized facts."""
    def __init__(self, reason: str, details: dict):
        super().__init__(reason)
        self.details = copy.deepcopy(details)


class CommunityWorld:
    def __init__(self, aliases: list[str], config: dict):
        require(len(aliases) == 12 and len(set(aliases)) == 12, "World requires twelve unique residents")
        self.aliases = list(aliases)
        self.config = config
        self.rules = copy.deepcopy(config["world"])
        credits = self.rules["initial_personal_credits"]
        self.state = {"version": 0, "round": 0, "pool": self.rules["initial_pool"],
            "balances": {a: credits for a in aliases}, "labor": {a: 0 for a in aliases},
            "service_units": {a: 0 for a in aliases}, "common_need": self.rules["initial_common_need"],
            "projects": {}, "public_events": [], "private_events": {a: [] for a in aliases},
            "accounting": {"initial_total": credits * len(aliases) + self.rules["initial_pool"], "minted_rewards": 0, "spent_project_costs": 0}}

    def phases(self) -> list[str]:
        return ["action"]

    def _settle_phase(self, state: dict, phase: str) -> list[dict]:
        return self._resolve_projects(state)

    def _validate_visible_request(self, request: dict, before: dict) -> None:
        pass

    def _execute_request(self, state: dict, request: dict, before: dict) -> tuple[dict, list[str]]:
        self._validate_visible_request(request, before)
        return self._apply(state, request)

    def snapshot(self) -> dict:
        return copy.deepcopy(self.state)

    def begin_round(self, number: int) -> dict:
        require(number == self.state["round"] + 1, "Invalid next round")
        before = self.snapshot()
        self.state["round"] = number
        self.state["version"] += 1
        self.state["labor"] = {a: self.rules["labor_per_round"] for a in self.aliases}
        if number > 1:
            self.state["common_need"] += self.rules["need_growth_per_round"]
        return {"reason": "configured_round_boundary", "before": before, "after": self.snapshot()}

    def observe(self, alias: str, phase: str, feedback: dict | None = None, since: dict | None = None) -> dict:
        require(alias in self.aliases, "Unknown observer")
        s = self.state
        window = self.config["observation"]["public_history_window"]
        private_window = self.rules["private_history_window"]
        public_start = max(0, len(s["public_events"]) - window)
        private_start = max(0, len(s["private_events"][alias]) - private_window)
        if since is not None:
            require(type(since.get("public")) is int and 0 <= since["public"] <= len(s["public_events"])
                    and type(since.get("private")) is int and 0 <= since["private"] <= len(s["private_events"][alias]), "Invalid delivery cursor")
            # History windows may trim old context, never newly delivered messages.
            public_start = min(public_start, since["public"])
            private_start = min(private_start, since["private"])
        community = {"residents": self.aliases, "pool": s["pool"], "common_need": s["common_need"],
            "projects": copy.deepcopy(s["projects"]), "public_events": copy.deepcopy(s["public_events"][public_start:]),
            "rules": {"resource_origin": "scenario_assumption_not_WVS_income", "expectations": self.rules["public_expectations"],
                "withdrawal_expectation": self.rules["withdrawal_expectation"], "quorum": self.rules["quorum"],
                "approval_fraction_strictly_greater_than": self.rules["approval_fraction"],
                "voting": "After a round, pending projects reaching quorum and a strict majority are funded if pool suffices; funding order is creation order. Votes may change while pending.",
                "execution": "All choices are private until collected. GM resolves conflicts in the published seeded order; no automatic retry of a rejected action.",
                "labor_per_round": self.rules["labor_per_round"], "max_projects": self.rules["max_projects"],
                "max_open_per_proposer": self.rules["max_open_per_proposer"], "message_chars": self.rules["message_chars"],
                "private_history_window": private_window, "templates": self.rules["project_templates"]},
            "action_space": ACTION_HELP}
        content = {"community": community, "own_resources": {"credits": s["balances"][alias], "labor": s["labor"][alias],
                   "service_units": s["service_units"][alias]},
                   "private_events": copy.deepcopy(s["private_events"][alias][private_start:]),
                   "event_cursors": {"public": len(s["public_events"]), "private": len(s["private_events"][alias])},
                   "own_action_result": copy.deepcopy(feedback)}
        return {"observation_id": f"round_{s['round']}:{phase}:{alias}", "recipient": alias, "visibility": [alias],
                "round": s["round"], "world_version": s["version"], "phase": phase, "content": content}

    @staticmethod
    def _args(arguments: dict, keys: set[str]) -> None:
        require(type(arguments) is dict and set(arguments) == keys, "invalid_arguments")

    @staticmethod
    def _positive(value: object) -> None:
        require(type(value) is int and value > 0, "amount_or_units_must_be_positive_integer")

    def _text(self, value: object) -> None:
        require(isinstance(value, str) and 0 < len(value.strip()) <= self.rules["message_chars"], "invalid_message_length")

    def _apply(self, state: dict, request: dict) -> tuple[dict, list[str]]:
        actor, kind, args = request["actor_id"], request["action"], request["arguments"]
        event = {"request_id": request["request_id"], "round": state["round"], "actor": actor, "action": kind}
        effect: dict = {}
        affected = [actor]
        public = False
        if kind == "WAIT":
            self._args(args, set())
        elif kind in ("SAY", "SEND"):
            self._args(args, {"text"} if kind == "SAY" else {"text", "to"})
            self._text(args["text"])
            event["text"] = args["text"]
            effect["message_delivered"] = True
            if kind == "SAY":
                public, affected = True, list(self.aliases)
            else:
                require(args["to"] in self.aliases and args["to"] != actor, "invalid_private_recipient")
                event["to"] = args["to"]
                affected = [actor, args["to"]]
                for recipient in affected:
                    state["private_events"][recipient].append(copy.deepcopy(event))
        elif kind == "TRANSFER":
            self._args(args, {"to", "amount"}); self._positive(args["amount"])
            target, amount = args["to"], args["amount"]
            require(target in self.aliases and target != actor, "invalid_transfer_recipient")
            require(state["balances"][actor] >= amount, "insufficient_personal_credits")
            state["balances"][actor] -= amount; state["balances"][target] += amount
            effect = {"credits_delta": {actor: -amount, target: amount}}
            event.update({"to": target, "amount": amount})
            affected = [actor, target]
            for recipient in affected:
                state["private_events"][recipient].append(copy.deepcopy(event))
        elif kind in ("CONTRIBUTE", "WITHDRAW"):
            self._args(args, {"amount"}); self._positive(args["amount"])
            amount = args["amount"]
            if kind == "CONTRIBUTE":
                require(state["balances"][actor] >= amount, "insufficient_personal_credits")
                state["balances"][actor] -= amount; state["pool"] += amount
                effect = {"credits_delta": {actor: -amount}, "pool_delta": amount}
            else:
                require(state["pool"] >= amount, "insufficient_shared_pool_after_conflict_resolution")
                state["pool"] -= amount; state["balances"][actor] += amount
                effect = {"credits_delta": {actor: amount}, "pool_delta": -amount,
                          "exceeds_public_expectation": amount > self.rules["withdrawal_expectation"]}
                # Exceeding this soft expectation is NOT a permission failure.
            public, affected = True, list(self.aliases)
            event.update({"amount": amount, "effect": effect})
        elif kind == "PROPOSE":
            self._args(args, {"template_id"})
            template = next((t for t in self.rules["project_templates"] if t["id"] == args["template_id"]), None)
            require(template is not None, "unknown_project_template")
            require(len(state["projects"]) < self.rules["max_projects"], "project_capacity_reached")
            open_count = sum(p["proposer"] == actor and p["status"] != "completed" for p in state["projects"].values())
            require(open_count < self.rules["max_open_per_proposer"], "proposer_open_project_limit")
            project_id = f"project_{len(state['projects']) + 1:03d}"
            state["projects"][project_id] = {"id": project_id, "template": copy.deepcopy(template), "proposer": actor,
                "created_round": state["round"], "status": "pending", "votes": {}, "contributors": {}, "work_done": 0}
            effect = {"project_created": project_id}
            event["project_id"] = project_id
            public, affected = True, list(self.aliases)
        elif kind == "VOTE":
            self._args(args, {"project_id", "support"})
            require(isinstance(args["project_id"], str), "invalid_project_id")
            require(type(args["support"]) is bool, "vote_support_must_be_boolean")
            project = state["projects"].get(args["project_id"])
            require(project is not None and project["status"] == "pending", "project_not_open_for_vote")
            project["votes"][actor] = args["support"]
            effect = {"project_id": project["id"], "recorded_vote": args["support"]}
            event.update(effect)
            public, affected = True, list(self.aliases)
        elif kind == "WORK":
            self._args(args, {"project_id", "units"}); self._positive(args["units"])
            require(isinstance(args["project_id"], str), "invalid_project_id")
            project = state["projects"].get(args["project_id"])
            require(project is not None and project["status"] == "funded", "project_not_funded_or_already_complete")
            units = args["units"]
            require(state["labor"][actor] >= units, "insufficient_round_labor")
            require(units <= project["template"]["labor_required"] - project["work_done"], "work_exceeds_remaining_requirement")
            state["labor"][actor] -= units; project["work_done"] += units
            project["contributors"][actor] = project["contributors"].get(actor, 0) + units
            effect = {"project_id": project["id"], "labor_used": units, "progress_delta": units}
            event.update(effect)
            public, affected = True, list(self.aliases)
        elif kind == "SET_BALANCE":
            raise ValueError("permission_denied_resident_cannot_set_balances")
        else:
            raise ValueError("unknown_action")
        if public:
            state["public_events"].append(event)
        return effect, affected

    def _resolve_projects(self, state: dict) -> list[dict]:
        effects = []
        for project in state["projects"].values():
            template = project["template"]
            votes = list(project["votes"].values())
            if project["status"] == "pending" and len(votes) >= self.rules["quorum"] and sum(votes) > len(votes) * self.rules["approval_fraction"]:
                if state["pool"] >= template["cost"]:
                    state["pool"] -= template["cost"]
                    state["accounting"]["spent_project_costs"] += template["cost"]
                    project["status"] = "funded"
                    effects.append({"type": "project_funded", "project_id": project["id"], "pool_delta": -template["cost"], "affected_agents": self.aliases})
                else:
                    effects.append({"type": "project_funding_deferred", "project_id": project["id"], "reason": "insufficient_shared_pool", "affected_agents": self.aliases})
            if project["status"] == "funded" and project["work_done"] == template["labor_required"]:
                project["status"] = "completed"
                need_before = state["common_need"]
                state["common_need"] = max(0, need_before - template["need_reduction"])
                for alias in self.aliases:
                    state["balances"][alias] += template["credit_reward_each"]
                    state["service_units"][alias] += template["service_units_each"]
                state["accounting"]["minted_rewards"] += template["credit_reward_each"] * len(self.aliases)
                effects.append({"type": "project_completed", "project_id": project["id"], "common_need_delta": state["common_need"] - need_before,
                    "credit_reward_each": template["credit_reward_each"], "service_units_each": template["service_units_each"],
                    "affected_agents": self.aliases, "basis": "scenario_reward_not_inferred_human_welfare"})
        for effect in effects:
            state["public_events"].append({"round": state["round"], **copy.deepcopy(effect)})
        return effects

    def assert_accounting(self, state: dict) -> None:
        accounting = state["accounting"]
        require(all(type(v) is int and v >= 0 for v in state["balances"].values()) and state["pool"] >= 0, "Negative or invalid resources")
        require(sum(state["balances"].values()) + state["pool"] + accounting["spent_project_costs"] == accounting["initial_total"] + accounting["minted_rewards"], "Resource conservation failed")

    def commit_round(self, requests: list[dict], order: list[str], phase: str = "action") -> dict:
        # Backward-compatible entry for existing fixtures; no second transaction engine.
        from gm import GM
        if not hasattr(self, "_gm"):
            self._gm = GM(self)
        return self._gm.commit(requests, order, phase)

    def validate_phase(self, phase: str) -> None:
        require(phase in self.phases(), "Unknown execution phase")
        if "next_phase" in self.state:
            require(self.state["next_phase"] == phase, "Out-of-order phase")

    def check_preconditions(self, state: dict, request: dict, before: dict) -> None:
        # Detailed resource/reference checks remain atomic in _execute_request.
        require(isinstance(request.get("action"), str) and isinstance(request.get("arguments"), dict), "invalid_action_schema")

    def execute_action(self, state: dict, request: dict, before: dict) -> tuple[dict, list[str]]:
        return self._execute_request(state, request, before)

    def settle_phase(self, state: dict, phase: str) -> list[dict]:
        return self._settle_phase(state, phase)

    def assert_invariants(self, state: dict) -> None:
        self.assert_accounting(state)

    def collect_world_events(self, before: dict, after: dict, phase: str) -> list[dict]:
        return []  # Legacy direct-kernel callers; SceneBridge supplies event envelopes.

    def rejection_options(self, request: dict, before: dict, reason: str) -> list[str]:
        return ["Review the delivered action rules and rejection details; revise or abandon the request."]
