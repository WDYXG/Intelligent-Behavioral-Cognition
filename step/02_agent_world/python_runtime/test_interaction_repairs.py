"""Offline regression cases, authored but not executed in this change.

Uses twelve synthetic aliases and the scenario config only, never Persona files or APIs.
Run explicitly with unittest if authorized; importing this file does not run a simulation.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from living_world import LivingCommunityWorld


class InteractionRepairs(unittest.TestCase):
    def setUp(self):
        config_path = Path(__file__).resolve().parents[2] / "03_experiment/configs/community_python_v1.json"
        self.config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        self.aliases = [f"resident_{n:02d}" for n in range(1, 13)]
        self.world = LivingCommunityWorld(self.aliases, self.config)
        self.world.begin_round(1)

    def commit(self, phase, overrides=None):
        overrides = overrides or {}
        requests = [{"request_id": f"r{self.world.state['round']}:{phase}:{a}", "actor_id": a,
                     "round": self.world.state["round"], "world_version": self.world.state["version"],
                     "phase": phase, **overrides.get(a, {"action": "WAIT", "arguments": {}})} for a in self.aliases]
        return self.world.commit_round(requests, self.aliases, phase)

    def test_single_good_fetch_is_legal(self):
        self.commit("communication_1")
        t = self.commit("action", {self.aliases[0]: {"action": "FETCH", "arguments": {"food": 1, "water": 0}}})
        r = t["results"][0]
        self.assertTrue(r["accepted"])
        self.assertEqual(r["effects"]["inventory_delta"], {"food": 1, "water": 0})

    def test_bundle_rejection_reports_shortage_without_partial_fill(self):
        self.commit("communication_1")
        stock = self.world.state["facilities"]["water_maintenance"]["stock"]
        t = self.commit("action", {self.aliases[0]: {"action": "FETCH", "arguments": {"food": 1, "water": stock + 1}}})
        r = t["results"][0]
        self.assertFalse(r["accepted"])
        self.assertEqual(r["rejection_details"]["shortages"], {"water": 1})
        self.assertEqual(t["before"]["facilities"], t["after"]["facilities"])
        self.assertEqual(r["effects"], {})

    def test_invalid_plan_reference_does_not_cancel_valid_step(self):
        self.commit("communication_1")
        t = self.commit("action", {self.aliases[0]: {"action": "PLAN", "arguments": {"steps": [
            {"action": "FETCH", "arguments": {"food": 1, "water": 0}},
            {"action": "WORK", "arguments": {"project_id": "shared_pantry", "units": 1}},
        ]}}})
        steps = t["results"][0]["effects"]["steps"]
        self.assertEqual([s["accepted"] for s in steps], [True, False])
        self.assertEqual(steps[1]["rejection_reason"], "project_not_in_phase_observation")
        self.assertEqual(t["after"]["facilities"]["shared_pantry"]["stock"], t["before"]["facilities"]["shared_pantry"]["stock"] - 1)

    def test_rejected_first_step_allows_later_valid_step(self):
        self.commit("communication_1")
        t = self.commit("action", {self.aliases[0]: {"action": "PLAN", "arguments": {"steps": [
            {"action": "WORK", "arguments": {"project_id": "shared_pantry", "units": 1}},
            {"action": "FETCH", "arguments": {"food": 0, "water": 1}},
        ]}}})
        self.assertEqual([s["accepted"] for s in t["results"][0]["effects"]["steps"]], [False, True])

    def test_same_phase_new_project_is_not_visible_to_later_actor(self):
        t = self.commit("communication_1", {
            self.aliases[0]: {"action": "PROPOSE", "arguments": {"template_id": "shared_pantry"}},
            self.aliases[1]: {"action": "VOTE", "arguments": {"project_id": "project_001", "support": True}},
        })
        self.assertTrue(t["results"][0]["accepted"])
        self.assertFalse(t["results"][1]["accepted"])
        self.assertEqual(t["results"][1]["rejection_reason"], "project_not_in_phase_observation")

    def test_project_interface_distinguishes_templates_and_instances(self):
        obs = self.world.observe(self.aliases[0], "opening")["content"]
        self.assertIn("shared_pantry", obs["community"]["proposal_templates"])
        self.assertEqual(obs["community"]["project_board"]["funded_workable"], {})
        self.assertFalse(obs["decision_interface"]["maintenance"]["shared_pantry"]["requires_project"])

    def test_private_help_not_delivered_to_uninvolved_resident(self):
        self.commit("communication_1", {self.aliases[0]: {"action": "ASK_HELP", "arguments": {"to": self.aliases[1], "text": "Need water"}}})
        identity = f"r1:communication_1:{self.aliases[0]}"
        recipient = self.world.observe(self.aliases[1], "action_opening")["content"]
        stranger = self.world.observe(self.aliases[2], "action_opening")["content"]
        self.assertIn(identity, recipient["help_inbox"]["others_visible_open"])
        self.assertNotIn(identity, stranger["help_inbox"]["others_visible_open"])
        self.assertNotIn(identity, stranger["community"]["help_requests"])
        self.assertEqual(stranger["private_events"], [])

    def test_same_round_response_and_gift_remain_separate_steps(self):
        self.commit("communication_1", {self.aliases[0]: {"action": "ASK_HELP", "arguments": {"to": "public", "text": "Need water"}}})
        identity = f"r1:communication_1:{self.aliases[0]}"
        t = self.commit("action", {self.aliases[1]: {"action": "PLAN", "arguments": {"steps": [
            {"action": "RESPOND", "arguments": {"help_id": identity, "text": "I can give one unit"}},
            {"action": "GIVE", "arguments": {"to": self.aliases[0], "asset": "water", "amount": 1}},
        ]}}})
        self.assertEqual([s["accepted"] for s in t["results"][1]["effects"]["steps"]], [True, True])
        self.assertEqual(t["after"]["help_requests"][identity]["status"], "open")

    def test_help_owner_updates_then_closes_without_inferring_fulfillment(self):
        self.commit("communication_1", {self.aliases[0]: {"action": "ASK_HELP", "arguments": {"to": "public", "text": "Need water"}}})
        identity = f"r1:communication_1:{self.aliases[0]}"
        t = self.commit("action", {self.aliases[0]: {"action": "PLAN", "arguments": {"steps": [
            {"action": "UPDATE_HELP", "arguments": {"help_id": identity, "text": "Need food instead"}},
            {"action": "CLOSE_HELP", "arguments": {"help_id": identity, "status": "withdrawn"}},
        ]}}})
        self.assertEqual([s["accepted"] for s in t["results"][0]["effects"]["steps"]], [True, True])
        item = t["after"]["help_requests"][identity]
        self.assertEqual(item["text"], "Need food instead")
        self.assertEqual(item["to"], "public")
        self.assertEqual(item["status"], "withdrawn")
        self.assertEqual(item["closure_basis"], "requester_declaration_not_verified_material_fulfillment")

    def test_nonowner_cannot_close_help(self):
        self.commit("communication_1", {self.aliases[0]: {"action": "ASK_HELP", "arguments": {"to": "public", "text": "Need water"}}})
        identity = f"r1:communication_1:{self.aliases[0]}"
        t = self.commit("action", {self.aliases[1]: {"action": "CLOSE_HELP", "arguments": {"help_id": identity, "status": "resolved"}}})
        self.assertFalse(t["results"][1]["accepted"])
        self.assertEqual(t["after"]["help_requests"][identity]["status"], "open")


if __name__ == "__main__":
    unittest.main()
