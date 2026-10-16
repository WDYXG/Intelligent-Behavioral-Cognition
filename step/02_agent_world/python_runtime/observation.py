"""Deliver only committed, audience-authorized WorldEvents; no scene rule knowledge."""
from __future__ import annotations

import copy

from common import require


class ObservationDispatcher:
    def __init__(self, aliases: list[str]):
        self.aliases = set(aliases)
        self.events: dict[str, dict] = {}
        self.delivered = {alias: set() for alias in aliases}
        self.last_sequence = 0

    def register(self, events: list[dict], log) -> None:
        """Called after environment/GM commit, never while sealed choices collect."""
        for event in events:
            require(isinstance(event.get("event_id"), str) and event["event_id"] not in self.events, "Duplicate/invalid WorldEvent")
            require(isinstance(event.get("visibility"), list) and set(event["visibility"]) <= self.aliases,
                    "WorldEvent has unknown audience")
            require(type(event.get("world_sequence")) is int and event["world_sequence"] > self.last_sequence, "WorldEvent order reversal")
            self.last_sequence = event["world_sequence"]
            self.events[event["event_id"]] = copy.deepcopy(event)
            log.add("world_event", event)

    def deliver(self, scene, agent, phase: str, log, feedback: dict | None = None, schedule: dict | None = None) -> dict:
        previous = agent.X["last_observation"]
        since = previous["content"].get("event_cursors") if previous else None
        obs = scene.observe(agent.alias, phase, feedback, since=since)
        require(obs["round"] == scene.state["round"] and obs["world_version"] == scene.state["version"], "Stale scene observation")
        require(set(("scene", "self", "events", "action_result", "decision_phase", "event_cursors")) <= set(obs["content"]), "Incomplete normalized observation")
        require(obs["content"]["action_result"] == feedback, "Scene changed recipient's action result")
        if feedback is not None:
            require(feedback["agent_id"] == agent.alias, "Wrong feedback recipient")
        received = obs["content"]["events"]
        sequence = [event["world_sequence"] for event in received]
        require(sequence == sorted(set(sequence)), "Duplicate/unordered observed WorldEvents")
        ids = set()
        for event in received:
            identity = event["event_id"]
            require(identity in self.events and event == self.events[identity], "Uncommitted or altered WorldEvent")
            require(agent.alias in event["visibility"] and event["round"] <= obs["round"], "Unauthorized/future event delivery")
            ids.add(identity)
        unseen = {key for key, event in self.events.items() if agent.alias in event["visibility"]} - self.delivered[agent.alias]
        require(unseen <= ids, "Scene omitted newly visible WorldEvents; history windows may only trim already-delivered events")
        if schedule is not None:
            obs["content"]["schedule"] = copy.deepcopy(schedule)
        agent.deliver(obs, log)
        self.delivered[agent.alias].update(ids)
        return obs
