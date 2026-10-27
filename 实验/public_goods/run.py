"""公共品实验入口：真人参照、离线验收及轨迹评价。"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

# Allow execution by file path from any working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from public_goods.artifacts import compact_cli


@compact_cli
def deepseek_trial(argv=None):
    from public_goods.agents.agent_a import AgentRunner
    from public_goods.agents.agent_bc import BRunner, CRunner
    from public_goods.agents.runner import RunConfig
    from public_goods.environment.world import World
    from public_goods.evaluation.preflight import trial_budget
    from public_goods.models.deepseek import CNYBudget, DeepSeekClient, ENDPOINT, MODEL, PRICING, load_key

    parser = argparse.ArgumentParser(description="DeepSeek trial plan; real calls require --execute and a local key.")
    parser.add_argument("--execute", action="store_true", help="Send real requests after the trial has been authorized")
    parser.add_argument("--output", type=Path, help="New directory for the three-arm trial")
    parser.add_argument("--key-file", type=Path, help="Local .env path; its contents are never printed")
    parser.add_argument("--budget-limit", help="Explicitly change the cumulative ledger limit after user authorization")
    parser.add_argument("--budget-reason", help="Record the authorization/reason for a cumulative limit change")
    parser.add_argument("--arms", nargs="+", choices=("A", "B", "C"), default=["A", "B", "C"],
                        help="Run only unfinished arms, retaining the same shared budget")
    args = parser.parse_args(argv)
    if len(set(args.arms)) != len(args.arms):
        parser.error("--arms must not contain duplicates")
    if bool(args.budget_limit) != bool(args.budget_reason):
        parser.error("--budget-limit and --budget-reason must be supplied together")
    if args.budget_limit:
        from decimal import Decimal, InvalidOperation
        try:
            requested_limit = Decimal(args.budget_limit)
        except InvalidOperation:
            parser.error("--budget-limit must be a finite positive CNY amount")
        if not requested_limit.is_finite() or requested_limit <= 0:
            parser.error("--budget-limit must be a finite positive CNY amount")
    root = Path(__file__).resolve().parent
    plan = {"endpoint": ENDPOINT, "model": MODEL, "thinking": "disabled", "temperature": 0.7,
            "budget_limit_cny": args.budget_limit or "10", "pricing": PRICING, "trial": trial_budget(),
            "requested_budget_change": {"limit_cny": args.budget_limit, "reason": args.budget_reason} if args.budget_limit else None,
            "budget_policy": "Reserve the documented full context cost before each request; stop if insufficient or usage is unknown.",
            "ledger": str(root / "runs/live/deepseek_budget_20261020.jsonl"),
            "seed": "Only local scheduling is seeded; no provider seed parameter is sent."}
    plan["trial"]["pending"] = ["Execute only within the user's authorized scope; a preview makes no requests"]
    plan["trial"]["arms"] = {arm: plan["trial"]["arms"][arm] for arm in args.arms}
    plan["trial"]["total_groups"] = len(args.arms)
    plan["trial"]["totals"] = {key: sum(row[key] for row in plan["trial"]["arms"].values())
                               for key in plan["trial"]["totals"]}
    from decimal import Decimal
    plan["output_only_peak_cap_cny_without_retries"] = str(
        Decimal(plan["trial"]["totals"]["requested_output_token_ceiling_without_retries"])
        * Decimal(PRICING["output_per_million"]) / 1000000)
    plan["cost_note"] = "Input cost is additional. Execution inherits the existing cumulative ledger limit unless an explicit change is recorded; 10 CNY is the default for a new ledger."
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    if args.output is None:
        parser.error("--execute requires a new --output directory")
    key = load_key(args.key_file)
    # Existing ledgers retain costs/reservations across runs. They are never reset here.
    budget = CNYBudget(plan["ledger"])
    report = {"status": "running", "plan": plan, "arms": {}}
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        if args.budget_limit is not None:
            budget.set_limit(args.budget_limit, args.budget_reason)
        plan["budget_limit_cny"] = budget.snapshot()["limit_cny"]
        plan["budget_before"] = budget.snapshot()
        (args.output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        model = DeepSeekClient(key, budget)
        try:
            for arm, cls in (("A", AgentRunner), ("B", BRunner), ("C", CRunner)):
                if arm not in args.arms:
                    continue
                config = RunConfig(max_calls=360 if arm == "A" else 720, allow_live=True)
                world = World(f"{args.output.name}:{arm}", ["member-a", "member-b", "member-c", "member-d"], seed=config.seed)
                task = cls(world, model, config, output=args.output / arm)
                try:
                    task.run()
                finally:
                    report["arms"][arm] = {"settings": asdict(config), "summary": task.summary}
            report["status"] = "completed"
        except Exception as error:
            report.update({"status": "failed", "failure": f"{type(error).__name__}: {error}"})
            raise
        finally:
            report["budget"] = budget.snapshot()
            (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    finally:
        budget.close()
    print(json.dumps({"status": report["status"], "budget": report["budget"]}, ensure_ascii=False, indent=2))


@compact_cli
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("reference", "score", "compare", "world-demo", "agent-a-demo", "agent-b-demo", "agent-c-demo", "preflight", "deepseek-trial", "ollama-trial", "ollama-resume", "paired-diagnostic", "sequential-diagnostic"),
        help="reference：真人参照；score/compare：评分/比较；*-demo：离线验收；preflight：统一验收；deepseek-trial：试运行计划（默认不调用）",
    )
    # Leave command-specific options (including --help) to the selected module.
    args = parser.parse_args(argv[:1])
    if args.command == "world-demo":
        from public_goods.environment.world import main as run_world
        run_world(argv[1:])
    elif args.command == "agent-a-demo":
        from public_goods.agents.agent_a import main as run_agent
        run_agent(argv[1:])
    elif args.command in ("agent-b-demo", "agent-c-demo"):
        from public_goods.agents.agent_bc import main as run_two_stage
        run_two_stage({"agent-b-demo": "B", "agent-c-demo": "C"}[args.command], argv[1:])
    elif args.command == "preflight":
        from public_goods.evaluation.preflight import main as run_preflight
        run_preflight(argv[1:])
    elif args.command == "deepseek-trial":
        deepseek_trial(argv[1:])
    elif args.command == "ollama-trial":
        from public_goods.experiments.ollama_trial import main as run_local
        run_local(argv[1:])
    elif args.command == "ollama-resume":
        from public_goods.experiments.ollama_trial import main_resume as run_recovery
        run_recovery(argv[1:])
    elif args.command == "paired-diagnostic":
        from public_goods.experiments.paired_diagnostic import main as run_diagnostic
        run_diagnostic(argv[1:])
    elif args.command == "sequential-diagnostic":
        from public_goods.experiments.sequential_diagnostic import main as run_diagnostic
        run_diagnostic(argv[1:])
    else:
        from public_goods.evaluation.human_reference import main as run_evaluation
        run_evaluation(argv)


if __name__ == "__main__":
    main()
