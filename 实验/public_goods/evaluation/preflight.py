"""Offline acceptance across A/B/C and a model-independent trial budget."""

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from public_goods.agents.agent_a import AgentRunner
from public_goods.agents.agent_bc import BRunner, CRunner, history_references, parse_judgment
from public_goods.agents.capabilities import build_capability_view, ORIGINAL_CAPABILITY_VERSION
from public_goods.agents.cognition import CognitiveUpdater, judgment_schema, judgment_transition_context, ORIGINAL_REVISION, STAGE_REVISION, PROMPT_REVISION
from public_goods.agents.state import AgentState, initial_state
from public_goods.agents.runner import RunConfig, parse_action
from public_goods.environment.world import InvalidAction, World
from public_goods.evaluation.human_reference import load_agent
from public_goods.models.client import MockModel, ModelResponse


ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {"A": AgentRunner, "B": BRunner, "C": CRunner}
SCENARIOS = ("constant", "varied", "long_judgment")
PARTICIPANTS = ("member-a", "member-b", "member-c", "member-d")


def check(condition, message):
    if not condition:
        raise ValueError(message)


def trial_budget(groups_per_arm=1):
    check(type(groups_per_arm) is int and groups_per_arm > 0, "Use a positive integer group count")
    arms = {}
    for arm in RUNNERS:
        settings = RunConfig(max_calls=360 if arm == "A" else 720)
        decisions = 4 * (10 + 10 + 10)  # N contribution, P contribution, P allocation.
        judgments = 0 if arm == "A" else decisions
        calls = decisions + judgments
        output_cap = decisions * settings.max_output_tokens + judgments * settings.max_judgment_tokens
        arms[arm] = {
            "groups": groups_per_arm,
            "calls_without_retries": calls * groups_per_arm,
            "calls_with_max_retries": min(settings.max_calls, calls * settings.max_attempts) * groups_per_arm,
            "requested_output_token_ceiling_without_retries": output_cap * groups_per_arm,
            "requested_output_token_ceiling_with_retries": output_cap * settings.max_attempts * groups_per_arm,
            "per_group_settings": asdict(settings),
        }
    total_keys = ("calls_without_retries", "calls_with_max_retries",
                  "requested_output_token_ceiling_without_retries", "requested_output_token_ceiling_with_retries")
    return {
        "purpose": "Engineering trial, not a powered behavioral comparison or formal sample-size decision.",
        "groups_per_arm": groups_per_arm,
        "total_groups": 3 * groups_per_arm,
        "arms": arms,
        "totals": {key: sum(row[key] for row in arms.values()) for key in total_keys},
        "input_tokens": None, "estimated_cost": None,
        "cost_formula": "sum(provider-billed tokens by price category * price per million / 1000000)",
        "pending": ["Provider and exact model version", "Tokenizer and actual usage fields",
                    "Current input/cache/output prices and billing semantics", "Approved money ceiling",
                    "Adapter timeouts, transport retry policy and usage accounting"],
        "notes": ["Input bytes measure serialized capacity, not tokens or a currency budget.",
                  "Output figures are requested caps, not expected use or guaranteed provider billing caps.",
                  "Retry counts include all attempts, not three retries after an initial attempt.",
                  "Automatic transport retries must not silently multiply the recorded call budget.",
                  "Completing this report does not authorize live calls."],
    }


class AcceptanceModel(MockModel):
    """Deliberately artificial actions/text; never human or LLM behavior evidence."""

    def __init__(self, scenario):
        super().__init__()
        check(scenario in SCENARIOS, "Unknown fixture scenario")
        self.scenario = scenario
        self.positions = Counter()

    def complete(self, request):
        default = super().complete(request)
        if self.scenario == "constant":
            return default
        payload = json.loads(request.messages[1]["content"])
        view = payload.get("observation", payload)
        if payload.get("step") == "judgment":
            value = json.loads(default.text)
            if self.scenario == "long_judgment":
                for field in value:
                    # Exercises schema/byte capacity only; no tokenizer/output-cap claim.
                    text = "离线容量测试文本：" + "样" * 320
                    value[field] = {"assessment": text[:320], "evidence": ["/rules/group_size"], "uncertainty": "high"}
            else:
                value["facts"] = {"assessment": f"当前为第 {view['round']} 轮。",
                                  "evidence": ["/round"], "uncertainty": "low"}
            return ModelResponse(json.dumps(value, ensure_ascii=False))
        key = (view["phase"], view["round"], view["stage"])
        position = self.positions[key]
        self.positions[key] += 1
        if view["stage"] == "contribution":
            # Include 0/20 and different peer values across otherwise identical arms.
            action = {"contribution": [0, 5, 15, 20][(view["round"] + position) % 4]}
        else:
            action = {"points": [10 if (position + slot + view["round"]) % 3 == 0 else 0 for slot in range(3)]}
        return ModelResponse(json.dumps(action))


def _next_valid_call(calls, task, pid, view, step):
    """Audit every retry, including its unchanged context and corrective feedback."""
    baseline, previous_error = None, None
    for attempt in range(1, task.config.max_attempts + 1):
        call = next(calls, None)
        check(call is not None, "Missing call before a World action")
        check(call["participant_id"] == pid and call["step"] == step and call["attempt"] == attempt,
              "Unexpected call order, participant or attempt")
        check(all(call[k] == view[k] for k in ("phase", "round", "stage")), "Call stage mismatch")
        messages = call["request"]["messages"]
        check(len(messages) == (2 if attempt == 1 else 3), "Unexpected extra context")
        size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
        check(size == call["input_bytes"] and size <= task.config.max_input_bytes, "Input capacity mismatch")
        if attempt == 1:
            baseline = messages[:2]
        else:
            check(messages[:2] == baseline, "Retry changed the authorized context")
            expected = "上一尝试未完成有效输出：" + previous_error + "。请基于同一输入返回符合当前结构的 JSON。"
            check(messages[2] == {"role": "user", "content": expected}, "Retry feedback mismatch")
        normalizations = []
        try:
            response = call["response"]
            check(response is not None, "Completed run contains an unresolved response")
            if response.get("finish_reason") not in (None, "stop"):
                raise InvalidAction("Unfinished model output")
            if step == "judgment":
                parse_judgment(response["text"], view, task.implementation == "B", task.config.max_response_bytes,
                               normalizations=normalizations, contract=getattr(task, "judgment_contract_version", 3))
            else:
                parse_action(response["text"], view["stage"], task.config.max_response_bytes)
        except InvalidAction:
            check(call.get("normalizations", []) == normalizations, "Evidence normalization log mismatch")
            check(call["status"] == "invalid_output", "Invalid output was accepted")
            previous_error = call["error"]
            continue
        check(call.get("normalizations", []) == normalizations, "Evidence normalization log mismatch")
        check(call["status"] == "valid", "Valid output was incorrectly rejected")
        return call
    raise ValueError("A committed action has no valid model output")


def audit_run(task):
    """Cross-check calls against the visible state before each committed batch."""
    events = task.world.audit_log()
    initial = events[0]
    replay = World(initial["group_id"], initial["participants"], initial["seed"])
    previous = dict.fromkeys(replay.participants)
    states = []
    aligned = task.implementation != "A" and getattr(task, "judgment_contract_version", 3) >= 4
    stores = {pid: AgentState() for pid in replay.participants} if aligned else {}
    updaters = {pid: CognitiveUpdater(state, task.implementation == "B", task.config.max_response_bytes)
                for pid, state in stores.items()}
    transitions = []
    calls = iter(task.calls)
    count = 0
    for event_index, event in enumerate(events[1:], 1):
        check(event["accepted"], "Acceptance fixtures must not contain rejected World batches")
        operation = event["operation"]
        if operation == "advance":
            replay.advance()
        else:
            snapshots = {pid: replay.observe(pid) for pid in replay.participants}
            pending = {}
            for pid, view in snapshots.items():
                judgment = None
                steps = ("decision",) if task.implementation == "A" else ("judgment", "decision")
                for step in steps:
                    call = _next_valid_call(calls, task, pid, view, step)
                    count += 1
                    check(call["participant_id"] == pid and call["step"] == step
                          and call["status"] == "valid",
                          "Unexpected call order, participant or status")
                    check(all(call[k] == view[k] for k in ("phase", "round", "stage")), "Call stage mismatch")
                    messages = call["request"]["messages"]
                    size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
                    check(size == call["input_bytes"] and size <= task.config.max_input_bytes, "Input capacity mismatch")
                    payload = json.loads(messages[1]["content"])
                    visible = payload if task.implementation == "A" else payload["observation"]
                    check(visible == view, "Request observation differs from the authorized World snapshot")
                    if aligned:
                        expected_keys = {"step", "observation", "agent_state", "capability_view"}
                        if step == "judgment":
                            expected_keys |= {"judgment_schema", "history_references"}
                            if getattr(task, "prompt_revision", ORIGINAL_REVISION) in (STAGE_REVISION, PROMPT_REVISION):
                                expected_keys.add("transition_context")
                                check(payload.get("transition_context") == judgment_transition_context(view, stores[pid].snapshot()["X"], revision=task.prompt_revision),
                                      "Judgment transition scope differs from the committed state")
                        check(set(payload) == expected_keys and payload["step"] == step, "Unexpected model context")
                        check(payload["capability_view"] == build_capability_view(view, revision=getattr(task, "capability_revision", ORIGINAL_CAPABILITY_VERSION)), "Capability view mismatch")
                    if step == "judgment":
                        if aligned:
                            expected_state = CognitiveUpdater.readonly_view(stores[pid].snapshot())
                            if getattr(task, "previous_x_mode", "retain") == "omit":
                                expected_state["X"] = None
                            check(payload["agent_state"] == expected_state,
                                  "Private agent state mismatch")
                            check(payload["judgment_schema"] == judgment_schema(view["phase"], task.implementation == "B", view["stage"], revision=getattr(task, "prompt_revision", ORIGINAL_REVISION)),
                                  "Judgment schema mismatch")
                        else:
                            check(payload["previous_judgment"] == previous[pid], "Private judgment history mismatch")
                        if "history_references" in payload:
                            check(payload["history_references"] == history_references(view), "History references differ from visible events")
                        judgment = parse_judgment(call["response"]["text"], view, task.implementation == "B",
                                                  task.config.max_response_bytes,
                                                  contract=getattr(task, "judgment_contract_version", 3))
                        check(judgment == call["judgment"], "Recorded judgment differs from the response")
                        if aligned:
                            pending[pid] = updaters[pid].prepare(view, judgment)
                    else:
                        if task.implementation != "A":
                            if aligned:
                                check(payload["agent_state"] == CognitiveUpdater.readonly_view(pending[pid]["state"]),
                                      "Decision did not use its own candidate state")
                            else:
                                check(payload["judgment"] == judgment, "Decision did not use its own current judgment")
                        action = parse_action(call["response"]["text"], view["stage"], task.config.max_response_bytes)
                        check(action == call["action"] == event["actions"][pid], "Model action and World execution differ")
                if judgment is not None:
                    previous[pid] = {k: view[k] for k in ("phase", "round", "stage")} | {"judgment": judgment}
                    states.append({"participant_id": pid, **previous[pid]})
            if operation == "contributions":
                replay.resolve_contributions(event["actions"])
            else:
                check(operation == "punishments", "Unexpected World operation")
                replay.resolve_punishments(event["actions"])
            if aligned:
                for pid in replay.participants:
                    transitions.append({"participant_id": pid, "world_event_index": event_index,
                                        "status": "committed", **pending[pid]})
                    updaters[pid].commit(pending[pid])
                if replay.stage == "feedback":
                    for pid in replay.participants:
                        feedback = updaters[pid].prepare_feedback(replay.observe(pid))
                        transitions.append({"participant_id": pid, "world_event_index": event_index,
                                            "status": "committed", **feedback})
                        updaters[pid].commit(feedback)
        check(replay.audit_log()[-1] == event, "World replay event mismatch")
    check(next(calls, None) is None, "Unexpected calls after the final World action")
    check(replay.stage == "finished" and replay.trajectory() == task.world.trajectory(), "Incomplete/different trajectory")
    if task.implementation != "A":
        check(states == task.judgments, "Committed judgment trace mismatch")
    if aligned:
        check(transitions == task.state_transitions, "Committed state transition mismatch")
        if hasattr(task, "states"):
            check(all(stores[pid].snapshot() == task.states[pid].snapshot() for pid in stores), "Final agent state mismatch")
    check(count == (120 if task.implementation == "A" else 240), "Wrong complete-experiment call count")
    people, peers = load_agent(task.output / "trajectory.jsonl")
    check(len(people) == 80 and len(peers) == 120, "Evaluator input counts differ")
    return {"passed": True, "calls_checked": len(task.calls), "valid_steps_checked": count, "person_rounds": len(people),
            "observation_and_state_checked": True, "action_and_replay_checked": True,
            "evaluator_input_checked": True,
            "versioned_state_transitions_checked": len(transitions) if aligned else None}


def audit_saved_run(output):
    """Read-only audit of a completed run; makes no model calls."""
    output = Path(output)
    read_rows = lambda name: [json.loads(line) for line in (output / name).read_text().splitlines()]
    config = json.loads((output / "config.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    check(summary["status"] == "completed", "Only completed runs can pass the full audit")
    world = World.replay(read_rows("events.jsonl"))
    check(world.trajectory() == read_rows("trajectory.jsonl"), "Saved trajectory differs from replay")
    task = SimpleNamespace(world=world, calls=read_rows("calls.jsonl"), output=output,
                           config=RunConfig(**config["settings"]), implementation=config["implementation"],
                           judgments=read_rows("judgments.jsonl") if config["implementation"] != "A" else [],
                           judgment_contract_version=config.get("judgment_contract_version", 3),
                           prompt_revision=config.get("prompt_revision", ORIGINAL_REVISION),
                           previous_x_mode=config.get("previous_x_mode", "retain"),
                           capability_revision=config.get("capability_revision", ORIGINAL_CAPABILITY_VERSION),
                           state_transitions=read_rows("state_transitions.jsonl") if config.get("judgment_contract_version", 3) >= 4 else [])
    if task.implementation != "A" and task.judgment_contract_version >= 4:
        check(config["initial_agent_states"] == {pid: initial_state() for pid in world.participants},
              "Initial state differs from declared common initialization")
    result = audit_run(task)
    result["recorded_source_matches_current"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        for name, digest in config["code_sha256"].items()}
    return result


def profile_requests(task):
    result = {}
    for step in ("judgment", "decision"):
        calls = [call for call in task.calls if call["step"] == step]
        if not calls:
            continue
        sizes = [call["input_bytes"] for call in calls]
        result[step] = {
            "calls": len(calls), "input_bytes_total": sum(sizes), "input_bytes_max": max(sizes),
            "response_bytes_max": max(len(c["response"]["text"].encode("utf-8")) for c in calls),
            "input_capacity_utilization_max": max(sizes) / task.config.max_input_bytes,
            "measured_model_tokens": None,
        }
    return result


def run_preflight(output, groups_per_arm=1):
    budget = trial_budget(groups_per_arm)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "fixture_only": True, "real_api_calls": 0,
              "model_behavior_validated": False, "trial_authorized": False,
              "trial_budget": budget, "scenarios": {}, "source_sha256": {}}
    for path in sorted(ROOT.rglob("*.py")):
        report["source_sha256"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        stream = StringIO()
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT.parent))
        result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
        report["tests"] = {"run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                           "skipped": len(result.skipped), "passed": result.wasSuccessful()}
        (output / "tests.txt").write_text(stream.getvalue())
        check(result.wasSuccessful() and not result.skipped, "Offline test suite did not fully pass; inspect tests.txt")
        for index, scenario in enumerate(SCENARIOS):
            tasks = {}
            for arm, cls in RUNNERS.items():
                world = World(f"acceptance-{scenario}", PARTICIPANTS, seed=20261020 + index)
                task = cls(world, AcceptanceModel(scenario), output=output / scenario / arm)
                task.run()
                tasks[arm] = task
                report["scenarios"].setdefault(scenario, {})[arm] = {
                    "audit": audit_run(task), "profile": profile_requests(task),
                    "summary": task.summary,
                }
            check(asdict(tasks["B"].config) == asdict(tasks["C"].config), "B/C settings differ")
            check(tasks["A"].world.trajectory() == tasks["B"].world.trajectory() == tasks["C"].world.trajectory(),
                  "Identical fixture actions produced different trajectories across arms")
        report["status"] = "passed"
        report["simulated_calls_in_acceptance_runs"] = sum(
            row["summary"]["simulated_calls"] for scenario in report["scenarios"].values() for row in scenario.values())
        report["limits"] = ["Only offline fixtures were used; this is not evidence of human likeness or model improvement.",
                            "Long judgments exercise field/byte limits; actual generation within token caps is untested.",
                            "Full real-model trial still needs provider, tokenizer, prices, adapter checks and authorization."]
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups-per-arm", type=int, default=1, help="Proposed real trial size for budgeting only")
    args = parser.parse_args(argv)
    report = run_preflight(args.output, args.groups_per_arm)
    print(json.dumps({"status": report["status"], "tests": report["tests"],
                      "scenarios": list(report["scenarios"]),
                      "simulated_calls_in_acceptance_runs": report["simulated_calls_in_acceptance_runs"],
                      "proposed_trial": report["trial_budget"]["totals"], "real_api_calls": 0},
                     ensure_ascii=False, indent=2))
