"""Local Ollama smoke/full trials; preview by default, real inference opt-in."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from public_goods import artifacts
import time

from public_goods.agents.agent_a import AgentRunner
from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.runner import RunConfig
from public_goods.agents.state import initial_state
from public_goods.environment.world import World
from public_goods.experiments.paired_diagnostic import ROOT, IDS, write_json
from public_goods.models.ollama import OllamaClient

RUNNERS = {"A": AgentRunner, "B": BRunner, "C": CRunner}


def plan(scope="smoke", arms=("A", "B", "C"), model="qwen3.5:9b", num_ctx=32768, structured_output=False,
         smoke_case="initial"):
    if scope not in ("smoke", "full"):
        raise ValueError("Unknown trial scope")
    if not arms or len(set(arms)) != len(arms) or any(arm not in RUNNERS for arm in arms):
        raise ValueError("Choose distinct A/B/C arms")
    if smoke_case not in ("initial", "late-punishment") or (scope == "full" and smoke_case != "initial"):
        raise ValueError("Smoke cases apply only to smoke trials")
    actions = 1 if scope == "smoke" else 120
    calls = sum(actions * (1 if arm == "A" else 2) for arm in arms)
    return {"protocol": "ollama-public-goods-v1", "scope": scope, "arms": list(arms),
            "model": model, "requested_num_ctx": num_ctx, "think": False,
            "output_format": "json_schema" if structured_output else "json",
            "smoke_case": smoke_case if scope == "smoke" else None,
            "calls_without_retries": calls, "maximum_calls_with_format_retries": calls * 3,
            "settings": asdict(RunConfig()),
            "execution": (f"Independent focal participant at {'N1 contribution' if smoke_case == 'initial' else 'P10 punishment after scripted N10/P9 history'} for each arm; same observation, fresh X, no proposed action execution or state commit."
                          if scope == "smoke" else "One independent four-person N10-P10 group per arm, with synchronous decisions and settlement."),
            "limits": ["Smoke trials check compatibility in selected contexts, not human likeness or autonomous full-history stability.",
                       "A full single group per arm is a pilot, not a stable ranking.",
                       "Compare A/B/C within this same model. Provider, quantization and sampler defaults differ from DeepSeek.",
                       "Local inference has no remote API fee; runtime and local compute costs remain.",
                       "No human development/evaluation data are sent to the model."]}


def smoke_world(group_id, seed, case):
    world = World(group_id, IDS, seed=seed)
    if case == "late-punishment":
        for round_ in range(10):
            world.resolve_contributions({pid: {"contribution": 8 if round_ == 0 and pid == IDS[2] else 10} for pid in IDS})
            world.advance()
        for _ in range(9):
            world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
            world.resolve_punishments({pid: {"points": [0, 0, 0]} for pid in IDS})
            world.advance()
        world.resolve_contributions({pid: {"contribution": 10} for pid in IDS})
    elif case != "initial":
        raise ValueError("Unknown smoke case")
    return world


def run_trial(output, model, *, scope="smoke", arms=("A", "B", "C"), allow_live=False, smoke_case="initial"):
    protocol = plan(scope, arms, model.model_id, model.num_ctx, getattr(model, "structured_output", False), smoke_case)
    if model.mode != "live" or not allow_live:
        raise ValueError("Real local inference requires explicit execution")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / "run.py"] + [p for folder in ("agents", "environment", "models")
                                   for p in sorted((ROOT / folder).glob("*.py"))]
    sources.append(Path(__file__))
    protocol["source_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    report = {"status": "running", "scope": scope, "arms": {}, "real_model_calls": 0,
              "human_likeness_evaluated": False, "limits": protocol["limits"]}
    started = time.monotonic()
    write_json(output / "plan.json", protocol)
    try:
        protocol["provider"] = model.inspect()
        write_json(output / "plan.json", protocol)
        for arm in arms:
            config = RunConfig(allow_live=True, max_calls=(3 if arm == "A" else 6) if scope == "smoke"
                               else (360 if arm == "A" else 720))
            world = smoke_world(f"{output.name}:{arm}", config.seed, smoke_case if scope == "smoke" else "initial")
            task = RUNNERS[arm](world, model, config, output=output / arm)
            result = {"status": "running"}
            try:
                if scope == "full":
                    result["summary"] = task.run()
                else:
                    observation = world.observe(IDS[0])
                    task._write_json("observation.json", observation)
                    events = world.audit_log()
                    result["action"] = task._decide(IDS[0], observation)
                    if arm != "A":
                        result["judgment"] = task._pending[IDS[0]]["state"]["X"]["judgment"]
                        if any(state.snapshot() != initial_state() for state in task.states.values()):
                            raise ValueError("Smoke probe changed committed agent state")
                    if world.audit_log() != events:
                        raise ValueError("Smoke probe executed an action")
                    result.update(action_executed=False, state_committed=False)
                result["status"] = "completed"
            except Exception as exc:
                result.update(status="failed", failure=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                if arm != "A":
                    task._pending.clear()
                    for updater in task.updaters.values():
                        updater.discard()
                result.update(attempts=len(task.calls), real_model_calls=sum(c["network_requests"] for c in task.calls),
                              invalid_outputs=sum(c["status"] == "invalid_output" for c in task.calls),
                              elapsed_seconds=sum(c["elapsed_seconds"] for c in task.calls))
                report["arms"][arm] = result
                report["real_model_calls"] += result["real_model_calls"]
                write_json(output / "report.json", report)
        model.inspect()  # Confirm the pinned model/server identity still matches.
        report["status"] = "completed"
    except Exception as exc:
        report.update(status="failed", failure=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(output / "report.json", report)
    return report


@artifacts.compact_cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Run the real local model; default only prints the plan")
    parser.add_argument("--scope", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--arms", nargs="+", choices=tuple(RUNNERS), default=list(RUNNERS))
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--structured-output", action="store_true", help="Constrain decoding to the experiment output schema; preserve independent Python validation")
    parser.add_argument("--evidence-paths", choices=("free", "visible"), default=None,
                        help="Default with structured output: constrain references to actual observation paths")
    parser.add_argument("--smoke-case", choices=("initial", "late-punishment"), default="initial")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    model = OllamaClient(args.model, args.base_url, num_ctx=args.num_ctx, timeout=args.timeout,
                         structured_output=args.structured_output,
                         evidence_paths=args.evidence_paths or ("visible" if args.structured_output else "free"))
    protocol = plan(args.scope, args.arms, args.model, args.num_ctx, args.structured_output, args.smoke_case)
    if not args.execute:
        print(json.dumps(protocol, ensure_ascii=False, indent=2))
        return
    if args.output is None:
        parser.error("--execute requires a new --output directory")
    result = run_trial(args.output, model, scope=args.scope, arms=args.arms, allow_live=True, smoke_case=args.smoke_case)
    print(json.dumps({key: result[key] for key in ("status", "real_model_calls", "elapsed_seconds")}, ensure_ascii=False))


def preview_resume(source):
    """Prove completed/pending data can be retained before any new inference."""
    from public_goods.evaluation.preflight import audit_saved_run
    from public_goods.experiments.recover_trial import recover, OllamaPrefixReplay
    source = Path(source)
    protocol = artifacts.read_json(source / "plan.json")
    report = artifacts.read_json(source / "report.json")
    if protocol["scope"] != "full" or report["status"] != "failed":
        raise ValueError("Resume requires a stopped full local trial")
    if protocol.get("provider", {}).get("provider") != "ollama":
        raise ValueError("Resume requires an Ollama source trial")
    actions = {}
    for arm in protocol["arms"]:
        folder = source / arm
        summary_file = folder / "summary.json"
        if artifacts.exists(summary_file):
            summary = artifacts.read_json(summary_file)
            if summary["status"] == "completed":
                audit = audit_saved_run(folder)
                actions[arm] = {"operation": "reuse_completed", "audit_passed": audit["passed"]}
            elif summary["status"] == "failed":
                audit = recover(folder, replay_class=OllamaPrefixReplay)
                actions[arm] = {"operation": "repair_failed", "prefix_audit": audit}
            else:
                raise ValueError("Unrecognized source summary status")
        elif artifacts.exists(folder / "config.json"):
            raise ValueError("Partially written arm lacks a final summary")
        else:
            actions[arm] = {"operation": "start_unattempted"}
    if not any(row["operation"] == "repair_failed" for row in actions.values()):
        raise ValueError("No exhausted citation failure is available to repair")
    return {"source": str(source.resolve()), "arms": actions, "real_model_calls_in_preview": 0,
            "protocol_deviation": "Visible-only evidence decoding applies after the repair boundary. Original exhausted responses remain recorded; this continuation is an exploratory engineering run."}


def resume_trial(source, output):
    from public_goods.experiments.recover_trial import recover, OllamaPrefixReplay
    source, output = Path(source), Path(output)
    preview = preview_resume(source)
    original = artifacts.read_json(source / "plan.json")
    provider = original["provider"]
    model = OllamaClient(original["model"], provider["endpoint"].removesuffix("/api/chat"),
        num_ctx=provider["requested_num_ctx"], timeout=provider["timeout_seconds"],
        structured_output=True, evidence_paths="visible")
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "protocol": "ollama-repaired-continuation-v1", "recovery": preview,
              "arms": {}, "new_real_model_calls": 0, "human_likeness_evaluated": False,
              "confirmatory_comparison": False}
    plan_record = {**original, "recovery": preview}
    plan_record["original_source_sha256"] = original.get("source_sha256", {})
    names = set(original.get("source_sha256", {})) | {"experiments/recover_trial.py"}
    plan_record["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                    for name in sorted(names)}
    write_json(output / "plan.json", plan_record)
    write_json(output / "report.json", report)
    try:
        current = model.inspect()
        if current["identity"] != provider["identity"]:
            raise ValueError("Installed model/server differs from the original trial")
        plan_record["provider"] = current
        write_json(output / "plan.json", plan_record)
        failed_arm = next(a for a, row in preview["arms"].items() if row["operation"] == "repair_failed")
        template = artifacts.read_json(source / failed_arm / "config.json")
        for arm in original["arms"]:
            operation = preview["arms"][arm]["operation"]
            before = model.sent_calls
            task = None
            entry = {"operation": operation, "status": "running"}
            try:
                if operation == "reuse_completed":
                    artifacts.copy_tree(source / arm, output / arm)
                    entry["summary"] = artifacts.read_json(source / arm / "summary.json")
                elif operation == "repair_failed":
                    entry["recovery"] = recover(source / arm, output / arm, model, replay_class=OllamaPrefixReplay)
                    entry["summary"] = entry["recovery"]["summary"]
                else:
                    config = RunConfig(**{**template["settings"], "max_calls": 360 if arm == "A" else 720})
                    args = {} if arm == "A" else {"prompt_revision": template["prompt_revision"],
                                                   "previous_x_mode": template.get("previous_x_mode", "retain")}
                    world = World(f"{output.name}:{arm}", IDS, seed=config.seed)
                    task = RUNNERS[arm](world, model, config, output / arm, **args)
                    entry["summary"] = task.run()
                entry["status"] = "completed"
            except Exception as exc:
                entry.update(status="failed", failure=f"{type(exc).__name__}: {exc}")
                if task is not None:
                    entry["summary"] = task.summary
                raise
            finally:
                entry["new_real_model_calls"] = model.sent_calls - before
                report["arms"][arm] = entry
                report["new_real_model_calls"] = model.sent_calls
                write_json(output / "report.json", report)
        model.inspect()
        report["status"] = "completed_repaired"
    except Exception as exc:
        report.update(status="failed", failure=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        write_json(output / "report.json", report)
    return report


@artifacts.compact_cli
def main_resume(argv=None):
    parser = argparse.ArgumentParser(description="Resume a stopped local trial with explicit citation repair; preview by default")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        result = preview_resume(args.source)
    else:
        if args.output is None:
            parser.error("--execute requires a new --output directory")
        result = resume_trial(args.source, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
