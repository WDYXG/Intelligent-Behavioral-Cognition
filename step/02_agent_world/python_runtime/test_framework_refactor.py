"""Authored but NOT executed during the refactor. Synthetic actors only; no API.

User may run: python -m unittest discover -s step/02_agent_world/python_runtime -p "test_*.py"
"""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from agent import Agent
from cognition import apply_report
from common import read_json, require
from gm import GM
from observation import ObservationDispatcher
from scene_community import LivingCommunityScene


class CaptureLog:
    def __init__(self):
        self.records = []

    def add(self, kind, payload):
        self.records.append((kind, copy.deepcopy(payload)))


class MinimalScene:
    """Contract fixture without any community fields; not a research scenario."""
    def __init__(self, aliases):
        self.aliases = aliases
        self.state = {"round": 1, "version": 1, "accepted_steps": 0}

    def snapshot(self):
        return copy.deepcopy(self.state)

    def validate_phase(self, phase):
        require(phase == "work", "wrong_phase")

    def check_preconditions(self, state, request, before):
        require(request["action"] == "WAIT", "unsupported")

    def execute_action(self, state, request, before):
        state["accepted_steps"] += 1
        return {}, [request["actor_id"]]

    def settle_phase(self, state, phase):
        return []

    def collect_world_events(self, before, after, phase):
        return []

    def assert_invariants(self, state):
        require(state["accepted_steps"] >= 0, "invalid_count")

    def rejection_options(self, request, before, reason):
        return ["Use supported action"]


class FrameworkTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(Path(__file__).resolve().parents[2] / "03_experiment/configs/community_python_v1.json")
        self.aliases = [f"resident_{i:02d}" for i in range(1, 13)]
        self.scene = LivingCommunityScene(self.aliases, self.config)
        self.scene.begin_round(1)
        self.gm = GM(self.scene)

    def batch(self, action="WAIT", arguments=None, actor="resident_01"):
        phase = self.scene.state["next_phase"]
        requests = [{"request_id": f"r1:{phase}:{a}", "actor_id": a, "phase": phase, "round": 1,
            "world_version": self.scene.state["version"], "basis_observation_id": f"opening:{a}",
            "action": action if a == actor else "WAIT", "arguments": (arguments or {}) if a == actor else {}}
            for a in self.aliases]
        return self.gm.commit(requests, self.aliases, phase)

    def open_args(self, **changes):
        return {"text": "请一起讨论怎么安排明天的事务。", "mode": "negotiate", "audience": "resident_02",
                "subject": None, "operation": None, **changes}

    def actor(self):
        return Agent({"alias": "resident_01", "initial": {"P": {"L1": {"unknown": None}}, "M0": {}}, "view": {"L1": {"unknown": None}}})

    def context(self, agent, log):
        agent.deliver(self.scene.observe(agent.alias, "opening"), log)
        return agent.decision_context(self.config, 1, 17)

    def report(self, context, op="create", linked="task1"):
        return {"judgments": [], "intent_updates": [{"op": op, "task_id": "task1", "title": "了解公共事务",
            "next_step": "决定是否继续了解", "basis_refs": [context["Observation"]["observation_id"]]}], "action_task_id": linked}

    def test_scene_config_and_phases(self):
        LivingCommunityScene.validate_config(self.config)
        self.assertEqual(self.scene.phases(), LivingCommunityScene.phase_names(self.config))

    def test_open_private_negotiation_is_not_consent(self):
        before = self.scene.observe("resident_02", "opening")
        tx = self.batch("OPEN", self.open_args())
        self.assertTrue(tx["results"][0]["accepted"])
        own = self.scene.observe("resident_02", "feedback", since=before["content"]["event_cursors"])
        other = self.scene.observe("resident_03", "feedback")
        received = [e for e in own["content"]["events"] if e["payload"].get("mode") == "negotiate"]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["epistemic_status"], "reported_speech_not_verified")
        self.assertEqual(received[0]["payload"]["other_agent_consent"], "not_implied")
        self.assertFalse(any(e["payload"].get("mode") == "negotiate" for e in other["content"]["events"]))
        self.assertFalse(any(e["payload"].get("mode") == "negotiate" for e in before["content"]["events"]))

    def test_public_speech_is_event_not_verified_claim(self):
        self.batch("SAY", {"text": "所有人都已经同意了。"})
        for a in self.aliases:
            events = self.scene.observe(a, "feedback")["content"]["events"]
            self.assertTrue(any(e["payload"].get("action") == "SAY" and e["epistemic_status"] == "reported_speech_not_verified" for e in events))
        self.assertEqual(self.scene.state["projects"], {})

    def test_unsupported_free_text_is_explicit_rejection(self):
        tx = self.batch("OPEN", self.open_args(mode="auto", text="让资源凭空翻倍。"))
        self.assertFalse(tx["results"][0]["accepted"])
        self.assertTrue(tx["results"][0]["possible_next_steps"])

    def test_open_cannot_rewrite_resources(self):
        before = self.scene.state["balances"]["resident_01"]
        tx = self.batch("OPEN", self.open_args(mode="execute", audience="self", operation={"action": "SET_BALANCE", "arguments": {"note": "increase balance"}}))
        self.assertFalse(tx["results"][0]["accepted"])
        self.assertEqual(self.scene.state["balances"]["resident_01"], before)

    def test_inspection_private_and_no_other_private_state(self):
        tx = self.batch("OPEN", self.open_args(mode="investigate", audience="self", subject="self"))
        self.assertTrue(tx["results"][0]["accepted"])
        events = [e for e in tx["world_events"] if e["payload"].get("action") == "INSPECT"]
        self.assertEqual(events[0]["visibility"], ["resident_01"])
        self.assertNotIn("resident_02", str(events[0]["payload"]["findings"]))

    def test_open_grounded_buy_preserves_original_checks(self):
        self.batch()
        before = self.scene.state["balances"]["resident_01"]
        tx = self.batch("OPEN", self.open_args(mode="execute", audience="self", operation={"action": "BUY", "arguments": {"food": 1, "water": 0}}))
        self.assertTrue(tx["results"][0]["accepted"])
        self.assertEqual(self.scene.state["balances"]["resident_01"], before - self.config["daily_life"]["market"]["prices"]["food"])

    def test_judgment_and_task_do_not_mutate_persona(self):
        agent, log = self.actor(), CaptureLog()
        frozen = agent.snapshot()
        context = self.context(agent, log)
        report = self.report(context)
        report["judgments"] = [{"text": "我还不能确定别人是否愿意参与。", "confidence": "uncertain", "basis_refs": [context["Observation"]["observation_id"]]}]
        agent.accept_decision({"action": "WAIT", "arguments": {}, "cognition": report}, context, self.config, "r1:communication_1:resident_01", log)
        self.assertEqual(agent.snapshot()["P"], frozen["P"])
        self.assertEqual(agent.snapshot()["M"], frozen["M"])
        self.assertEqual(agent.X["tasks"]["task1"]["status"], "active")

    def test_undelivered_reference_rejected_atomically(self):
        agent, log = self.actor(), CaptureLog()
        context = self.context(agent, log)
        report = self.report(context)
        report["intent_updates"][0]["basis_refs"] = ["secret:resident_02"]
        before = copy.deepcopy(agent.X)
        with self.assertRaises(ValueError):
            apply_report(agent.X, report, context, self.config["cognition"], "request")
        self.assertEqual(agent.X, before)

    def test_speech_does_not_auto_create_task(self):
        agent, log = self.actor(), CaptureLog()
        context = self.context(agent, log)
        report = {"judgments": [], "intent_updates": [], "action_task_id": None}
        agent.accept_decision({"action": "SAY", "arguments": {"text": "我愿意帮忙。"}, "cognition": report}, context, self.config, "request", log)
        self.assertEqual(agent.X["tasks"], {})

    def test_rejection_blocks_then_actor_can_abandon(self):
        agent, log = self.actor(), CaptureLog()
        context = self.context(agent, log)
        agent.accept_decision({"cognition": self.report(context)}, context, self.config, "request", log)
        result = {"request_id": "request", "task_id": "task1", "accepted": False, "phase": "communication_1", "effects": {}, "rejection_reason": "insufficient_time"}
        agent.deliver(self.scene.observe(agent.alias, "feedback", result), log)
        self.assertEqual(agent.X["tasks"]["task1"]["status"], "blocked")
        context = agent.decision_context(self.config, 1, 17)
        agent.accept_decision({"cognition": self.report(context, "abandon", None)}, context, self.config, "request2", log)
        self.assertEqual(agent.X["tasks"]["task1"]["status"], "abandoned")

    def test_success_is_not_automatic_task_completion(self):
        agent, log = self.actor(), CaptureLog()
        context = self.context(agent, log)
        agent.accept_decision({"cognition": self.report(context)}, context, self.config, "request", log)
        result = {"request_id": "request", "task_id": "task1", "accepted": True, "phase": "communication_1", "effects": {}, "rejection_reason": None}
        agent.deliver(self.scene.observe(agent.alias, "feedback", result), log)
        self.assertEqual(agent.X["tasks"]["task1"]["status"], "active")

    def test_generic_gm_without_community_state(self):
        scene = MinimalScene(self.aliases)
        requests = [{"request_id": a, "actor_id": a, "phase": "work", "round": 1, "world_version": 1,
                     "action": "WAIT", "arguments": {}} for a in self.aliases]
        transaction = GM(scene).commit(requests, self.aliases, "work")
        self.assertEqual(transaction["after"]["accepted_steps"], 12)

    def test_agent_accepts_noncommunity_observation(self):
        agent, log = self.actor(), CaptureLog()
        agent.deliver({"observation_id": "example:1", "recipient": agent.alias, "visibility": [agent.alias], "round": 1,
                       "world_version": 1, "phase": "work:opening", "content": {"scene": {"name": "contract_fixture"},
                       "self": {"available_slot": 1}, "events": [], "action_result": None, "decision_phase": "work", "event_cursors": {}}}, log)
        self.assertEqual(agent.decision_context(self.config, 1, 17)["phase"], "work")

    def test_dispatcher_blocks_unregistered_event(self):
        dispatcher, agent, log = ObservationDispatcher(self.aliases), self.actor(), CaptureLog()
        original = self.scene.observe
        def forged(*args, **kwargs):
            obs = original(*args, **kwargs)
            obs["content"]["events"][0]["event_id"] = "not_committed"
            return obs
        self.scene.observe = forged
        with self.assertRaises(ValueError):
            dispatcher.deliver(self.scene, agent, "opening", log)
        self.assertEqual(agent.episodes, [])

    def test_dispatcher_registers_then_delivers_authorized_events(self):
        scene = LivingCommunityScene(self.aliases, self.config)
        boundary = scene.begin_round(1)
        dispatcher, agent, log = ObservationDispatcher(self.aliases), self.actor(), CaptureLog()
        dispatcher.register(boundary["world_events"], log)
        obs = dispatcher.deliver(scene, agent, "opening", log)
        self.assertEqual(len(obs["content"]["events"]), len(boundary["world_events"]))

    def test_tasks_survive_phase_and_round_changes(self):
        agent, log = self.actor(), CaptureLog()
        context = self.context(agent, log)
        agent.accept_decision({"cognition": self.report(context)}, context, self.config, "request", log)
        self.batch()
        self.batch()
        self.scene.begin_round(2)
        agent.deliver(self.scene.observe(agent.alias, "round2_opening"), log)
        self.assertEqual(agent.X["tasks"]["task1"]["status"], "active")
        self.assertEqual(agent.X["current_round"], 2)

    def test_generic_commit_is_idempotent(self):
        scene = MinimalScene(self.aliases)
        requests = [{"request_id": a, "actor_id": a, "phase": "work", "round": 1, "world_version": 1,
                     "action": "WAIT", "arguments": {}} for a in self.aliases]
        gm = GM(scene)
        first = gm.commit(requests, self.aliases, "work")
        self.assertEqual(gm.commit(requests, self.aliases, "work"), first)
        self.assertEqual(scene.state["accepted_steps"], 12)


if __name__ == "__main__":
    unittest.main()
