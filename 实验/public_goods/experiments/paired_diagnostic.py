"""Same-observation B/C probes; scripted histories, never human reference scores."""

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from public_goods import artifacts

from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.cognition import ORIGINAL_REVISION, SCOPE_REVISION, VERIFIED_REVISION, STAGE_REVISION, PROMPT_REVISION
from public_goods.agents.runner import RunConfig
from public_goods.agents.state import initial_state
from public_goods.environment.world import World
from public_goods.models.client import MockModel

ROOT = Path(__file__).resolve().parents[1]
IDS = ("member-a", "member-b", "member-c", "member-d")
CASES = ("N_initial", "N_low_history", "N_high_history", "P_no_received",
         "P_received", "P_low_peers", "P_high_peers")
CONTRASTS = (("N_low_history", "N_high_history"), ("P_no_received", "P_received"),
             ("P_low_peers", "P_high_peers"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_case(name, repetition=0):
    if name not in CASES:
        raise ValueError("Unknown diagnostic case")
    world = World(f"paired-diagnostic-{name}-{repetition}", IDS, 20261021 + repetition)

    def contribute(peers=10):
        world.resolve_contributions({pid: {"contribution": 10 if pid == IDS[0] else peers} for pid in IDS})

    if name.startswith("N_"):
        if name != "N_initial":
            for _ in range(3):
                contribute(0 if name == "N_low_history" else 20)
                world.advance()
    else:
        for _ in range(10):
            contribute()
            world.advance()
        if name in ("P_no_received", "P_received"):
            contribute()
            actions = {pid: {"points": [0, 0, 0]} for pid in IDS}
            if name == "P_received":
                # Researcher-only ordering is used solely to construct the fixture.
                # It is never passed to either model.
                order = world.audit_log()[-1]["result"]["researcher_only_display_order"]
                actions[IDS[1]]["points"][order[IDS[1]].index(IDS[0])] = 2
            world.resolve_punishments(actions)
            world.advance()
        else:
            contribute(0 if name == "P_low_peers" else 20)
    view = world.observe(IDS[0])
    return world, view


def plan(repetitions=1, *, compare_revisions=False, baseline_revision=None):
    if type(repetitions) is not int or not 1 <= repetitions <= 3:
        raise ValueError("Use 1 to 3 diagnostic repetitions")
    if type(compare_revisions) is not bool:
        raise ValueError("compare_revisions must be a boolean")
    if baseline_revision is not None:
        if not compare_revisions:
            raise ValueError("baseline_revision requires compare_revisions")
        if baseline_revision not in (ORIGINAL_REVISION, SCOPE_REVISION, VERIFIED_REVISION, STAGE_REVISION):
            raise ValueError("Baseline must be a preserved earlier revision, not the current revision")
    baseline = SCOPE_REVISION if baseline_revision is None else baseline_revision
    revisions = [baseline, PROMPT_REVISION] if compare_revisions else [PROMPT_REVISION]
    config = RunConfig(max_calls=6)
    return {
        "protocol": "paired-visible-history-v2", "prompt_revisions": revisions, "architecture_version": "tower-aligned-v1",
        "repetitions": repetitions, "cases": list(CASES), "arms": ["B", "C"],
        "calls_without_retries": len(CASES) * repetitions * 4 * len(revisions),
        "calls_with_max_retries": len(CASES) * repetitions * 12 * len(revisions),
        "settings": asdict(config),
        "initial_state": initial_state(),
        "history_source": "Scripted legal World events; no human development or evaluation records used",
        "state_policy": "Independent probe: fresh P/M/X for each arm/case/repetition; historical observations supplied without past model judgments",
        "execution": "One focal participant proposes an action; no probe action is executed, no candidate state is committed",
        "order": "Alternate B/C and C/B by case plus repetition; same local seed inside each pair; revisions interleaved and order counterbalanced",
        "contrasts": [list(pair) for pair in CONTRASTS],
        "semantic_review_rubric": [
            "Does the report distinguish current contribution task from available later deduction stage?",
            "If arithmetic is asserted, is personal return .4 distinguished from total return 1.6 and net changes -.6 / +.6?",
            "Does it avoid inferring all-group deductions or persistent identities from own anonymous feedback?",
            "Does it distinguish facts, expectations and personal appraisal, with relevant visible evidence?",
            "Does it distinguish fixed group membership from anonymous display positions?",
            "For enforcement, is self the sender and are displayed others the targets; are incoming risks kept separate?",
            "Does it distinguish the final experimental condition from the final round of that condition, using the visible round count?",
        ],
        "limits": ["Action changes are descriptive; no preferred direction is a pass criterion.",
                   "Received-points contrast also changes own realized income; it does not isolate a psychological punishment effect.",
                   "Fresh X does not test memory anchoring or autonomous multi-round dynamics.",
                   "No automatic semantic score, human-likeness score or causal mechanism claim.",
                   "Local seeds do not guarantee deterministic provider sampling."],
    }


def probe_key(row):
    return row['repetition'], row['case'], row['arm'], row['prompt_revision']


def resume_records(source, protocol, mode):
    """Retain every attempted probe, including failures; never selectively rerun."""
    source = Path(source).resolve()
    previous_plan = artifacts.read_json(source / 'plan.json')
    previous = artifacts.read_json(source / 'report.json')
    if previous['status'] != 'failed' or previous_plan['execution_mode'] != mode:
        raise ValueError('Resume requires an interrupted run in the same execution mode')
    for key in ('protocol', 'repetitions', 'cases', 'arms', 'prompt_revisions', 'settings'):
        if previous_plan[key] != protocol[key]:
            raise ValueError(f'Resume protocol differs: {key}')
    for name, value in previous_plan['source_sha256'].items():
        # The driver may acquire resume support; all agent/model/rule code must match.
        if name != 'experiments/paired_diagnostic.py' and hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != value:
            raise ValueError(f'Resume source differs: {name}')
    rows, seen = [], set()
    for row in previous['results']:
        key = probe_key(row)
        if (key in seen or row['repetition'] not in range(protocol['repetitions'])
                or row['case'] not in CASES or row['arm'] not in ('B', 'C')
                or row['prompt_revision'] not in protocol['prompt_revisions']):
            raise ValueError('Invalid or duplicate attempted probe in resume source')
        artifact = (source / row['artifact']).resolve()
        saved = artifacts.read_json(artifact/'probe.json')
        if any(saved.get(k) != row.get(k) for k in ('case', 'repetition', 'arm', 'prompt_revision', 'status', 'action', 'judgment', 'attempts')):
            raise ValueError('Resume report differs from saved probe')
        seen.add(key)
        rows.append(dict(row, artifact=str(artifact), inherited_from=str(source)))
    return rows


def run_diagnostic(output, model, repetitions=1, *, allow_live=False, compare_revisions=False,
                   baseline_revision=None, resume_from=None):
    protocol = plan(repetitions, compare_revisions=compare_revisions, baseline_revision=baseline_revision)
    if model.mode == "live" and not allow_live:
        raise ValueError("Live diagnostic requires explicit execution")
    protocol["settings"]["allow_live"] = allow_live
    protocol["execution_mode"] = model.mode
    inherited = resume_records(resume_from, protocol, model.mode) if resume_from is not None else []
    seen = {probe_key(row) for row in inherited}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    if resume_from is not None:
        protocol['resume_from'] = str(Path(resume_from).resolve())
        protocol['resumption_note'] = 'Execution interrupted by a failed probe; retain all attempted probes including failure, resume only unattempted independent probes. This is an execution deviation; agent prompts/settings are unchanged.'
    protocol["source_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for folder in ("agents", "environment", "models", "experiments")
                                 for p in sorted((ROOT / folder).glob("*.py"))}
    write_json(output / "plan.json", protocol)
    report = {"status": "running", "fixture_only": model.mode == "mock", "results": inherited,
              "real_api_calls": sum(x['real_api_calls'] for x in inherited),
              "attempts": sum(x['attempts'] for x in inherited), "new_real_api_calls": 0,
              "semantic_review_status": "not_reviewed",
              "human_likeness_evaluated": False, "limits": protocol["limits"]}
    try:
        for repetition in range(repetitions):
            for index, name in enumerate(CASES):
                expected = None
                for arm_index, arm in enumerate((("B", "C") if (index + repetition) % 2 == 0 else ("C", "B"))):
                    revisions = protocol["prompt_revisions"]
                    order = revisions if (index + repetition + arm_index) % 2 == 0 else list(reversed(revisions))
                    for revision in order:
                        if (repetition, name, arm, revision) in seen:
                            continue
                        world, view = build_case(name, repetition)
                        if expected is not None and view != expected:
                            raise ValueError("Paired observations differ")
                        expected = deepcopy(view)
                        folder = output / f"repeat-{repetition}" / name
                        if len(protocol["prompt_revisions"]) > 1:
                            folder = folder / revision
                        folder = folder / arm
                        runner = {"B": BRunner, "C": CRunner}[arm](
                            world, model, RunConfig(**protocol["settings"]), output=folder, prompt_revision=revision)
                        before = world.audit_log()
                        write_json(folder / "observation.json", view)
                        result = {"case": name, "repetition": repetition, "arm": arm, "prompt_revision": revision, "status": "running",
                                  "observation_sha256": digest(view), "action_executed": False,
                                  "state_committed": False, "artifact": str(folder.relative_to(output))}
                        try:
                            action = runner._decide(IDS[0], view)
                            judgment = runner._pending[IDS[0]]["state"]["X"]["judgment"]
                            if world.audit_log() != before or any(s.snapshot() != initial_state() for s in runner.states.values()):
                                raise ValueError("Independent probe changed committed World or agent state")
                            result.update(status="completed", action=action, judgment=judgment)
                        except Exception as error:
                            result.update(status="failed", failure=f"{type(error).__name__}: {error}")
                            raise
                        finally:
                            runner._pending.clear()
                            for updater in runner.updaters.values():
                                updater.discard()
                            result["attempts"] = len(runner.calls)
                            result["invalid_outputs"] = sum(c["status"] == "invalid_output" for c in runner.calls)
                            result["real_api_calls"] = sum(c["network_requests"] for c in runner.calls)
                            result["finish_reasons"] = [c["response"].get("finish_reason") for c in runner.calls if c["response"]]
                            report["results"].append(result)
                            report["attempts"] += result["attempts"]
                            report["real_api_calls"] += result["real_api_calls"]
                            report["new_real_api_calls"] += result["real_api_calls"]
                            write_json(folder / "probe.json", result)
                            write_json(output / "report.json", report)
        report["status"] = "completed_with_probe_failures" if any(r['status'] != 'completed' for r in report['results']) else "completed"
        report["contrasts"] = []
        for repetition in range(repetitions):
            for arm in ("B", "C"):
                for revision in protocol["prompt_revisions"]:
                    lookup = {r["case"]: r for r in report["results"] if r["arm"] == arm and r["repetition"] == repetition and r["prompt_revision"] == revision}
                    for low, high in CONTRASTS:
                        if lookup[low]['status'] != 'completed' or lookup[high]['status'] != 'completed':
                            report['contrasts'].append({'arm':arm, 'prompt_revision':revision, 'repetition':repetition,
                                                        'from':low, 'to':high, 'status':'missing_failed_probe'})
                            continue
                        first, second = lookup[low]["action"], lookup[high]["action"]
                        amount = lambda a: a["contribution"] if "contribution" in a else sum(a["points"])
                        report["contrasts"].append({"arm": arm, "prompt_revision": revision, "repetition": repetition, "from": low, "to": high,
                                                    "action_changed": first != second, "amount_difference": amount(second) - amount(first)})
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
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--execute", action="store_true", help="Use the live model and existing shared cumulative budget ledger")
    parser.add_argument("--compare-revisions", action="store_true", help="Interleave a preserved earlier revision and the current revision")
    parser.add_argument("--baseline-revision", choices=(ORIGINAL_REVISION, SCOPE_REVISION, VERIFIED_REVISION, STAGE_REVISION),
                        help="Earlier revision to compare with the current revision; requires --compare-revisions")
    parser.add_argument("--resume-from", type=Path, help="Preserve all attempted probes and continue only those not attempted")
    parser.add_argument("--plan", action="store_true", help="Print protocol only")
    args = parser.parse_args(argv)
    try:
        protocol = plan(args.repetitions, compare_revisions=args.compare_revisions, baseline_revision=args.baseline_revision)
    except ValueError as error:
        parser.error(str(error))
    if args.plan:
        print(json.dumps(protocol, ensure_ascii=False, indent=2))
        return
    if args.output is None:
        parser.error("--output is required")
    if args.execute:
        from public_goods.models.deepseek import CNYBudget, DeepSeekClient, load_key
        budget = CNYBudget(ROOT / "runs/live/deepseek_budget_20261020.jsonl")
        try:
            report = run_diagnostic(args.output, DeepSeekClient(load_key(), budget), args.repetitions,
                                    allow_live=True, compare_revisions=args.compare_revisions,
                                    baseline_revision=args.baseline_revision, resume_from=args.resume_from)
        finally:
            budget.close()
    else:
        report = run_diagnostic(args.output, MockModel(), args.repetitions, compare_revisions=args.compare_revisions,
                                baseline_revision=args.baseline_revision, resume_from=args.resume_from)
    print(json.dumps({k: report[k] for k in ("status", "attempts", "real_api_calls", "semantic_review_status")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
