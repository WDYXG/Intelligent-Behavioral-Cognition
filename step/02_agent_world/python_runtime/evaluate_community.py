"""Offline artifact/hash inspection and descriptive extraction; never calls an API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import digest, file_hash, read_json, require


def verify_index(root: Path, artifacts: dict) -> None:
    for relative, expected in artifacts.items():
        file = (root / relative).resolve()
        require(file.is_relative_to(root), "Unsafe artifact path")
        require(file_hash(file) == expected["sha256"] and file.stat().st_size == expected["bytes"], f"Artifact mismatch: {relative}")


def read_experiment(root: Path) -> dict:
    root = root.resolve()
    config = read_json(root / "experiment_config.json")
    if config.get("schema_version") == "social_framework_v1":
        from evaluation import read_experiment as read_framework
        result = read_framework(root)
        # Community presentation only. The core reader treats scene metrics as
        # opaque, so another scene does not require changes to evaluation.py.
        for metric in result["rows"]["scene_metrics"]:
            for alias, outcome in metric.get("daily_outcomes", {}).items():
                if outcome is not None:
                    result["rows"]["daily_outcomes"].append({k: v for k, v in metric.items() if k in
                        ("run", "repetition", "seed", "round", "phase", "experiment_condition")}
                        | {"agent_id": alias, **outcome})
        return result
    manifest = read_json(root / "experiment_manifest.json")
    verify_index(root, manifest["artifacts"])
    rows = {"requests": [], "results": [], "observations": [], "system_effects": [],
            "plan_steps": [], "daily_outcomes": [], "environment": []}
    statuses = []
    for reference in manifest["runs"]:
        run_path = (root / reference["manifest"]).resolve()
        require(run_path.is_relative_to(root), "Unsafe run path")
        run_dir, run = run_path.parent, read_json(run_path)
        verify_index(run_dir, run["artifacts"])
        previous = None
        event_count = 0
        terminal = None
        with (run_dir / "events.jsonl").open(encoding="utf-8") as handle:
            for event_count, line in enumerate(handle, 1):
                event = json.loads(line)
                recorded = event.pop("hash")
                require(event["seq"] == event_count and event["previous_hash"] == previous and digest(event) == recorded, "Broken event chain")
                previous = recorded
                p = event["payload"]
                base = {"run": run["run_id"], "repetition": run["repetition"], "seed": run["seed"]}
                if event["type"] == "action_request":
                    rows["requests"].append({**base, **p, "agent_id": p["actor_id"], "project_id": p["arguments"].get("project_id"), "recipient": p["arguments"].get("to")})
                elif event["type"] == "action_result":
                    rows["results"].append({**base, **p})
                    for index, step in enumerate(p.get("effects", {}).get("steps", []), 1):
                        rows["plan_steps"].append({**base, "round": p["round"], "phase": p.get("phase", "action"),
                            "agent_id": p["agent_id"], "parent_request_id": p["request_id"], "step_index": index, **step})
                elif event["type"] == "observation_delivered":
                    rows["observations"].append({**base, **p, "agent_id": p["recipient"]})
                elif event["type"] == "world_transaction":
                    phase = p.get("phase", "action")
                    rows["system_effects"].extend({**base, "round": p["round"], "phase": phase, **effect} for effect in p["system_effects"])
                    if phase == "action":
                        for alias, outcome in p["after"].get("last_daily_outcome", {}).items():
                            if outcome is not None:
                                rows["daily_outcomes"].append({**base, "phase": phase, "agent_id": alias, **outcome})
                elif event["type"] == "world_round_boundary":
                    rows["environment"].append({**base, "round": p["after"]["round"], "phase": "environment",
                        "facilities": p["after"].get("facilities", {}), "market": p["after"].get("market", {}),
                        "system_effects": p.get("system_effects", [])})
                terminal = event
        require(previous == run["final_event_hash"], "Final event hash mismatch")
        if run["status"] == "completed":
            require(terminal is not None and terminal["type"] == "run_completed", "Completed manifest without completion event")
            final_states = read_json(run_dir / "agents.final.json")
            initial_states = read_json(run_dir / "agents.initial.json")
            episodes = read_json(run_dir / "episodes.json")
            require(digest(read_json(run_dir / "world.final.json")) == terminal["payload"]["world_hash"], "Final World hash mismatch")
            for alias, state in final_states.items():
                require(state["P"] == initial_states[alias]["P"] and state["M"] == initial_states[alias]["M"], "P/M changed")
                require(digest(state) == terminal["payload"]["agent_hashes"][alias], "Final Agent hash mismatch")
                observed = [r for r in rows["observations"] if r["run"] == run["run_id"] and r["agent_id"] == alias]
                require(state["X"]["observation_ids"] == [o["observation_id"] for o in observed], "X does not match delivered observations")
                require(len(episodes[alias]) == state["X"]["version"] == len(observed), "Episode/X count mismatch")
                original_observations = [{k: v for k, v in row.items() if k not in ("run", "repetition", "seed", "agent_id")} for row in observed]
                require(episodes[alias] == original_observations, "Saved episodes differ from delivered contents")
        statuses.append({"run": run["run_id"], "status": run["status"], "events": event_count, "hash_check": "passed"})
    return {"experiment_status": manifest["status"], "runs": statuses, "rows": rows,
            "scope": "artifact_consistency_and_descriptive_counts_not_behavioral_validation",
            "visibility": "privileged_researcher_readback_never_agent_input", "model_recalled": False}


def select_rows(rows: list[dict], **query) -> list[dict]:
    return [r for r in rows if all(v is None or r.get(k) == v for k, v in query.items())]


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline inspection of saved Python community artifacts; no simulation or API calls.")
    parser.add_argument("experiment_directory", type=Path)
    parser.add_argument("--agent")
    parser.add_argument("--round", type=int)
    parser.add_argument("--run")
    parser.add_argument("--phase", help="e.g. communication_1 or action")
    args = parser.parse_args()
    result = read_experiment(args.experiment_directory)
    query = {"agent_id": args.agent, "round": args.round, "run": args.run, "phase": args.phase}
    filtered = select_rows(result["rows"]["results"], **query)
    steps = select_rows(result["rows"]["plan_steps"], **query)
    days = select_rows(result["rows"]["daily_outcomes"], **query)
    print(json.dumps({"status": result["experiment_status"], "runs": result["runs"], "scope": result["scope"],
        "selected_results": len(filtered), "accepted": sum(r["accepted"] for r in filtered), "rejected": sum(not r["accepted"] for r in filtered),
        "plan_steps": len(steps), "plan_steps_rejected": sum(not s["accepted"] for s in steps),
        "daily_outcomes": len(days), "daily_outcomes_with_unmet_needs": sum(any(d["needs_after"].values()) for d in days),
        "cognition_updates": len(select_rows(result["rows"].get("cognition", []), **query)),
        "world_events": len(result["rows"].get("world_events", [])),
        "final_task_records": len(result["rows"].get("tasks", [])),
        "scene_metrics": len(result["rows"].get("scene_metrics", [])),
        "new_event_deliveries": sum(r["new_to_recipient"] for r in select_rows(result["rows"].get("deliveries", []), **query)),
        "scientific_claims": [], "API_calls": 0}, ensure_ascii=False, indent=2))
    return 0 if result["experiment_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
