"""Read-only audit of official downloads and working copies; never runs Stata code.

Uses bundled pandas/numpy. Writes 核验结果.json beside this script.
Checks the official ZIP checksum and compares its members with the working copies.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import zipfile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
MIRROR = ROOT / "mirror"
STEM = "HerrmannThoeniGaechter"
PERSON_ROUND = ["subjectid", "p", "secseq", "period"]
GROUP_ROUND = ["groupid", "p", "secseq", "period"]


def audit():
    source = json.loads((ROOT / "来源记录.json").read_text())
    csv = pd.read_csv(MIRROR / f"{STEM}DATA.csv", skiprows=3)
    data = pd.read_stata(MIRROR / f"{STEM}DATA.dta", convert_categoricals=False)
    labeled = pd.read_stata(MIRROR / f"{STEM}DATA.dta", convert_categoricals=True)
    with pd.io.stata.StataReader(MIRROR / f"{STEM}DATA.dta") as reader:
        labels = reader.variable_labels()
        city_labels = reader.value_labels()["city"]

    checks = []

    def check(name, passed, detail=None):
        item = {"name": name, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        checks.append(item)

    official_files_verified = {}
    for item in source["official_file_records"]:
        path = ROOT / source["official_local_files"][item["path"]]
        content = path.read_bytes()
        size_matches = len(content) == item["size"]
        md5_matches = hashlib.md5(content).hexdigest() == item["digest"]
        check(f"official_size:{item['path']}", size_matches)
        check(f"official_md5:{item['path']}", md5_matches)
        official_files_verified[item["path"]] = size_matches and md5_matches

    archive_path = ROOT / source["official_local_files"][f"{STEM}.zip"]
    member_comparisons = {}
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        check("official_zip_crc", bad_member is None, {"bad_member": bad_member})
        for item in source["mirror_files"]:
            name = item["name"]
            official = archive.read(name)
            working = (MIRROR / name).read_bytes()
            byte_identical = official == working
            newline_equivalent = (name.endswith(".do")
                                  and official.replace(b"\r\n", b"\n") == working.replace(b"\r\n", b"\n"))
            detail = {"byte_identical": byte_identical,
                      "equal_after_newline_normalization": newline_equivalent if name.endswith(".do") else None,
                      "official_sha256": hashlib.sha256(official).hexdigest()}
            member_comparisons[name] = detail
            check(f"working_copy_matches_official:{name}", byte_identical or newline_equivalent, detail)
    official_archive_verified = official_files_verified[f"{STEM}.zip"] and bad_member is None

    for item in source["mirror_files"]:
        actual = hashlib.sha256((MIRROR / item["name"]).read_bytes()).hexdigest()
        check(f"download_hash:{item['name']}", actual == item["sha256"])
    readme = (ROOT / source["readme"]["path"]).read_bytes()
    official_readme = next(f for f in source["official_file_records"] if f["path"].endswith(".rtf"))
    check("readme_matches_official_md5", hashlib.md5(readme).hexdigest() == official_readme["digest"])
    check("csv_stata_shape_and_columns", csv.shape == data.shape and list(csv) == list(data))
    comparisons = {}
    for column in data:
        if column in {"sessionid", "p", "city"}:
            equal = csv[column].astype(str).equals(labeled[column].astype(str))
            difference = None
        else:
            equal = np.allclose(csv[column], data[column], equal_nan=True, atol=1e-6, rtol=0)
            difference = float((csv[column] - data[column]).abs().max())
        comparisons[column] = {"equal": bool(equal), "max_absolute_difference": difference}
    check("csv_stata_all_values", all(x["equal"] for x in comparisons.values()), comparisons)

    key_columns = PERSON_ROUND + ["sessionid", "groupid", "mgroupid", "city"]
    check("required_ids_present", not data[key_columns].isna().any().any())
    check("three_peer_rows_per_person_round", data.groupby(PERSON_ROUND).size().eq(3).all())
    constant = ["senderscontribution", "recpun", "groupid", "mgroupid", "city", "sessionid"]
    check("person_round_fields_consistent", data.groupby(PERSON_ROUND)[constant].nunique(dropna=False).eq(1).all().all())
    people = data.drop_duplicates(PERSON_ROUND).copy()
    check("four_people_per_group_round", people.groupby(GROUP_ROUND).subjectid.nunique().eq(4).all())
    check("four_fixed_people_within_each_phase", people.groupby(["groupid", "p", "secseq"]).subjectid.nunique().eq(4).all())
    periods = people.groupby(["subjectid", "p", "secseq"]).period.agg(lambda x: set(x))
    check("ten_complete_rounds_per_person_phase", periods.map(lambda x: x == set(range(1, 11))).all())
    for column, upper in [("senderscontribution", 20), ("otherscontribution", 20)]:
        check(f"range_and_integer:{column}", data[column].between(0, upper).all() and data[column].mod(1).eq(0).all())
    punishment = data.loc[data.p == 1]
    check("punishment_phase_values_present", not punishment[["punishment", "recpun"]].isna().any().any())
    check("punishment_points_valid", punishment.punishment.between(0, 10).all() and punishment.punishment.mod(1).eq(0).all())
    check("received_points_valid", punishment.recpun.between(0, 30).all() and punishment.recpun.mod(1).eq(0).all())
    check("no_punishment_values_structurally_missing_in_N", data.loc[data.p == 0, ["punishment", "recpun"]].isna().all().all())

    group_contributions = {
        key: dict(zip(g.subjectid, g.senderscontribution))
        for key, g in people.groupby(GROUP_ROUND)
    }
    peer_errors = 0
    for _, g in data.groupby(PERSON_ROUND):
        first = g.iloc[0]
        key = tuple(first[x] for x in GROUP_ROUND)
        expected = Counter(v for sid, v in group_contributions[key].items() if sid != first.subjectid)
        peer_errors += Counter(g.otherscontribution) != expected
    check("peer_contributions_match_group", peer_errors == 0, {"inconsistent_person_rounds": peer_errors})
    sent = punishment.groupby(GROUP_ROUND).punishment.sum()
    received = people.loc[people.p == 1].groupby(GROUP_ROUND).recpun.sum()
    check("group_assigned_equals_received_punishment", sent.equals(received), {"group_rounds": len(sent)})

    sequence = people.groupby(["subjectid", "secseq"]).p.first().unstack()
    counts = sequence.fillna(-1).value_counts()
    sequences = [{"phase_1": int(a), "phase_2": None if b == -1 else int(b), "people": int(n)}
                 for (a, b), n in counts.items()]
    np_ids = sequence.index[(sequence[0] == 0) & (sequence[1] == 1)]
    np_people = people.loc[people.subjectid.isin(np_ids)]
    changed_groups = []
    for group_id, group in np_people.groupby("groupid"):
        if set(group.loc[group.secseq == 0, "subjectid"]) != set(group.loc[group.secseq == 1, "subjectid"]):
            changed_groups.append(int(group_id))
    stable = np_people.loc[~np_people.groupid.isin(changed_groups)]
    candidate_counts = stable.groupby("city").agg(people=("subjectid", "nunique"), groups=("groupid", "nunique"))
    session_changes = np_people.groupby("groupid").sessionid.nunique()

    dependencies = set()
    for path in MIRROR.glob("*.do"):
        script = path.read_text(errors="replace")
        dependencies.update(re.findall(r"^\s*(?:qui\s+)?do\s+([\w.]+)", script, flags=re.MULTILINE))
    dependencies = sorted({name if name.endswith(".do") else name + ".do" for name in dependencies})
    missing_dependencies = [name for name in dependencies if not (MIRROR / name).exists()]

    result = {
        "audit_date": "2026-10-19",
        "scope": "data structure and protocol audit; no Agent implementation or model API calls",
        "provenance": {"source_manifest": "来源记录.json", "official_archive_verified": official_archive_verified,
                       "official_archive_path": source["official_local_files"][f"{STEM}.zip"],
                       "working_copy_comparisons": member_comparisons,
                       "note": "Archive verification checks Dryad's published size/MD5 and ZIP CRC. CSV/DTA require byte identity; author scripts allow only CRLF/LF normalization. See individual checks for results."},
        "csv_header_line": 4,
        "counts": {"rows": len(data), "columns": len(data.columns), "people": int(data.subjectid.nunique()),
                   "person_rounds": len(people), "group_ids": int(data.groupid.nunique()),
                   "session_labels": int(data.sessionid.nunique()), "cities": int(data.city.nunique()),
                   "N_person_rounds": int((people.p == 0).sum()), "P_person_rounds": int((people.p == 1).sum()),
                   "identical_peer_rows_beyond_first": int(data.duplicated().sum())},
        "variables": [{"name": name, "label": labels[name], "missing_rows": int(data[name].isna().sum())} for name in data],
        "checks": checks,
        "check_summary": {"passed": sum(x["passed"] for x in checks), "failed": sum(not x["passed"] for x in checks)},
        "sequences": sequences,
        "candidate_reference": {
            "criteria": "N then P, complete ten rounds each, same four members throughout both phases; selection uses protocol and membership only",
            "status": "candidate, not a frozen analysis sample or a development/evaluation split",
            "N_P_people_before_membership_filter": int(np_people.subjectid.nunique()),
            "excluded_membership_change_group_ids": changed_groups,
            "excluded_people_for_membership_change": int(np_people.loc[np_people.groupid.isin(changed_groups)].subjectid.nunique()),
            "people": int(stable.subjectid.nunique()), "groups": int(stable.groupid.nunique()),
            "person_rounds": len(stable), "cities": int(stable.city.nunique()),
            "by_city": {city_labels[int(city)]: {k: int(v) for k, v in row.items()} for city, row in candidate_counts.to_dict("index").items()},
            "group_ids": sorted(int(x) for x in stable.groupid.unique())},
        "limitations": {
            "recipient_id": "Not provided. Equal-contribution recipients cannot be uniquely identified; do not infer persistent recipient identity from row order.",
            "row_grain": "One sender-round-peer record. Retain all three peer rows for punishment; collapse only after verifying shared person-round values for contribution analyses.",
            "missing_N_punishment": "Structural absence, not observed zero punishment.",
            "session_id": {"note": "55 labels are not necessarily 55 independent sessions. Some labels change during/between phases; preserve stable groups when splitting.",
                           "N_P_groups_with_multiple_session_labels": [int(x) for x in session_changes[session_changes > 1].index]},
            "norms": "civic and ruleoflaw are contextual measures, not participant-level roundwise normative expectations; behavioral data cannot directly validate X cognition.",
            "payoffs": "No observed payoff column. Payoffs can be reconstructed from the protocol, not validated against recorded earnings here.",
            "author_code": {"executed": False, "missing_dependencies": missing_dependencies,
                            "note": "Also references derived variables absent from the 22 supplied columns; not a standalone reproducible pipeline."}},
        "protocol": {
            "source": "论文及补充材料.pdf",
            "N_payoff": "20 - contribution + 0.4 * sum(group contributions)",
            "P_payoff": "max(0, N_payoff - 3 * received_points) - assigned_points",
            "P_initial_lump_sum": 25,
            "fresh_tokens_each_round": 20,
            "contribution_range": [0, 20], "points_per_peer_range": [0, 10],
            "original_screen_points_range": [-10, 0],
            "other_contributions": "Displayed in a fresh random order every round; no stable public participant ID.",
            "P_feedback": "Own first-stage income, assigned-point costs, received-point total, income reduction, period income and cumulative income; no identity of punishers.",
            "N_P_disclosure": "P instructions are introduced after N finishes; do not announce future punishment at the start of N.",
            "memory": "Retain own visible experiences across N/P for stable groups, but never attach persistent identities to shuffled peer columns.",
            "control_questions": "Original participants had to answer rule-comprehension questions correctly before each phase.",
            "screen_timers": "Human contribution timers: 90 seconds in rounds 1-2, then 60; punishment: 180 then 120. LLM latency is not a validated human response-time measure.",
            "interpretation": "N/P contrasts in the selected fixed-order sample include phase/experience differences; do not call them an isolated causal effect of punishment."
        },
    }
    (ROOT / "核验结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"counts": result["counts"], "checks": result["check_summary"],
                      "candidate_people": result["candidate_reference"]["people"],
                      "candidate_groups": result["candidate_reference"]["groups"],
                      "official_archive_verified": official_archive_verified}, ensure_ascii=False, indent=2))
    return result["check_summary"]["failed"]


if __name__ == "__main__":
    raise SystemExit(bool(audit()))
