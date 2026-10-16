"""Scene-independent shared Runner; only explicit execution dispatches APIs."""
from __future__ import annotations

import random
import shutil
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agent import Agent
from common import STEP, EventLog, artifact_index, digest, file_hash, read_json, require, sanitized_error, utc_now, validate_config, write_json
from scene_adapter import SceneAdapter, load_scene_class
from gm import GM
from observation import ObservationDispatcher
from llm import CostLedger, LLMClient, preflight
from population import load_population


def run_phase(world: SceneAdapter, gm: GM, dispatcher: ObservationDispatcher, agents: dict, client: LLMClient, log: EventLog,
              config: dict, seed: int, round_no: int, phase: str, phase_index: int, stats: dict) -> None:
    """One shared scheduler for communication and action; sealed decisions then GM."""
    order = list(agents)
    random.Random(seed * 1000003 + round_no * 101 + phase_index).shuffle(order)
    version = world.state["version"]
    log.add("phase_scheduled", {"round": round_no, "phase": phase, "GM_order": order,
                              "concurrency_limit": config["api"]["concurrency"]})
    contexts, prepared_inputs, context_failures = {}, {}, []
    for alias, agent in agents.items():
        dispatcher.deliver(world, agent, f"{phase}:opening", log,
                           schedule={"GM_order_this_phase": order, "sealed_choices": True})
        try:
            draft = agent.context_draft(config, round_no,
                (seed + round_no * 1009 + phase_index * 101 + list(agents).index(alias)) % 4294967296)
            prepared = client.prepare(draft)
            prepared_inputs[alias] = prepared
            contexts[alias] = prepared.context
            # Save exact intended content even if this phase cannot dispatch. The
            # api_request event, not this preflight event, records an API attempt.
            log.add("decision_input", {"agent_id": alias, "round": round_no, "phase": phase,
                "context": contexts[alias], "messages": prepared.messages, "dispatch_status": "prepared_not_dispatched",
                "context_sha256": prepared.audit["context_sha256"], "messages_sha256": prepared.audit["messages_sha256"]})
            log.add("context_budget_check", {"agent_id": alias, "round": round_no, "phase": phase, **prepared.audit})
            if not prepared.audit["budget"]["fits"]:
                context_failures.append({"agent_id": alias, "reason": "mandatory_context_exceeds_input_reservation",
                    "reservation": prepared.audit["budget"]["input_reservation"], "cap": config["api"]["max_input_tokens"]})
        except Exception as exc:
            failure = {"agent_id": alias, "round": round_no, "phase": phase, **sanitized_error(exc)}
            log.add("context_build_failed", failure)
            context_failures.append(failure)
    # Diagnose every actor before dispatching any request. No silent WAIT/Persona trim.
    if context_failures:
        log.add("context_phase_blocked", {"round": round_no, "phase": phase, "failures": context_failures, "API_dispatched": False})
        raise ValueError(f"Phase {phase}: context preparation failed for {len(context_failures)} agent(s); no API dispatched in this phase. Inspect context_budget_check/context_build_failed; mandatory inputs are not truncated")
    requests, failures = [], []
    with ThreadPoolExecutor(max_workers=min(len(agents), config["api"]["concurrency"]), thread_name_prefix="social-agent-api") as executor:
        futures = {executor.submit(client.decide, alias, round_no, prepared): alias for alias, prepared in prepared_inputs.items()}
        for future in as_completed(futures):
            alias = futures[future]
            try:
                decision = future.result()
                request_id = f"r{round_no}:{phase}:{alias}"
                task_id = agents[alias].accept_decision(decision, contexts[alias], config, request_id, log)
                request = {"request_id": request_id, "actor_id": alias, "round": round_no,
                    "phase": phase, "world_version": version, "basis_state_version": agents[alias].X["version"],
                    "basis_cognition_revision": agents[alias].X["cognition_revision"], "task_id": task_id,
                    "basis_observation_id": agents[alias].X["last_observation"]["observation_id"],
                    "action": decision["action"], "arguments": decision["arguments"]}
                log.add("action_request", request)
                requests.append(request)
                stats["requests"] += 1
            except Exception as exc:
                error = {"agent_id": alias, "round": round_no, "phase": phase, **sanitized_error(exc)}
                log.add("decision_failed", error)
                failures.append(error)
    if failures:
        for request in requests:
            result = {"request_id": request["request_id"], "agent_id": request["actor_id"], "task_id": request.get("task_id"),
                "result_id": "result:" + request["request_id"],
                "round": round_no, "phase": phase, "accepted": False, "executed_action": None,
                "rejection_reason": "phase_aborted_due_to_decision_failure", "world_version_before": version,
                "world_version_after": version, "effects": {}, "affected_agents": [request["actor_id"]]}
            log.add("action_result", result)
            agent = agents[request["actor_id"]]
            dispatcher.deliver(world, agent, f"{phase}:cancelled", log, result)
            stats["cancelled"] += 1
        raise ValueError(f"Round {round_no} phase {phase} aborted: {len(failures)} decision failures; no actions from this phase committed; earlier phases and environment updates remain recorded")
    requests.sort(key=lambda r: order.index(r["actor_id"]))
    log.add("collection_barrier", {"round": round_no, "phase": phase, "request_ids": [r["request_id"] for r in requests]})
    log.add("phase_commit_intent", {"round": round_no, "phase": phase, "world_before_hash": digest(world.snapshot()), "requests": requests, "order": order})
    transaction = gm.commit(requests, order, phase)
    log.add("world_transaction", transaction)
    dispatcher.register(transaction["world_events"], log)
    log.add("scene_metrics", world.metrics(transaction))
    by_actor = {}
    for result in transaction["results"]:
        log.add("action_result", result)
        by_actor[result["agent_id"]] = result
        stats["accepted" if result["accepted"] else "rejected"] += 1
        if result["accepted"]:
            kind = result["executed_action"]
            stats["actions"][kind] = stats["actions"].get(kind, 0) + 1
            steps = result["effects"].get("steps", [])
            for step in steps:
                stats["plan_steps_accepted" if step["accepted"] else "plan_steps_rejected"] += 1
                if step["accepted"]:
                    stats["primitive_actions"][step["action"]] = stats["primitive_actions"].get(step["action"], 0) + 1
            if not steps:
                primitive = result["effects"].get("grounded_action", kind)
                stats["primitive_actions"][primitive] = stats["primitive_actions"].get(primitive, 0) + 1
    for alias, agent in agents.items():
        # Scene.observe filters all consequences; core never broadcasts raw effects.
        dispatcher.deliver(world, agent, f"{phase}:feedback", log, by_actor[alias])
        agent.assert_frozen()
    stats["phases_completed"] += 1


def run_one(directory: Path, population: list[dict], config: dict, resolved: dict, ledger: CostLedger, seed: int, repetition: int,
            *, log_factory=EventLog, runtime_validator=None) -> dict:
    directory.mkdir()
    log = log_factory(directory / "events.jsonl")
    try:
        agents = {m["alias"]: Agent(m) for m in population}
        world = load_scene_class(config)(list(agents), config)
        require(world.phases() == load_scene_class(config).phase_names(config), "Scene phase declaration mismatch")
        gm = GM(world)
        dispatcher = ObservationDispatcher(list(agents))
    except BaseException as exc:
        failure = {"schema_version": "community_run_manifest_v1", "status": "failed", "run_id": directory.name,
                   "experiment_id": config["experiment_id"], "repetition": repetition, "seed": seed,
                   "stage": "initialize_scene", "error": sanitized_error(exc), "finished_at": utc_now(),
                   "cost_after": ledger.snapshot(), "behavioral_validation_performed": False}
        log.add("run_failed", failure)
        failure["final_event_hash"] = log.last_hash
        log.close()
        failure["artifacts"] = artifact_index(directory)
        write_json(directory / "run_manifest.json", failure)
        return failure
    manifest = {"schema_version": "community_run_manifest_v1", "status": "running", "started_at": utc_now(),
        "run_id": directory.name, "repetition": repetition, "seed": seed, "experiment_id": config["experiment_id"],
        "population_count": len(population), "learning_enabled": False, "backend": "llm", "model": resolved["model"],
        "config_digest": digest(config), "source_time_alignment": population[0]["time_alignment"],
        "runtime_time_alignment": config["time_alignment"], "runtime_x0_adapter": "empty_source_to_scene_runtime_v2",
        "kernel": config.get("kernel"), "scene_adapter": type(world).__module__ + ":" + type(world).__name__,
        "cognitive_protocol": config.get("cognition", {}).get("protocol", "action_only_v1"), "phases": world.phases(),
        "schedule": "scene_environment_then_sealed_decisions_then_generic_GM_then_scene_filtered_feedback",
        "partial_failure_policy": "earlier phases persist; failed decision phase is not committed; no resume",
        "scenario_conditions_origin": "experiment_assumptions_not_persona_evidence",
        "cost_before": ledger.snapshot(), "trace_hash_format": "python_json_v1_not_TS_event_hashes",
        "durable_recovery": False, "behavioral_validation_performed": False}
    client = LLMClient(config, resolved, ledger, log)
    stats = {"scope": "engineering_descriptive_only", "rounds_completed": 0, "phases_completed": 0,
             "requests": 0, "accepted": 0, "rejected": 0, "cancelled": 0, "actions": {},
             "primitive_actions": {}, "plan_steps_accepted": 0, "plan_steps_rejected": 0}
    try:
        write_json(directory / "agents.initial.json", {a: x.snapshot() for a, x in agents.items()})
        write_json(directory / "world.initial.json", world.snapshot())
        log.add("run_started", {"seed": seed, "agents": list(agents), "world": world.snapshot(),
                               "frozen_hashes": {a: x.initial_frozen_hash for a, x in agents.items()}})
        for round_no in range(1, config["rounds"] + 1):
            boundary = world.begin_round(round_no)
            log.add("world_round_boundary", boundary)
            dispatcher.register(boundary.get("world_events", []), log)
            phases = world.phases()
            require(phases == load_scene_class(config).phase_names(config), "Runtime phases exceed/change the budgeted scene schedule")
            for phase_index, phase in enumerate(phases):
                run_phase(world, gm, dispatcher, agents, client, log, config, seed, round_no, phase, phase_index, stats)
                print(f"{directory.name}: round {round_no}/{config['rounds']} {phase} committed; {len(agents)} agent results", flush=True)
            stats["rounds_completed"] += 1
            end = world.termination()
            log.add("termination_check", {"round": round_no, **end})
            if end["ended"]:
                manifest["termination"] = end
                break
        if runtime_validator is not None:
            manifest["runtime_validation"] = runtime_validator(world, agents, log, ledger)
            log.add("runtime_validation", manifest["runtime_validation"])
            require(manifest["runtime_validation"]["passed"], "Runtime acceptance gates failed; inspect runtime_validation")
        log.add("run_completed", {"world_hash": digest(world.snapshot()), "agent_hashes": {a: digest(x.snapshot()) for a, x in agents.items()}})
        write_json(directory / "world.final.json", world.snapshot())
        write_json(directory / "agents.final.json", {a: x.snapshot() for a, x in agents.items()})
        write_json(directory / "episodes.json", {a: x.episodes for a, x in agents.items()})
        write_json(directory / "basic_statistics.json", stats)
        manifest["status"] = "completed"
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["error"] = sanitized_error(exc)
        log.add("run_failed", {"error": manifest["error"], "world_hash": digest(world.snapshot()), "cost": ledger.snapshot()})
        write_json(directory / "world.failed.json", world.snapshot())
        write_json(directory / "agents.failed.json", {a: x.snapshot() for a, x in agents.items()})
        write_json(directory / "episodes.failed.json", {a: x.episodes for a, x in agents.items()})
        write_json(directory / "basic_statistics.partial.json", stats)
    finally:
        manifest["finished_at"] = utc_now()
        manifest["cost_after"] = ledger.snapshot()
        manifest["final_event_hash"] = log.last_hash
        log.close()
        manifest["artifacts"] = artifact_index(directory)
        write_json(directory / "run_manifest.json", manifest)
    return manifest


def run_experiment(config_path: Path) -> tuple[Path, dict]:
    # Even preflight failures receive a new manifest; no old output is changed.
    stamp = utc_now().replace(":", "-").replace(".", "-")
    directory = STEP / "03_experiment" / "community_experiments" / f"{stamp}_{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    manifest = {"schema_version": "community_experiment_manifest_v1", "status": "starting", "started_at": utc_now(),
                "python_version": sys.version, "runs": [], "scope": "community_execution_not_formal_behavioral_validation",
                "source_files_unchanged": True, "stage": "read_config"}
    ledger = None
    try:
        config = read_json(config_path)
        validate_config(config)
        write_json(directory / "experiment_config.json", config)
        manifest["config_sha256"] = file_hash(config_path)
        manifest["stage"] = "load_existing_12_personas"
        population, source = load_population(config, config_path.parent)
        manifest["population_source"] = source
        write_json(directory / "population.loaded.json", population)
        manifest["stage"] = "API_preflight_no_network"
        resolved = preflight(config)
        manifest["API"] = {k: v for k, v in resolved.items() if k != "key"}
        ledger = CostLedger(config)
        code_dir = directory / "code_snapshot"
        code_dir.mkdir()
        manifest["code"] = {}
        for source_file in sorted(Path(__file__).parent.glob("*.py")):
            shutil.copyfile(source_file, code_dir / source_file.name)
            manifest["code"][source_file.name] = file_hash(source_file)
        manifest["spec_hashes"] = {name: file_hash(STEP / "specs" / name) for name in ("Architecture_Spec.md", "Data_Contract.md", "Experiment_Spec.md")}
        manifest["stage"] = "execute"
        for repetition in range(1, config["repetitions"] + 1):
            run_dir = directory / f"community_repeat_{repetition:03d}"
            run = run_one(run_dir, population, config, resolved, ledger, config["seed"] + repetition - 1, repetition)
            manifest["runs"].append({"run_id": run_dir.name, "status": run["status"], "manifest": f"{run_dir.name}/run_manifest.json"})
            require(run["status"] == "completed", f"{run_dir.name} failed; inspect its run_manifest and events")
        # Check original initialization files were not rewritten by execution.
        require(file_hash(Path(source["path"])) == source["sha256"], "Initialization manifest changed during execution")
        manifest["status"] = "completed"
        manifest["stage"] = "finished"
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["error"] = sanitized_error(exc)
        write_json(directory / "failure.json", {"stage": manifest["stage"], **manifest["error"]})
    finally:
        manifest["finished_at"] = utc_now()
        manifest["cost"] = ledger.snapshot() if ledger else {"calls": 0, "reason": "no_API_dispatch_before_preflight"}
        manifest["artifacts"] = artifact_index(directory)
        for file in directory.rglob("*"):
            if file.is_file() and file.parent != directory:
                manifest["artifacts"][file.relative_to(directory).as_posix()] = {"sha256": file_hash(file), "bytes": file.stat().st_size}
        write_json(directory / "experiment_manifest.json", manifest)
    return directory, manifest
