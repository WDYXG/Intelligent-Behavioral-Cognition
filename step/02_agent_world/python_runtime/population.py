"""Read and verify completed Persona artifacts; never project or rewrite Evidence."""
from __future__ import annotations

import copy
from pathlib import Path

from common import read_json, file_hash, legacy_digest, require


def authorized_view(initial: dict, plan: dict) -> dict:
    """Reconstruct only the previously licensed view, not a new Persona mapping."""
    view = {f"L{i}": {} for i in range(1, 9)}
    for layer, items in initial["P"].items():
        require(layer in view, "Unknown P layer")
        for key, value in items.items():
            if key in plan["decision_fields"]:
                provenance = value["provenance"]
                view[layer][key] = {"ontology_key": value["ontology_key"], "value": value["value"],
                    "value_status": value["value_status"], "question": value["source_item"]["title"],
                    "group": value["source_item"]["group"], "observation_time": provenance["observation_time"],
                    "reference_period": provenance["reference_period"], "limitations": provenance["limitations"],
                    "unavailable_reason": value["unavailable_reason"]}
            elif value.get("provenance") is None:
                require(value["value"] is None, "Unproven value in unknown placeholder")
                view[layer][key] = {"ontology_key": value["ontology_key"], "value": None, "value_status": "unknown"}
    return view


def load_population(config: dict, config_dir: Path) -> tuple[list[dict], dict]:
    manifest_path = (config_dir / config["population"]["initialization_manifest"]).resolve()
    root, manifest = manifest_path.parent, read_json(manifest_path)
    require(file_hash(manifest_path) == config["population"]["initialization_manifest_sha256"], "Initialization batch manifest changed")
    require(manifest["status"] == "completed", "Initialization batch is not completed")
    subjects = manifest["subjects"]
    require(len(subjects) == manifest["expected_count"] == config["population"]["expected_count"] == 12,
            "Exactly the completed twelve-person batch is required; no subset fallback")
    require(len({s["source_record_id"] for s in subjects}) == 12, "Duplicate respondent identity")
    expected_ids = config["population"]["expected_record_ids"]
    require(len(expected_ids) == 12 and set(expected_ids) == {s["source_record_id"] for s in subjects}, "Population composition changed")

    def verified(relative: str) -> dict:
        path = (root / relative).resolve()
        require(path.is_relative_to(root), "Initialization artifact escapes its root")
        meta = manifest["artifacts"].get(relative)
        require(meta is not None and file_hash(path) == meta["sha256"], f"Initialization artifact hash mismatch: {path.name}")
        return read_json(path)

    population = []
    for index, subject in enumerate(sorted(subjects, key=lambda s: s["source_record_id"]), 1):
        require(subject["status"] == "completed", "Incomplete subject initialization")
        initial = verified(subject["state_path"])
        prefix = Path(subject["state_path"]).parent.as_posix()
        plan = verified(prefix + "/experiment_role_plan.json")
        view = verified(prefix + "/decision_persona_view.json")["P"]
        routes = verified(prefix + "/route_decisions.json")
        require(initial["schema_version"] == "agent_state_v1.0.0", "Unsupported AgentState schema")
        require(legacy_digest(initial) == subject["initialization_hash"], "Legacy state hash mismatch")
        require(legacy_digest(plan) == initial["initialization_manifest"]["role_plan_sha256"], "RolePlan hash mismatch")
        require(initial["experiment_id"] == config["reuse_authorization"]["source_experiment_id"], "Unapproved source experiment")
        require(initial["source_identity"]["record_id"] == subject["source_record_id"] and initial["source_identity"]["kind"] == "human_grounded", "Identity mismatch")
        require(initial["source_identity"]["dataset"] == config["population"]["dataset"], "Dataset mismatch")
        require(set(initial["P"]) == {f"L{i}" for i in range(1, 9)}, "Missing L1-L8 structure")
        require(plan["learning_enabled"] is False and plan["human_target"] is None, "Unsupported initialization policy")
        require(initial["M0"]["entries"] == [] and initial["X0"]["version"] == 0 and initial["X0"]["observation_ids"] == [], "Nonempty M0/X0 requires a new authorized adapter")
        require(set(plan["decision_fields"]) == set(config["reuse_authorization"]["decision_fields"]), "Runtime may not expand/alter Persona input permission")
        require(view == authorized_view(initial, plan), "Saved decision view differs from authorized view")
        require(len({r["evidence_id"] for r in routes}) == len(routes), "Duplicate routing evidence")
        for layer in view.values():
            for field, value in layer.items():
                if field.startswith("Q"):
                    require(field not in plan["target_closure"]["reserved_fields"], "Target in decision view")
                    require(value["value"] is None or any(r["source_field"] == field and r["route"] == "P_INIT" and r["decision_visible"] for r in routes), "Unrouted decision value")
        population.append({"alias": f"resident_{index:02d}", "initial": copy.deepcopy(initial), "view": copy.deepcopy(view),
            "origin": {"record_id": subject["source_record_id"], "slot": subject.get("selection_slot"),
                       "state_path": subject["state_path"], "initialization_hash": subject["initialization_hash"]},
            "time_alignment": plan["time_alignment"], "target_fields": plan["target_closure"]["reserved_fields"]})
    require(all(p["time_alignment"] == population[0]["time_alignment"] for p in population), "Mixed time alignment")
    return population, {"path": str(manifest_path), "sha256": file_hash(manifest_path), "count": 12,
                        "operation": "read_existing_states_no_projection"}
