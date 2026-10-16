"""Deterministic daily-life kernel; all needs/health/relationships are experiment state.

Reuses the existing GM transaction engine. No Persona inference, network or model calls.
Private material outcomes never enter the public system-effects list.
"""
from __future__ import annotations

import copy

from common import require
from community_world import ACTION_HELP, ActionRejected, CommunityWorld

FACILITIES = ("shared_pantry", "water_maintenance", "community_workspace")
GOODS = ("food", "water")
ASSETS = ("credits", "food", "water")
INTERFACE_REVISION = "community_interaction_repair_v1"
COMMUNICATION_HELP = {
    k: ACTION_HELP[k] for k in ("WAIT", "SAY", "SEND", "PROPOSE", "VOTE", "SET_BALANCE")
}
COMMUNICATION_HELP.update({
    "ASK_HELP": {"to": "public or resident alias", "text": "request; not an automatic transfer"},
    "RESPOND": {"help_id": "visible help id", "text": "response; no binding resource promise"},
    "UPDATE_HELP": {"help_id": "your open help id", "text": "replacement request text; same audience; renews expiry"},
    "CLOSE_HELP": {"help_id": "your open help id", "status": "resolved or withdrawn; requester declaration only"},
    "OFFER_TRADE": {"to": "resident alias", "give": {"asset": "credits/food/water", "amount": "positive integer"},
                    "want": {"asset": "credits/food/water", "amount": "positive integer"}},
    "CANCEL_TRADE": {"offer_id": "own open offer id"},
})
MATERIAL_HELP = {k: ACTION_HELP[k] for k in ("WAIT", "TRANSFER", "CONTRIBUTE", "WITHDRAW", "WORK", "SET_BALANCE")}
MATERIAL_HELP.update({
    "BUY": {"food": "nonnegative integer; 0 means do not buy food", "water": "nonnegative integer; 0 means do not buy water; positive total; atomic bundle"},
    "FETCH": {"food": "nonnegative integer; 0 means do not fetch food", "water": "nonnegative integer; 0 means do not fetch water; positive total; atomic bundle"},
    "GIVE": {"to": "resident alias", "asset": "food/water/credits", "amount": "positive integer"},
    "ACCEPT_TRADE": {"offer_id": "open offer addressed to you"},
    "EARN": {"units": "positive integer work units"},
    "REST": {"units": "positive integer rest units"},
    "MAINTAIN": {"facility_id": "published facility id", "units": "positive integer labor"},
    **{k: COMMUNICATION_HELP[k] for k in ("RESPOND", "UPDATE_HELP", "CLOSE_HELP")},
})


def validate_living_config(config: dict) -> None:
    """Fail before dispatch; numbers are engineering assumptions, not measured traits."""
    life = config["daily_life"]
    expected = {"origin", "communication_passes", "max_action_steps", "time_per_round", "initial_inventory",
                "need_per_round", "initial_health", "max_health", "max_deficit", "damage_threshold",
                "damage_per_round", "recovery_per_round", "impaired_health_below", "impaired_time_penalty",
                "rest_health_per_unit", "base_wage", "workspace_bonus", "maintenance_cost_per_unit",
                "repair_per_unit", "offer_ttl_rounds", "max_open_offers_per_resident", "max_open_help_per_resident",
                "help_ttl_rounds", "market", "facilities", "events"}
    require(set(life) == expected, "Unsupported/missing daily_life fields")
    require(life["origin"] == "experiment_assumptions_not_persona_evidence", "Life conditions must declare experiment origin")
    for key in expected - {"origin", "initial_inventory", "need_per_round", "market", "facilities", "events"}:
        require(type(life[key]) is int and life[key] >= 0, f"Invalid daily_life.{key}")
    require(1 <= life["communication_passes"] <= 3 and 1 <= life["max_action_steps"] <= 4, "Invalid phase/plan limits")
    require(life["time_per_round"] > life["communication_passes"], "Need time left after communications")
    require(0 < life["initial_health"] <= life["max_health"] and life["max_deficit"] > 0, "Invalid life bounds")
    require(0 < life["damage_threshold"] <= life["max_deficit"] and life["damage_per_round"] > 0, "Invalid deficit consequences")
    require(life["impaired_health_below"] <= life["max_health"] and life["impaired_time_penalty"] < life["time_per_round"], "Invalid impairment")
    for key in ("offer_ttl_rounds", "help_ttl_rounds", "max_open_offers_per_resident", "max_open_help_per_resident", "repair_per_unit"):
        require(life[key] > 0, f"daily_life.{key} must be positive")
    for key in ("initial_inventory", "need_per_round"):
        require(set(life[key]) == set(GOODS), f"Invalid {key} goods")
        require(all(type(v) is int and v >= (1 if key == "need_per_round" else 0) for v in life[key].values()), f"Invalid {key} amount")
    market = life["market"]
    require(set(market) == {"initial_stock", "restock_per_round", "stock_cap", "prices"}, "Invalid market fields")
    for key, values in market.items():
        require(set(values) == set(GOODS) and all(type(v) is int and v >= 0 for v in values.values()), f"Invalid market.{key}")
    require(all(market["prices"][g] > 0 and market["initial_stock"][g] <= market["stock_cap"][g] for g in GOODS), "Invalid market prices/caps")
    require(set(life["facilities"]) == set(FACILITIES), "Missing facilities")
    for name, f in life["facilities"].items():
        require(set(f) == {"condition", "max_condition", "wear_every_rounds", "wear_units", "daily_capacity", "stock_cap", "initial_stock"}, f"Invalid facility {name}")
        require(all(type(v) is int and v >= 0 for v in f.values()), f"Invalid facility numbers {name}")
        require(f["max_condition"] > 0 and f["condition"] <= f["max_condition"] and f["wear_every_rounds"] > 0 and f["initial_stock"] <= f["stock_cap"], "Invalid facility bounds")
    for event in life["events"]:
        require(set(event) == {"round", "kind", "target", "amount"}, "Invalid environment event fields")
        require(type(event["round"]) is int and event["round"] > 0 and type(event["amount"]) is int and event["amount"] > 0, "Invalid event amount/time")
        require((event["kind"] == "facility_damage" and event["target"] in FACILITIES)
                or (event["kind"] == "market_loss" and event["target"] in GOODS), "Unsupported environment event")
    require({t["id"] for t in config["world"]["project_templates"]} == set(FACILITIES), "Living projects must map to the three facilities")


class LivingCommunityWorld(CommunityWorld):
    def __init__(self, aliases: list[str], config: dict):
        super().__init__(aliases, config)
        self.life = copy.deepcopy(config["daily_life"])
        d, s = self.life, self.state
        s.update({"kernel": "community_daily_life_v1", "next_phase": "round_boundary",
                  "experiment_origin": d["origin"], "scenario_role": "resident_for_all_not_WVS_occupation",
                  "inventory": {a: copy.deepcopy(d["initial_inventory"]) for a in aliases},
                  "needs": {a: {g: 0 for g in GOODS} for a in aliases},
                  "health": {a: d["initial_health"] for a in aliases}, "time": {a: 0 for a in aliases},
                  "market": copy.deepcopy(d["market"]["initial_stock"]), "market_revenue": 0,
                  "facilities": {k: {"condition": f["condition"], "stock": f["initial_stock"]} for k, f in d["facilities"].items()},
                  "work_slots": 0, "offers": {}, "help_requests": {},
                  "relations": {a: {} for a in aliases}, "last_daily_outcome": {a: None for a in aliases},
                  "goods_ledger": {g: {"initial": d["initial_inventory"][g] * 12 + d["market"]["initial_stock"][g]
                                      + d["facilities"]["shared_pantry" if g == "food" else "water_maintenance"]["initial_stock"],
                                      "created": 0, "consumed": 0, "lost": 0} for g in GOODS}})
        s["accounting"].update({"spent_maintenance_costs": 0, "minted_wages": 0})
        s["interface_revision"] = INTERFACE_REVISION

    def phases(self) -> list[str]:
        return [f"communication_{n}" for n in range(1, self.life["communication_passes"] + 1)] + ["action"]

    def _private(self, state: dict, participants: list[str], event: dict) -> None:
        for alias in set(participants):
            state["private_events"][alias].append(copy.deepcopy({"round": state["round"], **event}))

    def _relationship(self, state: dict, actor: str, target: str, kind: str) -> None:
        # Symmetric interaction counts, NOT inferred trust, friendship or personality.
        for a, b in ((actor, target), (target, actor)):
            entry = state["relations"][a].setdefault(b, {"counts": {}, "last_round": 0})
            entry["counts"][kind] = entry["counts"].get(kind, 0) + 1
            entry["last_round"] = state["round"]

    def begin_round(self, number: int) -> dict:
        require(self.state["next_phase"] in ("round_boundary", "settled"), "Previous round incomplete")
        transition = super().begin_round(number)
        s, d = self.state, self.life
        s["next_phase"] = self.phases()[0]
        public = []
        for name, f in s["facilities"].items():
            rule = d["facilities"][name]
            if number > 1 and number % rule["wear_every_rounds"] == 0:
                before = f["condition"]
                f["condition"] = max(0, before - rule["wear_units"])
                public.append({"type": "facility_wear", "facility": name, "delta": f["condition"] - before})
        for event in d["events"]:
            if event["round"] != number:
                continue
            if event["kind"] == "facility_damage":
                f = s["facilities"][event["target"]]
                before = f["condition"]
                f["condition"] = max(0, before - event["amount"])
                actual = before - f["condition"]
            else:
                good = event["target"]
                actual = min(s["market"][good], event["amount"])
                s["market"][good] -= actual
                s["goods_ledger"][good]["lost"] += actual
            public.append({"type": "environment_event", "kind": event["kind"], "target": event["target"], "actual_loss": actual})
        for good in GOODS:
            if number > 1:
                added = min(d["market"]["restock_per_round"][good], d["market"]["stock_cap"][good] - s["market"][good])
                s["market"][good] += added
                s["goods_ledger"][good]["created"] += added
                public.append({"type": "market_restock", "good": good, "amount": added})
            name = "shared_pantry" if good == "food" else "water_maintenance"
            rule, f = d["facilities"][name], s["facilities"][name]
            output = rule["daily_capacity"] * f["condition"] // rule["max_condition"]
            added = min(output, rule["stock_cap"] - f["stock"])
            f["stock"] += added
            s["goods_ledger"][good]["created"] += added
            public.append({"type": "facility_production", "facility": name, "good": good, "amount": added})
        w = d["facilities"]["community_workspace"]
        s["work_slots"] = w["daily_capacity"] * s["facilities"]["community_workspace"]["condition"] // w["max_condition"]
        for a in self.aliases:
            s["time"][a] = max(1, d["time_per_round"] - (d["impaired_time_penalty"] if s["health"][a] < d["impaired_health_below"] else 0))
            s["labor"][a] = min(self.rules["labor_per_round"], s["time"][a])
            for g in GOODS:
                s["needs"][a][g] = min(d["max_deficit"], s["needs"][a][g] + d["need_per_round"][g])
        for offer in s["offers"].values():
            if offer["status"] == "open" and number > offer["expires_round"]:
                offer["status"] = "expired"
                self._private(s, [offer["from"], offer["to"]], {"type": "trade_expired", "offer_id": offer["id"]})
        for help_item in s["help_requests"].values():
            if help_item["status"] == "open" and number > help_item["expires_round"]:
                help_item["status"] = "expired"
        s["public_events"].extend({"round": number, **e} for e in public)
        self.assert_accounting(s)
        transition.update({"after": self.snapshot(), "system_effects": public, "reason": "daily_life_environment_update"})
        return transition

    def observe(self, alias: str, phase: str, feedback: dict | None = None, since: dict | None = None) -> dict:
        obs = super().observe(alias, phase, feedback, since)
        s, d, content = self.state, self.life, obs["content"]
        active = s["next_phase"]
        comm = active.startswith("communication_")
        content["community"].update({"decision_phase": active, "facilities": copy.deepcopy(s["facilities"]),
            "market": copy.deepcopy(s["market"]), "workspace_bonus_slots": s["work_slots"],
            "help_requests": {k: copy.deepcopy(v) for k, v in s["help_requests"].items() if v["status"] == "open" and v["to"] == "public"},
            "action_space": copy.deepcopy(COMMUNICATION_HELP if comm else MATERIAL_HELP)})
        if not comm:
            content["community"]["action_space"]["PLAN"] = {"steps": "list of {action, arguments}; at most " + str(d["max_action_steps"]) + "; only actions listed for this phase, no nested PLAN"}
        # Known current mechanics are public; future scheduled events remain hidden.
        content["community"]["rules"]["daily_life"] = {k: copy.deepcopy(v) for k, v in d.items() if k not in ("events", "initial_inventory", "initial_health")}
        content["community"]["rules"].update({
            "interface_revision": INTERFACE_REVISION,
            "help_lifecycle": "ASK_HELP creates a new request. UPDATE_HELP edits your open request without changing audience and renews expiry. CLOSE_HELP is the requester's resolved/withdrawn declaration, not verified aid. RESPOND is private text, never automatic material help. These last three actions also work in the action phase/PLAN, costing 1 time each.",
            "bundle_execution": "BUY/FETCH accept food-only (water=0), water-only (food=0), or both. All requested quantities must be available; no automatic partial fill or purchase. Current stock is not reserved for you.",
            "plan_failure": "Each structurally valid PLAN step checks references against the SAME phase-opening observation. A rejected step does not cancel other steps. No unseen object may be used, even if created earlier in this phase.",
            "history_delivery": "Recent-history windows bound older context; new public events and your new private events since last delivery are included in full. Full delivered episodes are archived locally.",
            "legacy_counters": "common_need is a public agenda counter, not the sum of private food/water deficits; service_units are legacy project counters, not automatic food or water deliveries.",
            "execution": "Each phase: all choices sealed before GM; published seeded order resolves resource conflicts. Prior phases persist if later phase fails.",
            "voting": "Projects resolve after each phase. New projects are visible only after phase feedback; project completion restores its facility to maximum condition; production/bonus slots refresh next round.",
            "life_timing": "Round opening adds needs, applies wear/events and supply. Communication costs one time except WAIT; action phase allows one action or bounded PLAN. At action-phase end stored food/water is automatically consumed up to deficits, then unmet deficits affect health. WAIT is legal; no food is bought/fetched automatically.",
            "material_costs": "BUY/FETCH/GIVE/TRANSFER/CONTRIBUTE/WITHDRAW/ACCEPT_TRADE cost 1 time. WORK/EARN/MAINTAIN use units time and labor; REST uses units time. WAIT costs 0. Invalid steps cost 0; earlier accepted PLAN steps are not rolled back by later rejection.",
            "trade": "Offers private, no escrow. Recipient acceptance atomically exchanges assets if BOTH still have them; rejected acceptance leaves offer open. Expiry/cancellation ends offer. Messages are not enforceable contracts.",
            "health": "Abstract experiment variable, not medical evidence; at zero residents remain active with reduced time; no death model.",
            "relationships": "Private pairwise counts of actual interactions, not inferred trust. Public messages/project actions stay public; inventory/health/trades stay private.",
            "maintenance": "MAINTAIN costs own credits and labor and raises facility condition; stock/bonus production changes next round. FETCH may use remaining stock even if condition is zero.",
            "earn": "EARN creates configured external wage credits; scarce workspace slots add bonus, otherwise base wage remains available."})
        content["own_resources"].update({"origin": d["origin"], "scenario_role": "resident",
            "inventory": copy.deepcopy(s["inventory"][alias]), "unmet_needs": copy.deepcopy(s["needs"][alias]),
            "health": s["health"][alias], "time_remaining": s["time"][alias]})
        content["own_relations"] = copy.deepcopy(s["relations"][alias])
        content["own_help_requests"] = {k: copy.deepcopy(v) for k, v in s["help_requests"].items()
                                       if v["status"] == "open" and v["to"] != "public" and alias in (v["from"], v["to"])}
        content["own_offers"] = {k: copy.deepcopy(v) for k, v in s["offers"].items() if v["status"] == "open" and alias in (v["from"], v["to"])}
        content["own_daily_outcome"] = copy.deepcopy(s["last_daily_outcome"][alias])
        # This interface is a deterministic rendering of this recipient's observation,
        # not privileged access, a recommended policy, or a promise of execution.
        self._add_decision_interface(content, alias)
        return obs

    @staticmethod
    def _add_decision_interface(content: dict, alias: str) -> None:
        community, own = content["community"], content["own_resources"]
        rules = community["rules"]
        templates = rules.pop("templates")
        community["proposal_templates"] = {t["id"]: {**t, "id_kind": "template_id_not_project_id", "entry_action": "PROPOSE"} for t in templates}
        board = {"pending_vote_or_funding": {}, "funded_workable": {}, "completed": {}}
        for identity, p in community["projects"].items():
            votes = list(p["votes"].values())
            row = {"project_id": identity, "template_id": p["template"]["id"], "status": p["status"],
                   "vote_count": len(votes), "yes_count": sum(votes), "quorum": rules["quorum"],
                   "approval_fraction_strictly_greater_than": rules["approval_fraction_strictly_greater_than"],
                   "cost": p["template"]["cost"], "pool_covers_cost_now": community["pool"] >= p["template"]["cost"],
                   "remaining_labor": p["template"]["labor_required"] - p["work_done"],
                   "my_vote": p["votes"].get(alias)}
            board[{"pending": "pending_vote_or_funding", "funded": "funded_workable", "completed": "completed"}[p["status"]]][identity] = row
        community["project_board"] = board
        community["project_workflow"] = {
            "PROPOSE": "Use template_id from proposal_templates during communication; GM creates a distinct project_id.",
            "VOTE": "Use an observed pending project_id during communication. Quorum plus strict approval threshold plus pool funds are required for funding.",
            "CONTRIBUTE": "Optional action-phase payment into pool; does not cast a vote or automatically create a project.",
            "WORK": "Use an observed funded project_id during action; time/labor and remaining work constrain units.",
            "MAINTAIN": "Direct repair using facility_id; NO project, proposal or vote required. Own credits/time/labor required."}
        life = rules["daily_life"]
        content["decision_interface"] = {
            "basis": "this_delivered_observation_only_not_recommendations", "time_remaining": own["time_remaining"],
            "labor_remaining": own["labor"], "message_chars_max": rules["message_chars"],
            "stock_not_reserved": True,
            "FETCH_stock": {"food": community["facilities"]["shared_pantry"]["stock"], "water": community["facilities"]["water_maintenance"]["stock"]},
            "BUY_stock": copy.deepcopy(community["market"]), "BUY_prices": copy.deepcopy(life["market"]["prices"]),
            "single_item_syntax": {"food_only": {"food": 1, "water": 0}, "water_only": {"food": 0, "water": 1}},
            "maintenance": {k: {"condition": f["condition"], "max_condition": life["facilities"][k]["max_condition"],
                               "credits_per_unit": life["maintenance_cost_per_unit"], "repair_per_unit": life["repair_per_unit"],
                               "requires_project": False} for k, f in community["facilities"].items()},
        }
        visible_help = {**community["help_requests"], **content["own_help_requests"]}
        content["help_inbox"] = {
            "own_open": {k: copy.deepcopy(v) for k, v in visible_help.items() if v["from"] == alias},
            "others_visible_open": {k: copy.deepcopy(v) for k, v in visible_help.items() if v["from"] != alias},
            "max_own_open": life["max_open_help_per_resident"],
            "controls": "Own: UPDATE_HELP/CLOSE_HELP. Others: RESPOND; GIVE/OFFER_TRADE use requester alias if you choose. Text is a participant claim, not verified need. Material help remains a separate action; no obligation."}

    def _text(self, value: object) -> None:
        if not isinstance(value, str) or not 0 < len(value.strip()) <= self.rules["message_chars"]:
            raise ActionRejected("invalid_message_length", {"max_chars": self.rules["message_chars"],
                "actual_chars": len(value.strip()) if isinstance(value, str) else None, "automatic_truncation": False})

    def _cost_time(self, state: dict, actor: str, units: int, labor: bool = False) -> None:
        require(state["time"][actor] >= units, "insufficient_daily_time")
        if labor:
            require(state["labor"][actor] >= units, "insufficient_daily_labor")
            state["labor"][actor] -= units
        state["time"][actor] -= units

    def _recipient(self, actor: str, target: object) -> None:
        require(isinstance(target, str) and target in self.aliases and target != actor, "invalid_recipient")

    def _asset_amount(self, state: dict, actor: str, asset: str) -> int:
        return state["balances"][actor] if asset == "credits" else state["inventory"][actor][asset]

    def _move(self, state: dict, actor: str, target: str, asset: str, amount: int) -> None:
        require(isinstance(asset, str) and asset in ASSETS, "invalid_asset")
        self._positive(amount)
        require(self._asset_amount(state, actor, asset) >= amount, "insufficient_asset")
        if asset == "credits":
            state["balances"][actor] -= amount
            state["balances"][target] += amount
        else:
            state["inventory"][actor][asset] -= amount
            state["inventory"][target][asset] += amount

    def _execute_request(self, state: dict, request: dict, before: dict) -> tuple[dict, list[str]]:
        if request["action"] != "PLAN":
            self._validate_visible_request(request, before)
        return self._apply(state, request, visible_before=before)

    def _apply(self, state: dict, request: dict, visible_before: dict | None = None) -> tuple[dict, list[str]]:
        kind, args, actor = request["action"], request["arguments"], request["actor_id"]
        comm = request["phase"].startswith("communication_")
        if kind != "PLAN":
            require(kind in (COMMUNICATION_HELP if comm else MATERIAL_HELP), "action_not_allowed_in_phase")
            return self._single(state, request)
        require(not comm, "PLAN_not_allowed_in_communication")
        require(visible_before is not None, "PLAN_requires_phase_opening_snapshot")
        self._args(args, {"steps"})
        require(isinstance(args["steps"], list) and 1 <= len(args["steps"]) <= self.life["max_action_steps"], "invalid_plan_length")
        require(all(isinstance(x, dict) and set(x) == {"action", "arguments"} and isinstance(x["action"], str) and isinstance(x["arguments"], dict) for x in args["steps"]), "invalid_plan_schema")
        results, affected = [], {actor}
        for i, step in enumerate(args["steps"], 1):
            child = {**request, **step, "request_id": f"{request['request_id']}:step{i}"}
            trial = copy.deepcopy(state)
            result = {"request_id": child["request_id"], "action": step["action"], "arguments": copy.deepcopy(step["arguments"]), "accepted": False, "effects": {}}
            try:
                require(step["action"] in MATERIAL_HELP, "action_not_allowed_in_plan")
                self._validate_visible_request(child, visible_before)
                effect, targets = self._single(trial, child)
            except ValueError as exc:
                result["rejection_reason"] = str(exc)
                result["rejection_details"] = copy.deepcopy(getattr(exc, "details", {}))
            else:
                try:
                    self.assert_accounting(trial)
                except ValueError as exc:
                    raise RuntimeError("PLAN internal conservation failure") from exc
                state.clear()
                state.update(trial)
                result.update({"accepted": True, "effects": effect, "affected_agents": targets})
                affected.update(targets)
            results.append(result)
        return {"steps": results, "semantics": "bounded_sequential_partial_success"}, sorted(affected)

    def _validate_visible_request(self, request: dict, before: dict) -> None:
        # Never allow guessing an object created by an earlier actor in this same phase.
        # Validate against the phase-opening snapshot, not sequentially mutated World.
        actor = request["actor_id"]
        steps = request["arguments"].get("steps", []) if request["action"] == "PLAN" else [request]
        require(isinstance(steps, list), "invalid_plan_steps")
        for step in steps:
            require(isinstance(step, dict) and isinstance(step.get("arguments"), dict), "invalid_action_arguments")
            args = step["arguments"]
            kind = step.get("action")
            if kind in ("WORK", "VOTE"):
                identity = args.get("project_id")
                if not isinstance(identity, str) or identity not in before["projects"]:
                    raise ActionRejected("project_not_in_phase_observation", {
                        "requested_project_id": identity, "observed_project_ids": list(before["projects"]),
                        "template_ids_are_not_project_ids": True,
                        "workflow": "PROPOSE(template_id) -> observed pending project_id -> VOTE -> funded project_id -> WORK; direct facility repair uses MAINTAIN(facility_id)."})
            elif kind in ("ACCEPT_TRADE", "CANCEL_TRADE"):
                identity = args.get("offer_id")
                require(isinstance(identity, str), "invalid_offer_id")
                offer = before["offers"].get(identity)
                require(offer is not None and offer["status"] == "open" and actor in (offer["from"], offer["to"]), "offer_not_in_phase_observation")
            elif kind in ("RESPOND", "UPDATE_HELP", "CLOSE_HELP"):
                identity = args.get("help_id")
                require(isinstance(identity, str), "invalid_help_id")
                help_item = before["help_requests"].get(identity)
                require(help_item is not None and help_item["status"] == "open" and (help_item["to"] == "public" or actor in (help_item["from"], help_item["to"])), "help_not_in_phase_observation")

    def _single(self, state: dict, request: dict) -> tuple[dict, list[str]]:
        actor, kind, args = request["actor_id"], request["action"], request["arguments"]
        d = self.life
        comm = request["phase"].startswith("communication_")
        event = {"type": kind, "request_id": request["request_id"], "actor": actor, "phase": request["phase"]}
        if kind in ACTION_HELP:
            if kind != "WAIT":
                units = args.get("units", 1) if kind == "WORK" else 1
                self._positive(units)
                self._cost_time(state, actor, units)
            effect, targets = super()._apply(state, request)
            if kind in ("SEND", "TRANSFER"):
                self._relationship(state, actor, args["to"], kind)
            if kind == "WORK":
                project = state["projects"][args["project_id"]]
                for partner in project["contributors"]:
                    if partner != actor:
                        self._relationship(state, actor, partner, "shared_project_work")
            return {**effect, "time_used": 0 if kind == "WAIT" else units}, targets
        if comm or kind in ("RESPOND", "UPDATE_HELP", "CLOSE_HELP"):
            self._cost_time(state, actor, 1)
            if kind == "ASK_HELP":
                self._args(args, {"to", "text"}); self._text(args["text"])
                if args["to"] != "public":
                    self._recipient(actor, args["to"])
                own_open = [k for k, v in state["help_requests"].items() if v["from"] == actor and v["status"] == "open"]
                if len(own_open) >= d["max_open_help_per_resident"]:
                    raise ActionRejected("open_help_limit", {"own_open_help_ids": own_open,
                        "limit": d["max_open_help_per_resident"], "available_controls": ["UPDATE_HELP", "CLOSE_HELP"],
                        "automatic_replacement": False})
                identity = request["request_id"]
                item = {"id": identity, "from": actor, **args, "status": "open", "created_round": state["round"], "expires_round": state["round"] + d["help_ttl_rounds"]}
                state["help_requests"][identity] = item
                event.update(item)
                targets = self.aliases if args["to"] == "public" else [actor, args["to"]]
                if args["to"] == "public":
                    state["public_events"].append({"round": state["round"], **event})
                else:
                    self._private(state, targets, event)
                    self._relationship(state, actor, args["to"], kind)
                return {"help_id": identity, "time_used": 1}, list(targets)
            if kind in ("UPDATE_HELP", "CLOSE_HELP"):
                self._args(args, {"help_id", "text"} if kind == "UPDATE_HELP" else {"help_id", "status"})
                require(isinstance(args["help_id"], str), "invalid_help_id")
                item = state["help_requests"].get(args["help_id"])
                require(item is not None and item["status"] == "open" and item["from"] == actor, "help_not_owned_or_open")
                if kind == "UPDATE_HELP":
                    self._text(args["text"])
                    item["text"] = args["text"]
                    item["updated_round"] = state["round"]
                    item["expires_round"] = state["round"] + d["help_ttl_rounds"]
                else:
                    require(args["status"] in ("resolved", "withdrawn"), "invalid_help_closure_status")
                    item["status"] = args["status"]
                    item["closed_round"] = state["round"]
                    item["closure_basis"] = "requester_declaration_not_verified_material_fulfillment"
                targets = list(self.aliases) if item["to"] == "public" else [actor, item["to"]]
                update_event = {**event, "help_id": item["id"], "help": copy.deepcopy(item)}
                if item["to"] == "public":
                    state["public_events"].append({"round": state["round"], **update_event})
                else:
                    self._private(state, targets, update_event)
                return {"help_id": item["id"], "status": item["status"], "expires_round": item["expires_round"], "time_used": 1}, targets
            if kind == "RESPOND":
                self._args(args, {"help_id", "text"}); self._text(args["text"])
                require(isinstance(args["help_id"], str), "invalid_help_id")
                help_item = state["help_requests"].get(args["help_id"])
                require(help_item is not None and help_item["status"] == "open", "help_not_open")
                require(help_item["from"] != actor and help_item["to"] in ("public", actor), "help_not_visible_to_actor")
                target = help_item["from"]
                event.update(args)
                self._private(state, [actor, target], event)
                self._relationship(state, actor, target, kind)
                return {"response_delivered": True, "help_id": args["help_id"], "time_used": 1}, [actor, target]
            if kind == "OFFER_TRADE":
                self._args(args, {"to", "give", "want"}); self._recipient(actor, args["to"])
                for item in (args["give"], args["want"]):
                    self._args(item, {"asset", "amount"})
                    require(isinstance(item["asset"], str) and item["asset"] in ASSETS, "invalid_trade_asset")
                    self._positive(item["amount"])
                require(args["give"]["asset"] != args["want"]["asset"], "same_asset_trade_not_supported")
                require(self._asset_amount(state, actor, args["give"]["asset"]) >= args["give"]["amount"], "insufficient_offered_asset")
                require(sum(o["from"] == actor and o["status"] == "open" for o in state["offers"].values()) < d["max_open_offers_per_resident"], "open_offer_limit")
                identity = request["request_id"]
                offer = {"id": identity, "from": actor, **copy.deepcopy(args), "status": "open", "expires_round": state["round"] + d["offer_ttl_rounds"]}
                state["offers"][identity] = offer
                self._private(state, [actor, args["to"]], {**event, **offer})
                self._relationship(state, actor, args["to"], kind)
                return {"offer_id": identity, "escrow": False, "time_used": 1}, [actor, args["to"]]
            if kind == "CANCEL_TRADE":
                self._args(args, {"offer_id"})
                require(isinstance(args["offer_id"], str), "invalid_offer_id")
                offer = state["offers"].get(args["offer_id"])
                require(offer is not None and offer["status"] == "open" and offer["from"] == actor, "offer_not_owned_or_open")
                offer["status"] = "cancelled"
                self._private(state, [actor, offer["to"]], {**event, **args})
                return {"offer_cancelled": offer["id"], "time_used": 1}, [actor, offer["to"]]
            raise ValueError("unknown_communication_action")
        if kind in ("BUY", "FETCH"):
            self._args(args, set(GOODS))
            require(all(type(args[g]) is int and args[g] >= 0 for g in GOODS) and sum(args.values()) > 0, "invalid_goods_bundle")
            self._cost_time(state, actor, 1)
            cost = sum(args[g] * d["market"]["prices"][g] for g in GOODS) if kind == "BUY" else 0
            available = {g: state["market"][g] if kind == "BUY" else state["facilities"]["shared_pantry" if g == "food" else "water_maintenance"]["stock"] for g in GOODS}
            details = {"requested": copy.deepcopy(args), "available_at_execution": available,
                       "shortages": {g: args[g] - available[g] for g in GOODS if args[g] > available[g]},
                       "bundle_policy": "all_or_nothing", "executed_quantities": {g: 0 for g in GOODS},
                       "single_item_rule": "Set the unwanted item's quantity to 0; no automatic partial fill"}
            if state["balances"][actor] < cost:
                raise ActionRejected("insufficient_purchase_credits", {**details, "cost": cost, "own_credits": state["balances"][actor]})
            if details["shortages"]:
                raise ActionRejected("insufficient_supply_after_conflict_resolution", details)
            for good in GOODS:
                if kind == "BUY":
                    state["market"][good] -= args[good]
                else:
                    state["facilities"]["shared_pantry" if good == "food" else "water_maintenance"]["stock"] -= args[good]
                state["inventory"][actor][good] += args[good]
            state["balances"][actor] -= cost
            state["market_revenue"] += cost
            effect = {"inventory_delta": copy.deepcopy(args), "credits_delta": -cost, "time_used": 1}
            self._private(state, [actor], {**event, **effect})
            return effect, [actor]
        if kind == "GIVE":
            self._args(args, {"to", "asset", "amount"}); self._recipient(actor, args["to"])
            self._cost_time(state, actor, 1)
            self._move(state, actor, args["to"], args["asset"], args["amount"])
            self._private(state, [actor, args["to"]], {**event, **args})
            self._relationship(state, actor, args["to"], kind)
            return {**args, "time_used": 1, "transfer_executed": True}, [actor, args["to"]]
        if kind == "ACCEPT_TRADE":
            self._args(args, {"offer_id"})
            require(isinstance(args["offer_id"], str), "invalid_offer_id")
            offer = state["offers"].get(args["offer_id"])
            require(offer is not None and offer["status"] == "open" and offer["to"] == actor and state["round"] <= offer["expires_round"], "offer_unavailable_to_recipient")
            self._cost_time(state, actor, 1)
            giver = offer["from"]
            self._move(state, giver, actor, offer["give"]["asset"], offer["give"]["amount"])
            self._move(state, actor, giver, offer["want"]["asset"], offer["want"]["amount"])
            offer["status"] = "accepted"
            effect = {"offer_id": offer["id"], "give": offer["give"], "want": offer["want"], "time_used": 1}
            self._private(state, [actor, giver], {**event, **effect})
            self._relationship(state, actor, giver, "completed_trade")
            return copy.deepcopy(effect), [actor, giver]
        if kind in ("EARN", "REST", "MAINTAIN"):
            self._args(args, {"units", "facility_id"} if kind == "MAINTAIN" else {"units"})
            units = args["units"]; self._positive(units)
            self._cost_time(state, actor, units, labor=kind != "REST")
            if kind == "EARN":
                bonus_units = min(units, state["work_slots"])
                state["work_slots"] -= bonus_units
                wage = units * d["base_wage"] + bonus_units * d["workspace_bonus"]
                state["balances"][actor] += wage
                state["accounting"]["minted_wages"] += wage
                effect = {"wage": wage, "workspace_bonus_units": bonus_units, "time_used": units, "labor_used": units}
                self._private(state, [actor], {**event, **effect})
            elif kind == "REST":
                before = state["health"][actor]
                state["health"][actor] = min(d["max_health"], before + units * d["rest_health_per_unit"])
                effect = {"health_delta": state["health"][actor] - before, "time_used": units}
                self._private(state, [actor], {**event, **effect})
            else:
                name = args["facility_id"]
                require(isinstance(name, str) and name in FACILITIES, "invalid_facility")
                f, rule = state["facilities"][name], d["facilities"][name]
                require(f["condition"] < rule["max_condition"], "facility_already_at_maximum")
                cost = units * d["maintenance_cost_per_unit"]
                require(state["balances"][actor] >= cost, "insufficient_maintenance_credits")
                state["balances"][actor] -= cost
                state["accounting"]["spent_maintenance_costs"] += cost
                before = f["condition"]
                f["condition"] = min(rule["max_condition"], before + units * d["repair_per_unit"])
                effect = {"facility_id": name, "condition_delta": f["condition"] - before, "credits_delta": -cost, "time_used": units, "labor_used": units}
                state["public_events"].append({"round": state["round"], **event, **effect})
            return effect, list(self.aliases) if kind == "MAINTAIN" else [actor]
        raise ValueError("unknown_material_action")

    def _resolve_projects(self, state: dict) -> list[dict]:
        effects = super()._resolve_projects(state)
        for effect in list(effects):
            if effect["type"] == "project_completed":
                name = state["projects"][effect["project_id"]]["template"]["id"]
                before = state["facilities"][name]["condition"]
                state["facilities"][name]["condition"] = self.life["facilities"][name]["max_condition"]
                restored = {"type": "facility_restored", "facility_id": name, "condition_delta": state["facilities"][name]["condition"] - before,
                            "production_effective": "next_round", "affected_agents": list(self.aliases)}
                effects.append(restored)
                state["public_events"].append({"round": state["round"], **restored})
        return effects

    def _settle_phase(self, state: dict, phase: str) -> list[dict]:
        effects = self._resolve_projects(state)
        phases = self.phases()
        state["next_phase"] = phases[phases.index(phase) + 1] if phase != "action" else "settled"
        if phase != "action":
            return effects
        for a in self.aliases:
            before_needs, before_health = copy.deepcopy(state["needs"][a]), state["health"][a]
            consumed = {}
            for g in GOODS:
                consumed[g] = min(state["inventory"][a][g], state["needs"][a][g])
                state["inventory"][a][g] -= consumed[g]
                state["needs"][a][g] -= consumed[g]
                state["goods_ledger"][g]["consumed"] += consumed[g]
            if max(state["needs"][a].values()) >= self.life["damage_threshold"]:
                state["health"][a] = max(0, before_health - self.life["damage_per_round"])
            elif not any(state["needs"][a].values()):
                state["health"][a] = min(self.life["max_health"], before_health + self.life["recovery_per_round"])
            outcome = {"type": "daily_consumption_and_health", "round": state["round"], "consumed": consumed,
                       "needs_before": before_needs, "needs_after": copy.deepcopy(state["needs"][a]),
                       "health_before": before_health, "health_after": state["health"][a]}
            state["last_daily_outcome"][a] = outcome
            self._private(state, [a], outcome)
        # Do not publish individual deficits/health through system_effects.
        return effects

    def assert_accounting(self, state: dict) -> None:
        # Market payments remain in a separate external-sector account.
        accounting = state["accounting"]
        super_state = {**state, "pool": state["pool"] + state["market_revenue"],
                       "accounting": {**accounting,
                           "spent_project_costs": accounting["spent_project_costs"] + accounting["spent_maintenance_costs"],
                           "minted_rewards": accounting["minted_rewards"] + accounting["minted_wages"]}}
        super().assert_accounting(super_state)
        require(type(state["pool"]) is int and state["pool"] >= 0 and type(state["market_revenue"]) is int and state["market_revenue"] >= 0, "Invalid public/external balance")
        for a in self.aliases:
            require(all(type(v) is int and v >= 0 for v in state["inventory"][a].values()), "Invalid inventory")
            require(0 <= state["health"][a] <= self.life["max_health"] and state["time"][a] >= 0 and state["labor"][a] >= 0, "Invalid daily bounds")
            require(all(0 <= n <= self.life["max_deficit"] for n in state["needs"][a].values()), "Invalid need bounds")
        for name, f in state["facilities"].items():
            require(0 <= f["condition"] <= self.life["facilities"][name]["max_condition"] and 0 <= f["stock"] <= self.life["facilities"][name]["stock_cap"], "Invalid facility bounds")
        require(state["work_slots"] >= 0, "Invalid work capacity")
        for g in GOODS:
            require(0 <= state["market"][g] <= self.life["market"]["stock_cap"][g], "Invalid market stock")
            facility = "shared_pantry" if g == "food" else "water_maintenance"
            total = sum(state["inventory"][a][g] for a in self.aliases) + state["market"][g] + state["facilities"][facility]["stock"]
            ledger = state["goods_ledger"][g]
            require(total + ledger["consumed"] + ledger["lost"] == ledger["initial"] + ledger["created"], "Goods conservation failed")
