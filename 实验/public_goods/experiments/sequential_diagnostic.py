"""Two consecutive P rounds with committed actions, feedback and private X."""

import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.cognition import PROMPT_REVISION
from public_goods.agents.runner import RunConfig
from public_goods.environment.world import World
from public_goods.experiments.paired_diagnostic import ROOT, IDS, write_json
from public_goods.models.client import MockModel, ModelResponse

SEED = 20261022
GROUP = "sequential-diagnostic-20261022-001"
STAGES = (("P", 1, "contribution"), ("P", 1, "punishment"),
          ("P", 2, "contribution"), ("P", 2, "punishment"))
RUNNERS = {"B": BRunner, "C": CRunner}


def plan():
    return {
        "protocol": "sequential-P1-P2-v1", "prompt_revision": PROMPT_REVISION,
        "arms": ["B", "C"], "participants_per_arm": 4,
        "settings": asdict(RunConfig(seed=SEED, max_calls=96)),
        "scripted_prefix": "N1-N10: all four contribute10; legal World events; no prior model judgments",
        "model_stages": [list(x) for x in STAGES],
        "checkpoint": ["P", 2, "feedback"],
        "calls_without_retries": 64, "calls_with_max_retries": 192,
        "state_policy": "Fresh common-unknown P/M/X at P1, then persistent private X and feedback through P2; no resets between stages",
        "execution": "All four agents decide from stage-start snapshots; execute complete batch, settle and commit X/feedback",
        "order": "Interleave arms B/C, C/B, B/C, C/B across the four stages",
        "semantic_review_rubric": ["Personal feedback versus unknown other-to-other sending",
            "Final condition versus final round", "Actor and target of sending",
            "New observations versus earlier uncertain X", "Visible arithmetic and sources"],
        "limits": ["One group per arm, two P rounds; not a full20-round experiment or formal comparison.",
            "Prior N history is scripted and has no historical model X; N-to-P cognitive continuity is not tested.",
            "Groups share only initial observations; subsequent histories can diverge through their own actions.",
            "No human data, score or independent estimate of memory anchoring is used.",
            "Semantic content needs separate review; successful replay does not establish truth."],
    }


def build_world():
    world = World(GROUP, IDS, SEED)
    for _ in range(10):
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
        world.advance()
    return world


def _batch(task, expected):
    if task.world.stage == "feedback":
        task.world.advance()
    actual = tuple(getattr(task.world, key) for key in ("phase", "round", "stage"))
    if actual != expected:
        raise ValueError(f"Unexpected diagnostic stage: {actual}")
    snapshots = {pid: task.world.observe(pid) for pid in IDS}
    actions = {pid: task._decide(pid, snapshots[pid]) for pid in IDS}
    if task.world.stage == "contribution":
        task.world.resolve_contributions(actions)
    else:
        task.world.resolve_punishments(actions)
    task._after_commit()
    task._flush_events()


def _finish(task, stages_done, failure):
    for updater in task.updaters.values():
        updater.discard()
    task._pending.clear()
    task._flush_events()
    reached = stages_done == len(STAGES)
    replay = World.replay(task.world.audit_log())
    if replay.trajectory() != task.world.trajectory():
        raise ValueError("Checkpoint replay differs from World")
    rows = [r for r in task.world.trajectory() if r["phase"] == "P"]
    costs = [Decimal(c["response"]["cost_cny_upper"]) for c in task.calls
             if c["response"] and c["response"]["cost_cny_upper"] is not None]
    summary = {
        "status": "checkpoint_completed" if reached else "interrupted", "failure": failure,
        "stages_committed": stages_done, "checkpoint_reached": reached,
        "experiment_finished": task.world.stage == "finished",
        "phase": task.world.phase, "round": task.world.round, "stage": task.world.stage,
        "attempts": len(task.calls), "real_api_calls": sum(c["network_requests"] for c in task.calls),
        "invalid_outputs": sum(c["status"] == "invalid_output" for c in task.calls),
        "valid_judgments": sum(c["step"] == "judgment" and c["status"] == "valid" for c in task.calls),
        "valid_decisions": sum(c["step"] == "decision" and c["status"] == "valid" for c in task.calls),
        "model_person_rounds_settled": len(rows), "state_transitions": len(task.state_transitions),
        "replay_verified": True, "fixture_only": task.fixture_only,
        "cost_upper_cny": str(sum(costs, Decimal(0))) if len(costs) == len(task.calls) and costs else None,
        "mean_contribution_by_round": {str(round_): sum(r["contribution"] for r in rows if r["round"] == round_) / 4
                                       for round_ in sorted({r["round"] for r in rows})},
        "total_points_sent_by_round": {str(round_): sum(t["points"] for r in rows if r["round"] == round_ for t in r["targets"])
                                       for round_ in sorted({r["round"] for r in rows})},
    }
    task._write_json("summary.json", summary)
    task._write_json("final_agent_states.json", {pid: s.snapshot() for pid, s in task.states.items()})
    if reached:
        task._append("model_segment.jsonl", rows)
    return summary


def run_diagnostic(output, model, *, allow_live=False):
    if model.mode == "live" and not allow_live:
        raise ValueError("Live sequential diagnostic requires explicit execution")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    protocol = plan()
    protocol["settings"]["allow_live"] = allow_live
    protocol["execution_mode"] = model.mode
    paths = [ROOT / "run.py"] + [p for folder in ("agents", "environment", "models", "experiments")
                                   for p in sorted((ROOT / folder).glob("*.py"))]
    protocol["source_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    write_json(output / "plan.json", protocol)
    tasks, done = {}, dict.fromkeys(RUNNERS, 0)
    report = {"status": "running", "fixture_only": model.mode == "mock", "arms": {},
              "semantic_review_status": "not_reviewed", "human_likeness_evaluated": False}
    failure = None
    try:
        for arm, cls in RUNNERS.items():
            task = cls(build_world(), model, RunConfig(**protocol["settings"]), output / arm)
            tasks[arm] = task
            task._started = True
            task._write_json("segment.json", {k: protocol[k] for k in
                             ("protocol", "scripted_prefix", "model_stages", "checkpoint", "limits")})
        for index, expected in enumerate(STAGES):
            for arm in (("B", "C") if index % 2 == 0 else ("C", "B")):
                _batch(tasks[arm], expected)
                done[arm] += 1
                report["stages_committed"] = dict(done)
                write_json(output / "report.json", report)
        report["status"] = "checkpoint_completed"
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
        report.update(status="failed", failure=failure)
        raise
    finally:
        for arm, task in tasks.items():
            report["arms"][arm] = _finish(task, done[arm], failure)
        report["attempts"] = sum(r["attempts"] for r in report["arms"].values())
        report["real_api_calls"] = sum(r["real_api_calls"] for r in report["arms"].values())
        if hasattr(model, "budget"):
            report["budget"] = model.budget.snapshot()
        write_json(output / "report.json", report)
    return report


class _RecordedModel(MockModel):
    """Replay every original response, including invalid attempts, without HTTP."""
    def __init__(self, calls):
        super().__init__()
        self.calls = iter(calls)

    def complete(self, request):
        recorded = next(self.calls, None)
        if recorded is None or asdict(request) != recorded["request"]:
            raise ValueError("Reconstructed request differs from recorded call")
        self.requests.append(request)
        return ModelResponse(**recorded["response"])


def audit_saved_diagnostic(output):
    """Reconstruct exact requests, previous X, commits and feedback offline."""
    output = Path(output)
    read = lambda p: json.loads(p.read_text())
    rows = lambda p: [json.loads(s) for s in p.read_text().splitlines()]
    protocol, report = read(output / "plan.json"), read(output / "report.json")
    if report["status"] != "checkpoint_completed":
        raise ValueError("Only a completed checkpoint can pass this audit")
    result = {}
    for arm, cls in RUNNERS.items():
        folder = output / arm
        config, calls = read(folder / "config.json"), rows(folder / "calls.jsonl")
        model = _RecordedModel(calls)
        task = cls(build_world(), model, RunConfig(**protocol["settings"]), prompt_revision=config["prompt_revision"])
        if config["settings"] != protocol["settings"] or any(
                config.get(key) != value for key, value in task._config_metadata().items()):
            raise ValueError("Recorded settings or agent metadata differ")
        for expected in STAGES:
            _batch(task, expected)
        if next(model.calls, None) is not None:
            raise ValueError("Unexpected extra recorded call")
        checks = [task.world.audit_log() == rows(folder / "events.jsonl"),
                  task.judgments == rows(folder / "judgments.jsonl"),
                  task.state_transitions == rows(folder / "state_transitions.jsonl"),
                  {pid: s.snapshot() for pid, s in task.states.items()} == read(folder / "final_agent_states.json"),
                  [r for r in task.world.trajectory() if r["phase"] == "P"] == rows(folder / "model_segment.jsonl"),
                  all(s.snapshot()["version"] == 6 for s in task.states.values())]
        for actual, recorded in zip(task.calls, calls):
            for key in ("call_id", "participant_id", "phase", "round", "stage", "step", "attempt",
                        "input_bytes", "status", "action", "judgment", "normalizations", "error"):
                checks.append(actual.get(key) == recorded.get(key))
        if not all(checks) or len(task.calls) != len(calls):
            raise ValueError("Committed checkpoint trace differs from offline reconstruction")
        result[arm] = {"passed": True, "calls_checked": len(calls), "judgments_checked": len(task.judgments),
                       "state_transitions_checked": len(task.state_transitions), "final_state_version_per_agent": 6,
                       "model_person_rounds": 8}
    return {"passed": True, "arms": result,
            "source_matches_frozen_plan": all(hashlib.sha256((ROOT / n).read_bytes()).hexdigest() == h
                                               for n, h in protocol["source_sha256"].items()),
            "real_api_calls_in_audit": 0,
            "scope": "Exact request/state/World/retry replay; semantic correctness reviewed separately"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, indent=2))
        return
    if args.audit:
        audit = audit_saved_diagnostic(args.audit)
        write_json(args.audit / "audit.json", audit)
        print(json.dumps(audit, ensure_ascii=False))
        return
    if args.output is None:
        parser.error("--output is required")
    if args.execute:
        from public_goods.models.deepseek import CNYBudget, DeepSeekClient, load_key
        budget = CNYBudget(ROOT / "runs/live/deepseek_budget_20261020.jsonl")
        try:
            report = run_diagnostic(args.output, DeepSeekClient(load_key(), budget), allow_live=True)
        finally:
            budget.close()
    else:
        report = run_diagnostic(args.output, MockModel())
    print(json.dumps({k: report[k] for k in ("status", "attempts", "real_api_calls")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
