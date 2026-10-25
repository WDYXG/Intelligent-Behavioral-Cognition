"""Architecture-aligned B/C prototype with shared state and capability boundaries."""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from public_goods.agents.runner import BaseRunner, RunConfig, parse_action
from public_goods.environment.world import World
from public_goods.models.client import MockModel


# Re-export the judgment helpers for existing imports and historical audits.
from public_goods.agents.cognition import (
    CONTRACT_VERSION, CognitiveUpdater, DECISION_PROMPT, JUDGMENT_PROMPT,
    NORM_FIELDS, NORM_INSTRUCTION, evidence_exists, history_references,
    judgment_schema, parse_judgment, validate_belief,
    ORIGINAL_REVISION, SCOPE_REVISION, VERIFIED_REVISION, STAGE_REVISION, PROMPT_REVISION, prompt_pair,
    judgment_transition_context,
)
from public_goods.agents.capabilities import (
    build_capability_view, CAPABILITY_VERSION, ORIGINAL_CAPABILITY_VERSION, SCOPE_CAPABILITY_VERSION,
    VERIFIED_CAPABILITY_VERSION, STAGE_CAPABILITY_VERSION,
)
from public_goods.agents.state import AgentState


class TwoStageRunner(BaseRunner):
    source_files = ("agents/agent_bc.py", "agents/cognition.py", "agents/state.py", "agents/capabilities.py")
    include_norms = False
    judgment_contract_version = CONTRACT_VERSION

    def __init__(self, world, model, config=None, output=None, *, prompt_revision=PROMPT_REVISION,
                 previous_x_mode="retain"):
        if previous_x_mode not in ("retain", "omit"):
            raise ValueError("previous_x_mode must be retain or omit")
        self.previous_x_mode = previous_x_mode
        self.prompt_revision = prompt_revision
        self._judgment_prompt, self.decision_prompt = prompt_pair(prompt_revision)
        self.capability_revision = {ORIGINAL_REVISION: ORIGINAL_CAPABILITY_VERSION,
                                    SCOPE_REVISION: SCOPE_CAPABILITY_VERSION,
                                    VERIFIED_REVISION: VERIFIED_CAPABILITY_VERSION,
                                    STAGE_REVISION: STAGE_CAPABILITY_VERSION,
                                    PROMPT_REVISION: CAPABILITY_VERSION}[prompt_revision]
        config = config or RunConfig(max_calls=720)
        self.states = {pid: AgentState() for pid in world.participants}
        self.updaters = {pid: CognitiveUpdater(state, self.include_norms, config.max_response_bytes)
                         for pid, state in self.states.items()}
        self._pending = {}
        self.judgments = []
        self.state_transitions = []
        # 120 actions × two calls × at most three attempts per call.
        super().__init__(world, model, config, output)

    @property
    def _previous(self):
        """Read-only compatibility view; authoritative state belongs to the updater."""
        return {pid: state.snapshot()["X"] for pid, state in self.states.items()}

    @property
    def judgment_prompt(self):
        return self._judgment_prompt + (NORM_INSTRUCTION if self.include_norms else "")

    def _config_metadata(self):
        return {
            "judgment_prompt": self.judgment_prompt,
            "decision_prompt": self.decision_prompt,
            "prompt_revision": self.prompt_revision,
            "previous_x_mode": self.previous_x_mode,
            "previous_x_policy": "Only the judgment input can omit old X. Keep observations, P/M, state version and current Decision judgment unchanged. No deletion from the committed store.",
            "capability_revision": self.capability_revision,
            "judgment_schemas": {f"{phase}/{stage}": judgment_schema(phase, self.include_norms, stage, revision=self.prompt_revision)
                                 for phase, stage in (("N", "contribution"), ("P", "contribution"), ("P", "punishment"))},
            "state_policy": "Frozen common-unknown P/L1-L8 and M; isolated updater prepares X; read-only Decision; commit after accepted World batch. Episodic records are not long-term learning.",
            "judgment_contract": "v4: state and capability views; task-specific norm recognition and enforcement reports; previous reports remain uncertain; original response preserved",
            "judgment_contract_version": CONTRACT_VERSION,
            "architecture_version": "tower-aligned-v1",
            "initial_agent_states": {pid: state.snapshot() for pid, state in self.states.items()},
            "mechanism_scope": {
                "N1_recognition": "candidate report" if self.include_norms else "ablated",
                "N2_descriptive_expectation": "report in X",
                "N3_normative_expectation": "report in X" if self.include_norms else "ablated",
                "personal_appraisal": "current report, not P or internalization" if self.include_norms else "ablated",
                "N4_internalization": "disabled",
                "N5_learning": "long-term disabled; short-term X revisions are recorded",
                "N6_enforcement": "report at available action stage, then independent action" if self.include_norms else "explicit report ablated; legal action remains available",
                "evidence_level": "implemented representations, not established cognitive mechanisms",
            },
            "evidence_validation": "Paths must exist in the observation; this does not prove the assessment is true.",
        }

    def _decide(self, pid, observation):
        updater = self.updaters[pid]
        capability = build_capability_view(observation, revision=self.capability_revision)
        payload = {
            "step": "judgment", "observation": observation,
            "agent_state": updater.readonly_view(self.states[pid].snapshot()),
            "capability_view": capability,
            "judgment_schema": judgment_schema(observation["phase"], self.include_norms, observation["stage"], revision=self.prompt_revision),
            "history_references": history_references(observation),
        }
        # Keep the previous scope metadata in both modes; the sole intervention
        # is the old X body visible to judgment. Decision receives the new X.
        if self.prompt_revision in (STAGE_REVISION, PROMPT_REVISION):
            payload["transition_context"] = judgment_transition_context(observation, payload["agent_state"]["X"], revision=self.prompt_revision)
        if self.previous_x_mode == "omit":
            payload["agent_state"]["X"] = None
        judgment = self._call(
            pid, observation, payload, self.judgment_prompt, "judgment",
            lambda text, **audit: updater.validate(text, observation, **audit),
            self.config.max_judgment_tokens,
        )
        transition = updater.prepare(observation, judgment)
        self._pending[pid] = transition
        action = self._call(
            pid, observation, {"step": "decision", "observation": observation,
                               "agent_state": updater.readonly_view(transition["state"]),
                               "capability_view": capability},
            self.decision_prompt, "decision",
            lambda text: parse_action(text, observation["stage"], self.config.max_response_bytes),
            self.config.max_output_tokens,
        )
        return action

    def _after_commit(self):
        # Failed batches never become remembered judgments or partial World actions.
        rows = [{"participant_id": pid, **deepcopy(self._pending[pid]["state"]["X"])} for pid in self.world.participants]
        transitions = [{"participant_id": pid, "world_event_index": len(self.world.audit_log()) - 1,
                        "status": "committed", **deepcopy(self._pending[pid])} for pid in self.world.participants]
        for pid in self.world.participants:
            self.updaters[pid].commit(self._pending[pid])
        self._pending.clear()
        self.judgments.extend(rows)
        self._append("judgments.jsonl", rows)
        self.state_transitions.extend(transitions)
        self._append("state_transitions.jsonl", transitions)
        if self.world.stage == "feedback":
            # Feedback is an experience, not another model judgment or M update.
            # Ingest immediately so the final round is retained without a next action.
            feedback = {pid: self.updaters[pid].prepare_feedback(self.world.observe(pid))
                        for pid in self.world.participants}
            rows = [{"participant_id": pid, "world_event_index": len(self.world.audit_log()) - 1,
                     "status": "committed", **deepcopy(feedback[pid])} for pid in self.world.participants]
            for pid in self.world.participants:
                self.updaters[pid].commit(feedback[pid])
            self.state_transitions.extend(rows)
            self._append("state_transitions.jsonl", rows)

    def run(self):
        try:
            return super().run()
        finally:
            self._pending.clear()
            for updater in self.updaters.values():
                updater.discard()


class BRunner(TwoStageRunner):
    implementation = "B"
    include_norms = True


class CRunner(TwoStageRunner):
    implementation = "C"


def main(implementation, argv=None):
    parser = argparse.ArgumentParser(description=f"Run offline {implementation} fixtures; no real model calls.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    runner = {"B": BRunner, "C": CRunner}[implementation]
    group = World(f"{implementation}-offline-fixture", ["member-a", "member-b", "member-c", "member-d"], seed=20261019)
    print(json.dumps(runner(group, MockModel(), output=args.output).run(), ensure_ascii=False, indent=2))
