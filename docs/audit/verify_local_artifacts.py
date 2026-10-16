"""Read-only local evidence audit. Never projects Persona or calls a model.

Requires privately restored research artifacts. Outputs aggregate checks only.
Usage: python -X utf8 docs/audit/verify_local_artifacts.py --output NEW.json
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEP = ROOT / "step"
RUNTIME = STEP / "02_agent_world/python_runtime"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify():
    sys.path.insert(0, str(RUNTIME))
    from population import load_population
    from trace_readback import read_trace
    config_path = STEP / "03_experiment/configs/community_python_v1.json"
    population, _ = load_population(read(config_path), config_path.parent)
    layers = {}
    for layer in (f"L{i}" for i in range(1, 9)):
        layers[layer] = {
            "stored_slots": sorted({len(m["initial"]["P"][layer]) for m in population}),
            "visible_slots": sorted({len(m["view"][layer]) for m in population}),
            "known_values_per_person": sorted({sum(v["value"] is not None for v in m["initial"]["P"][layer].values()) for m in population}),
        }
    report = {"scope": "read_only_artifact_consistency_not_behavioral_validity", "external_api_calls": 0,
              "persona": {"count": len(population), "hash_and_authorization_verified": True,
                          "layers": layers, "M0_empty": all(not m["initial"]["M0"]["entries"] for m in population)},
              "wvs": {}, "runs": []}
    wvs = STEP / "00_persona/derived/WVS7_CHN_2018/WVS_full_2026-09-17T09-33-15-228Z"
    manifest = read(wvs / "manifest.json")
    counts, identities, evidence_counts = {}, set(), Counter()
    for artifact in manifest["outputs"]:
        path = wvs / artifact["file"]
        assert sha(path) == artifact["sha256"], "WVS artifact hash mismatch"
        count = 0
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                count += 1
                if path.name == "standardized_records.jsonl":
                    identity = item["source_record_id"]
                    assert identity not in identities
                    identities.add(identity)
                    assert len(item["fields"]) == 290
                    assert len({f["field"] for f in item["fields"]}) == 290
                    assert not any(f["field"] == "Q263" for f in item["fields"])
                elif path.name == "evidence.jsonl":
                    identity = item["provenance"]["source_record_id"]
                    assert identity in identities
                    assert item["extensions"]["source_field"] != "Q263"
                    assert item["projection"]["persona_path"] is None
                    evidence_counts[identity] += 1
        counts[path.name] = count
    assert len(identities) == 3036 and counts["evidence.jsonl"] == 880440
    assert set(evidence_counts.values()) == {290}
    assert counts["consistency_flags.jsonl"] == 3036
    source = STEP / "00_persona" / manifest["source"]
    assert sha(source) == manifest["source_sha256"]
    report["wvs"] = {"hashes_match": True, "raw_zip_hash_matches": True, "counts": counts,
                     "unique_respondents": len(identities), "evidence_per_person": 290, "Q263_absent": True}
    spec = importlib.util.spec_from_file_location("audit_phase_ic", STEP / "03_experiment/analysis/validate_phase_ic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for directory in sorted((STEP / "03_experiment/runtime_validation").iterdir()):
        if not directory.is_dir():
            continue
        mp = directory / ("readiness_manifest.json" if directory.name.startswith("two_") else "runtime_validation_manifest.json")
        if not mp.exists():
            continue
        m = read(mp)
        item = {"directory": directory.relative_to(ROOT).as_posix(), "status": m["status"],
                "stage": m.get("stage"), "calls": m.get("cost", {}).get("calls")}
        snapshots = directory / "code_snapshot"
        item["changed_current_python_files"] = [p.name for p in snapshots.glob("*.py")
            if (RUNTIME / p.name).exists() and sha(p) != sha(RUNTIME / p.name)]
        if m.get("runtime_validation", {}).get("passed"):
            validated, _ = module.validate(directory)
            item["readback_checks_passed"] = len(validated["checks"])
            item["events"] = validated["event_count"]
            item["next_model_decision_observed"] = False
        report["runs"].append(item)
    report["current_code_hashes"] = {p.relative_to(ROOT).as_posix(): sha(p)
        for base in [RUNTIME, STEP / "02_agent_world/runtime", STEP / "00_persona/pipeline/src"]
        for p in sorted(base.iterdir()) if p.suffix in (".py", ".ts")}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new filename")
    result = verify()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"persona": result["persona"], "wvs": result["wvs"], "runs": result["runs"]}, ensure_ascii=False, indent=2))
