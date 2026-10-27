"""Counterfactual single-step probes from saved, exactly replayed agent states.

No human scores, new World actions or long-term state changes are involved.
The old reports, including their errors, are retained in the source run.
"""

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from public_goods import artifacts

from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.cognition import CognitiveUpdater, STAGE_REVISION, PROMPT_REVISION
from public_goods.agents.runner import RunConfig
from public_goods.environment.world import World
from public_goods.experiments.paired_diagnostic import ROOT, write_json, digest
from public_goods.experiments.sequential_diagnostic import _RecordedModel
from public_goods.models.client import MockModel

RUNNERS = {"B": BRunner, "C": CRunner}
# Fixed before new outputs: one stable-history case and three factual-error scopes.
# Same participant/scope selection in both source arms. This is targeted diagnosis.
CHECKPOINTS = (("N", 4, "contribution", "member-a"),
               ("P", 1, "contribution", "member-c"),
               ("P", 2, "punishment", "member-b"),
               ("P", 10, "punishment", "member-a"))
VARIANTS = (("v4_retain", STAGE_REVISION, "retain"),
            ("v5_retain", PROMPT_REVISION, "retain"),
            ("v5_omit", PROMPT_REVISION, "omit"))


def rows(path):
    return [json.loads(line) for line in artifacts.read_text(path).splitlines() if line.strip()]


def checkpoint_name(key):
    return "-".join(map(str, key))


def load_checkpoints(folder, arm):
    """Replay all original requests; capture only state already available then."""
    folder = Path(folder)
    config = artifacts.read_json(folder / "config.json")
    if config["implementation"] != arm or config["prompt_revision"] != STAGE_REVISION:
        raise ValueError("This diagnosis requires a preserved v4 run of the specified arm")
    calls, events = rows(folder / "calls.jsonl"), rows(folder / "events.jsonl")
    first = events[0]
    replay_model = _RecordedModel(calls)
    task = RUNNERS[arm](World(first["group_id"], first["participants"], first["seed"]),
                       replay_model, RunConfig(**config["settings"]), prompt_revision=STAGE_REVISION)
    captured, decide = {}, task._decide

    def capture(pid, observation):
        key = (observation["phase"], observation["round"], observation["stage"], pid)
        if key in CHECKPOINTS:
            captured[key] = {"events": task.world.audit_log(), "observation": deepcopy(observation),
                             "store": deepcopy(task.states[pid])}
        return decide(pid, observation)

    task._decide = capture
    task.run()
    if (next(replay_model.calls, None) is not None or len(task.calls) != len(calls)
            or task.world.audit_log() != events
            or task.state_transitions != rows(folder / "state_transitions.jsonl")
            or task.world.trajectory() != rows(folder / "trajectory.jsonl")
            or set(captured) != set(CHECKPOINTS)):
        raise ValueError("Saved source does not match complete request/state/World replay")
    return captured


def plan(repetitions=2):
    if type(repetitions) is not int or not 1 <= repetitions <= 3:
        raise ValueError("Use 1 to 3 preselected repetitions")
    return {
        "protocol": "saved-history-X-diagnostic-v1", "repetitions": repetitions,
        "arms": list(RUNNERS), "checkpoints": [list(k) for k in CHECKPOINTS],
        "variants": [{"name": n, "prompt_revision": r, "previous_x_mode": m} for n, r, m in VARIANTS],
        "calls_without_retries": repetitions * len(CHECKPOINTS) * len(RUNNERS) * len(VARIANTS) * 2,
        "calls_with_max_retries": repetitions * len(CHECKPOINTS) * len(RUNNERS) * len(VARIANTS) * 6,
        "settings": asdict(RunConfig(seed=20261024, max_calls=6)),
        "pairing": "Within each arm/checkpoint/repetition, retain the identical World observation, history, P/M and state version. v5 retain/omit differ only in judgment-input agent_state.X; scope metadata is held fixed. Decision receives its own newly generated X in both modes.",
        "execution": "One proposed action per independent probe. Do not execute actions or commit candidate states. Rotate variant order and reverse on alternating repetitions. Preserve failed attempts and stop on failure.",
        "semantic_rubric": ["Payment: distinguish 20 contribution tokens each round from 25 income once at P start.",
            "History: check any all/always/same-value claim against all visible rounds; retain rare values.",
            "Scope: distinguish current revealed contributions, unknown current choices, and past feedback.",
            "Sending: distinguish own received/sent points from unobserved other-to-other sending."],
        "limits": ["Targeted saved contexts, not a random sample or full autonomous interaction.",
            "Source B/C histories differ; only within-arm variant comparisons are paired.",
            "v4/v5 comparison tests the combined factual-context change, not its individual components.",
            "Omitting old X removes its text, not past actions/feedback. No action change does not rule out other anchoring sources.",
            "Local seeds are logged but not sent to this provider; repeats are stochastic, not deterministic pairs.",
            "Two repeats cannot establish a stable causal effect or human likeness. No preferred action is a pass criterion."],
    }


def run_diagnostic(output, model, sources, repetitions=2, *, allow_live=False):
    protocol = plan(repetitions)
    if model.mode == "live" and not allow_live:
        raise ValueError("Live diagnostic requires explicit execution")
    if set(sources) != set(RUNNERS):
        raise ValueError("Both B and C saved sources are required")
    # Finish all read-only replay validation before creating files or making calls.
    contexts = {arm: load_checkpoints(folder, arm) for arm, folder in sources.items()}
    protocol["settings"]["allow_live"] = allow_live
    protocol["execution_mode"] = model.mode
    protocol["sources"] = {arm: {"folder": str(Path(folder).resolve()),
        "sha256": {name: hashlib.sha256(artifacts.read_bytes(Path(folder) / name)).hexdigest()
                   for name in ("config.json", "calls.jsonl", "events.jsonl", "state_transitions.jsonl", "trajectory.jsonl")}}
        for arm, folder in sources.items()}
    protocol["source_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("agents", "environment", "models", "experiments") for p in sorted((ROOT / folder).glob("*.py"))}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", protocol)
    report = {"status": "running", "fixture_only": model.mode == "mock", "results": [],
              "attempts": 0, "real_api_calls": 0, "semantic_review_status": "not_reviewed",
              "human_likeness_evaluated": False, "limits": protocol["limits"]}
    try:
        for repetition in range(repetitions):
            for index, key in enumerate(CHECKPOINTS):
                for arm in (list(RUNNERS) if (index + repetition) % 2 == 0 else list(reversed(RUNNERS))):
                    checkpoint = contexts[arm][key]
                    view, store = checkpoint["observation"], checkpoint["store"]
                    variants = list(VARIANTS[index % len(VARIANTS):] + VARIANTS[:index % len(VARIANTS)])
                    if repetition % 2:
                        variants.reverse()
                    paired_inputs = []
                    for name, revision, mode in variants:
                        folder = output / f"repeat-{repetition}" / checkpoint_name(key) / arm / name
                        config = RunConfig(**dict(protocol["settings"], seed=protocol["settings"]["seed"] + repetition))
                        task = RUNNERS[arm](World.replay(checkpoint["events"]), model, config,
                                            prompt_revision=revision, previous_x_mode=mode)
                        # Clone the already validated store, including frozen P/M and episodes.
                        # Only the focal participant is queried; the copy is never committed.
                        task.states[key[3]] = deepcopy(store)
                        task.updaters[key[3]] = CognitiveUpdater(task.states[key[3]], task.include_norms, config.max_response_bytes)
                        task.output = folder
                        folder.mkdir(parents=True, exist_ok=False)
                        task._write_json("config.json", {"implementation": arm, "settings": asdict(config),
                            "model_id": model.model_id, "provider": model.metadata() if hasattr(model, "metadata") else None,
                            "checkpoint": list(key), **task._config_metadata()})
                        task._flush_events()
                        task._write_json("observation.json", view)
                        before = {pid: state.snapshot() for pid, state in task.states.items()}
                        result = {"repetition": repetition, "checkpoint": list(key), "arm": arm, "variant": name,
                            "observation_sha256": digest(view), "initial_focal_state_sha256": digest(store.snapshot()),
                            "artifact": str(folder.relative_to(output)), "action_executed": False, "state_committed": False}
                        try:
                            action = task._decide(key[3], view)
                            if task.world.audit_log() != checkpoint["events"] or before != {pid: s.snapshot() for pid, s in task.states.items()}:
                                raise ValueError("Diagnostic mutated committed state")
                            if revision == PROMPT_REVISION:
                                payload = json.loads(task.calls[0]["request"]["messages"][1]["content"])
                                payload["agent_state"]["X"] = None
                                paired_inputs.append(payload)
                                if any(other != payload for other in paired_inputs):
                                    raise ValueError("The X contrast changed additional input fields")
                            result.update(status="completed", action=action,
                                          judgment=task._pending[key[3]]["state"]["X"]["judgment"])
                        except Exception as error:
                            result.update(status="failed", failure=f"{type(error).__name__}: {error}")
                            raise
                        finally:
                            task._pending.clear()
                            for updater in task.updaters.values():
                                updater.discard()
                            result.update(attempts=len(task.calls), real_api_calls=sum(c["network_requests"] for c in task.calls),
                                invalid_outputs=sum(c["status"] == "invalid_output" for c in task.calls))
                            report["results"].append(result)
                            report["attempts"] += result["attempts"]
                            report["real_api_calls"] += result["real_api_calls"]
                            write_json(folder / "probe.json", result)
                            write_json(output / "report.json", report)
        report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        if hasattr(model, "budget"):
            report["budget"] = model.budget.snapshot()
        write_json(output / "report.json", report)
    return report


@artifacts.compact_cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--B", type=Path)
    parser.add_argument("--C", type=Path)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps(plan(args.repetitions), ensure_ascii=False, indent=2))
        return
    if not all((args.output, args.B, args.C)):
        parser.error("--output, --B and --C are required")
    sources = {"B": args.B, "C": args.C}
    if args.execute:
        from public_goods.models.deepseek import CNYBudget, DeepSeekClient, load_key
        budget = CNYBudget(ROOT / "runs/live/deepseek_budget_20261020.jsonl")
        try:
            result = run_diagnostic(args.output, DeepSeekClient(load_key(), budget), sources, args.repetitions, allow_live=True)
        finally:
            budget.close()
    else:
        result = run_diagnostic(args.output, MockModel(), sources, args.repetitions)
    print(json.dumps({key: result[key] for key in ("status", "attempts", "real_api_calls")}))


if __name__ == "__main__":
    main()
