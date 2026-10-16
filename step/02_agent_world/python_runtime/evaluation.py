"""Scene-neutral, read-only trajectory extraction and artifact integrity checks.

Scene metrics are opaque descriptive payloads supplied by adapters. No scientific
metric, cooperation detector or model call is inferred from the first run.
"""
from __future__ import annotations

import json
from pathlib import Path

from common import digest, file_hash, read_json, require


def verify_index(root: Path, artifacts: dict) -> None:
    for relative, expected in artifacts.items():
        path = (root / relative).resolve()
        require(path.is_relative_to(root), "Unsafe artifact path")
        require(file_hash(path) == expected["sha256"] and path.stat().st_size == expected["bytes"], f"Artifact mismatch: {relative}")


def select_rows(rows: list[dict], **query) -> list[dict]:
    return [r for r in rows if all(v is None or r.get(k) == v for k, v in query.items())]


def read_experiment(root: Path) -> dict:
    root = root.resolve()
    manifest = read_json(root / "experiment_manifest.json")
    verify_index(root, manifest["artifacts"])
    names = {"action_request": "requests", "action_result": "results", "observation_delivered": "observations",
             "world_event": "world_events", "cognition_update": "cognition", "scene_metrics": "scene_metrics",
             "state_transition": "state_transitions", "world_round_boundary": "environment"}
    rows = {name: [] for name in names.values()}
    rows.update(plan_steps=[], tasks=[], deliveries=[], daily_outcomes=[], system_effects=[])
    statuses = []
    for reference in manifest["runs"]:
        run_path = (root / reference["manifest"]).resolve()
        require(run_path.is_relative_to(root), "Unsafe run path")
        directory, run = run_path.parent, read_json(run_path)
        verify_index(directory, run["artifacts"])
        initial_path = directory / "agents.initial.json"
        initial = read_json(initial_path) if initial_path.exists() else {}
        require(initial or run["status"] == "failed", "Completed run missing initial Agent states")
        latest_x = {alias: state["X"] for alias, state in initial.items()}
        observed = {alias: [] for alias in initial}
        delivered_ids = {alias: set() for alias in initial}
        known_events = {}
        previous, terminal, count = None, None, 0
        with (directory / "events.jsonl").open(encoding="utf-8") as handle:
            for count, line in enumerate(handle, 1):
                event = json.loads(line)
                hashed = event.pop("hash")
                require(event["seq"] == count and event["previous_hash"] == previous and digest(event) == hashed, "Broken event chain")
                previous = hashed
                p, kind = event["payload"], event["type"]
                base = {"run": run["run_id"], "experiment_condition": run["experiment_id"], "seed": run["seed"], "repetition": run["repetition"], "event_seq": count}
                if kind in names:
                    row = {**base, **p}
                    if kind == "action_request":
                        row["agent_id"] = p["actor_id"]
                    elif kind == "observation_delivered":
                        row["agent_id"] = p["recipient"]
                    elif kind == "world_event":
                        row["agent_id"] = p.get("payload", {}).get("actor")
                    rows[names[kind]].append(row)
                if kind == "world_event":
                    require(p["event_id"] not in known_events, "Duplicate WorldEvent id")
                    known_events[p["event_id"]] = p
                elif kind == "observation_delivered":
                    alias = p["recipient"]
                    require(p["visibility"] == [alias], "Observation audience mismatch")
                    for received in p["content"].get("events", []):
                        require(received["event_id"] in known_events and received == known_events[received["event_id"]]
                                and alias in received["visibility"], "Unrecorded, changed or unauthorized WorldEvent delivery")
                        rows["deliveries"].append({**base, "agent_id": alias, "round": p["round"], "phase": p["phase"],
                            "observation_id": p["observation_id"], "world_event_id": received["event_id"],
                            "source_request_id": received.get("request_id"), "source_round": received["round"],
                            "new_to_recipient": received["event_id"] not in delivered_ids[alias]})
                        delivered_ids[alias].add(received["event_id"])
                    observed[alias].append(p)
                elif kind == "state_transition":
                    alias = p["agent_id"]
                    require(p["before"] == latest_x[alias], "Broken X transition chain")
                    latest_x[alias] = p["after"]
                elif kind == "action_result":
                    for index, step in enumerate(p.get("effects", {}).get("steps", []), 1):
                        rows["plan_steps"].append({**base, "round": p["round"], "phase": p["phase"], "agent_id": p["agent_id"],
                            "parent_request_id": p["request_id"], "step_index": index, **step})
                terminal = event
        require(previous == run["final_event_hash"], "Final event hash mismatch")
        if run["status"] == "completed":
            require(terminal and terminal["type"] == "run_completed", "Missing completion event")
            states = read_json(directory / "agents.final.json")
            episodes = read_json(directory / "episodes.json")
            require(digest(read_json(directory / "world.final.json")) == terminal["payload"]["world_hash"], "World hash mismatch")
            for alias, state in states.items():
                require(state["P"] == initial[alias]["P"] and state["M"] == initial[alias]["M"], "P/M changed")
                require(digest(state) == terminal["payload"]["agent_hashes"][alias] and state["X"] == latest_x[alias], "Agent/X hash or transition mismatch")
                require(episodes[alias] == observed[alias], "Episode delivery mismatch")
                require(state["X"]["version"] == len(observed[alias]) and state["X"]["observation_ids"] == [o["observation_id"] for o in observed[alias]], "Observation version mismatch")
                rows["tasks"].extend({"run": run["run_id"], "agent_id": alias, "experiment_condition": run["experiment_id"], **task} for task in state["X"].get("tasks", {}).values())
        statuses.append({"run": run["run_id"], "status": run["status"], "events": count, "hash_check": "passed"})
    return {"experiment_status": manifest["status"], "runs": statuses, "rows": rows,
            "scope": "artifact_and_delivery_consistency_not_behavioral_validation", "model_recalled": False,
            "visibility": "privileged_researcher_readback_never_agent_input"}
