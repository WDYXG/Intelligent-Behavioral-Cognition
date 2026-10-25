"""Fixed four-person N→P public-goods environment, with no model dependencies.

Only observe(participant_id) may be sent to an agent. Audit/trajectory exports
are researcher-only. Actions are committed in complete batches, never one by
one. Money is stored in fifths of a Guilder to avoid cumulative rounding drift.
"""

from copy import deepcopy
import json
from pathlib import Path
import random


class InvalidAction(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidAction(message)


def integer(value, upper):
    return type(value) is int and 0 <= value <= upper


def validate_action(stage, action):
    """Shared by the decision parser and the authoritative World executor."""
    require(stage in {"contribution", "punishment"}, "This stage does not accept actions")
    field = "contribution" if stage == "contribution" else "points"
    require(type(action) is dict and set(action) == {field}, f"Action must contain only {field}")
    value = action[field]
    if field == "contribution":
        require(integer(value, 20), "Contribution must be an integer from 0 to 20")
    else:
        require(type(value) is list and len(value) == 3 and all(integer(v, 10) for v in value),
                "Assign exactly three integer point amounts from 0 to 10")


class World:
    def __init__(self, group_id, participants, seed=0):
        require(isinstance(group_id, str) and bool(group_id), "A group ID is required")
        require(isinstance(participants, (list, tuple)) and len(participants) == 4
                and all(isinstance(p, str) and p for p in participants)
                and len(set(participants)) == 4, "Exactly four distinct participant IDs are required")
        require(type(seed) is int, "Seed must be an integer")
        self.group_id = group_id
        self.participants = tuple(participants)
        self.phase, self.round, self.stage = "N", 1, "contribution"
        self._rng = random.Random(seed)
        self._phase_ticks = {p: 0 for p in self.participants}
        self._total_ticks = {p: 0 for p in self.participants}
        self._history = {p: [{"type": "experiment_started", "rounds": 10}] for p in self.participants}
        self._contributions, self._order = {}, {}
        self._feedback, self._rows = {}, []
        self._events = [{"kind": "init", "version": 1, "group_id": group_id,
                         "participants": list(participants), "seed": seed}]

    def _rules(self):
        rules = {"group_size": 4, "same_group_each_round": True, "rounds": 10,
                 "fresh_tokens_each_round": 20, "tokens_carry_over": False,
                 "contribution_range": [0, 20], "contributions_are_simultaneous": True,
                 "retained_token_income": 1, "return_per_group_token": 0.4,
                 "income_formula": "20 - own_contribution + 0.4 * group_total_contribution",
                 "communication_allowed": False,
                 "anonymous_positions": "Other contributions are reshuffled every round; positions are not persistent identities."}
        if self.phase == "P":
            rules.update({"deduction_points_per_other_member": [0, 10], "cost_per_point_sent": 1,
                          "income_deduction_per_point_received": 3,
                          "points_representation": "Nonnegative counts of deduction points.",
                          "period_income_formula": "max(0, first_stage_income - 3 * total_received_points) - total_sent_points",
                          "negative_period_income_allowed": True, "phase_lump_sum": 25,
                          "deductions_are_simultaneous": True, "punishers_are_anonymous": True,
                          "experiment_ends_after_this_phase": True})
        return rules

    def _display(self, pid):
        return [{"position": i + 1, "contribution": self._contributions[target]}
                for i, target in enumerate(self._order[pid])]

    def observe(self, participant_id):
        require(participant_id in self.participants, "Unknown participant")
        pid = participant_id
        view = {"phase": self.phase, "round": self.round, "stage": self.stage,
                "rules": self._rules(), "phase_income": self._phase_ticks[pid] / 5,
                "total_income": self._total_ticks[pid] / 5, "history": self._history[pid]}
        if self.stage == "contribution":
            view["available_tokens"] = 20
            view["action_schema"] = {"contribution": "integer from 0 to 20"}
        elif self.stage == "punishment":
            view.update({"own_contribution": self._contributions[pid],
                         "other_contributions": self._display(pid),
                         "first_stage_income": self._gross_ticks(pid) / 5,
                         "action_schema": {"points": "three integers from 0 to 10, in displayed position order"}})
        elif self.stage == "feedback":
            view["feedback"] = self._feedback[pid]
        return deepcopy(view)

    def _validate_batch(self, actions, stage):
        require(self.stage == stage, f"Expected {stage} stage, found {self.stage}")
        require(type(actions) is dict and set(actions) == set(self.participants), "A complete four-person batch is required")
        for action in actions.values():
            validate_action(stage, action)

    def _gross_ticks(self, pid):
        return 5 * (20 - self._contributions[pid]) + 2 * sum(self._contributions.values())

    def _settle(self, points=None):
        received = {p: 0 for p in self.participants}
        if points is not None:
            for sender in self.participants:
                for target, amount in zip(self._order[sender], points[sender]):
                    received[target] += amount
        for pid in self.participants:
            gross = self._gross_ticks(pid)
            sent = sum(points[pid]) if points is not None else 0
            reduction = min(gross, 15 * received[pid])
            income = gross - reduction - 5 * sent
            self._phase_ticks[pid] += income
            self._total_ticks[pid] += income
            feedback = {"type": "round_feedback", "phase": self.phase, "round": self.round,
                        "own_contribution": self._contributions[pid], "other_contributions": self._display(pid),
                        "group_total_contribution": sum(self._contributions.values()),
                        "first_stage_income": gross / 5, "period_income": income / 5,
                        "phase_income": self._phase_ticks[pid] / 5, "total_income": self._total_ticks[pid] / 5}
            targets = []
            if points is not None:
                feedback.update({"assigned_points_by_position": list(points[pid]), "assigned_point_cost": sent,
                                 "received_points": received[pid], "income_reduction": reduction / 5})
                targets = [{"contribution": self._contributions[target], "points": amount}
                           for target, amount in zip(self._order[pid], points[pid])]
            self._feedback[pid] = feedback
            self._history[pid].append(deepcopy(feedback))
            self._rows.append({"group_id": self.group_id, "participant_id": pid, "phase": self.phase,
                               "round": self.round, "contribution": self._contributions[pid],
                               "received_points": received[pid] if points is not None else None, "targets": targets,
                               "period_income": income / 5, "phase_income": self._phase_ticks[pid] / 5,
                               "total_income": self._total_ticks[pid] / 5})
        self.stage = "feedback"

    def _request(self, operation, actions, validate, execute):
        event = {"kind": "request", "operation": operation, "phase": self.phase,
                 "round": self.round, "stage": self.stage, "actions": deepcopy(actions)}
        try:
            validate()
        except InvalidAction as error:
            event.update({"accepted": False, "reason": str(error)})
            self._events.append(event)
            raise
        execute()
        event.update({"accepted": True, "result": {"phase": self.phase, "round": self.round, "stage": self.stage,
                                                    "feedback": deepcopy(self._feedback) if self.stage == "feedback" else None}})
        if operation == "contributions":
            event["result"]["researcher_only_display_order"] = deepcopy(self._order)
        self._events.append(event)

    def resolve_contributions(self, actions):
        def execute():
            self._contributions = {p: actions[p]["contribution"] for p in self.participants}
            self._order = {}
            for pid in self.participants:
                others = [p for p in self.participants if p != pid]
                self._rng.shuffle(others)
                self._order[pid] = others
            if self.phase == "N":
                self._settle()
            else:
                self.stage = "punishment"
        self._request("contributions", actions, lambda: self._validate_batch(actions, "contribution"), execute)

    def resolve_punishments(self, actions):
        self._request("punishments", actions, lambda: self._validate_batch(actions, "punishment"),
                      lambda: self._settle({p: actions[p]["points"] for p in self.participants}))

    def advance(self):
        def execute():
            if self.round == 10 and self.phase == "P":
                self.stage = "finished"
                return
            if self.round == 10:
                self.phase, self.round = "P", 1
                for pid in self.participants:
                    self._phase_ticks[pid] = 125
                    self._total_ticks[pid] += 125
                    self._history[pid].append({"type": "new_experiment_announced", "phase": "P", "rounds": 10,
                                               "same_group": True, "lump_sum": 25, "ends_after_this_experiment": True})
            else:
                self.round += 1
            self.stage = "contribution"
            self._contributions, self._order, self._feedback = {}, {}, {}
        self._request("advance", None, lambda: require(self.stage == "feedback", "Advance requires completed feedback"), execute)

    def audit_log(self):
        """Researcher-only data. Contains identities and must not enter prompts."""
        return deepcopy(self._events)

    def trajectory(self):
        """Researcher-only records compatible with human_reference.load_agent."""
        return deepcopy(self._rows)

    def export(self, directory):
        require(self.stage == "finished", "Only a completed experiment can be exported for scoring")
        directory = Path(directory)
        require(not directory.exists(), "Output directory already exists; preserve previous runs")
        directory.mkdir(parents=True)
        for filename, rows in [("events.jsonl", self.audit_log()), ("trajectory.jsonl", self.trajectory())]:
            (directory / filename).write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows))

    @classmethod
    def replay(cls, events):
        require(bool(events) and events[0].get("kind") == "init" and events[0].get("version") == 1, "Unsupported event log")
        config = events[0]
        world = cls(config["group_id"], config["participants"], config["seed"])
        operations = {"contributions": world.resolve_contributions, "punishments": world.resolve_punishments,
                      "advance": world.advance}
        for event in events[1:]:
            require(event.get("kind") == "request" and event.get("operation") in operations, "Unknown event")
            try:
                if event["operation"] == "advance":
                    world.advance()
                else:
                    operations[event["operation"]](deepcopy(event["actions"]))
            except InvalidAction:
                require(event.get("accepted") is False, "A previously accepted event cannot be replayed")
            require(world._events[-1] == event, "Replay disagrees with recorded actions, ordering or outcomes")
        require(world.audit_log() == events, "Replay log differs")
        return world


def scripted_demo(seed=20261019):
    """Artificial integration fixture, not an Agent or a behavioral benchmark."""
    world = World("offline-fixture", ["member-a", "member-b", "member-c", "member-d"], seed)
    while world.stage != "finished":
        observations = {pid: world.observe(pid) for pid in world.participants}
        if world.stage == "contribution":
            actions = {pid: {"contribution": (i * 5 + observations[pid]["round"]) % 21}
                       for i, pid in enumerate(world.participants)}
            world.resolve_contributions(actions)
        elif world.stage == "punishment":
            world.resolve_punishments({pid: {"points": [1, 0, 2]} for pid in world.participants})
        else:
            world.advance()
    return world


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Run an artificial offline World fixture; no model calls.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    demo = scripted_demo()
    replayed = World.replay(demo.audit_log())
    require(replayed.trajectory() == demo.trajectory(), "Trajectory replay differs")
    if args.output:
        demo.export(args.output)
    print(json.dumps({"fixture_only": True, "rounds": 20, "person_rounds": len(demo.trajectory()),
                      "replay_matches": True, "model_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
