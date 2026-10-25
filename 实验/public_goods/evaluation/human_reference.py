"""Human reference and offline scoring. No model calls; original data stay intact.

Use 实验/public_goods/run.py with these commands:
reference: prepare the development reference.
score trajectory.jsonl --output result.json: score a complete agent trajectory.
compare B.jsonl C.jsonl --output comparison.json: compare B against C.
The input schema and fixed definitions are saved in data/reference/方案与划分.json.
"""

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT.parents[1] / "数据核验/Herrmann2008"
OUT = ROOT / "data/reference"
PLAN = OUT / "方案与划分.json"
SEED = 20261019
REPEATS = 2000
KEY = ["group_id", "participant_id", "phase", "round"]
CELLS = [(phase, r) for phase in ("N", "P") for r in range(1, 11)]
BINS = [(-20, -11), (-10, -1), (0, 0), (1, 10), (11, 20)]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_source():
    source = json.loads((AUDIT / "来源记录.json").read_text())
    record = next(x for x in source["official_file_records"] if x["path"].endswith(".zip"))
    content = (AUDIT / source["official_local_files"][record["path"]]).read_bytes()
    require(len(content) == record["size"] and hashlib.md5(content).hexdigest() == record["digest"],
            "Official archive checksum changed")
    with zipfile.ZipFile(BytesIO(content)) as archive:
        csv = archive.read("HerrmannThoeniGaechterDATA.csv")
    raw = pd.read_csv(BytesIO(csv), skiprows=3)
    return raw, hashlib.sha256(csv).hexdigest()


def make_plan(raw, digest):
    # Selection and allocation use protocol and IDs, never contribution outcomes.
    phases = raw[["subjectid", "p", "secseq"]].drop_duplicates()
    sequence = phases.pivot(index="subjectid", columns="secseq", values="p")
    ids = sequence.index[(sequence[0] == "N-experiment") & (sequence[1] == "P-experiment")]
    candidate = raw.loc[raw.subjectid.isin(ids)]
    groups, excluded = [], []
    for gid, group in candidate.groupby("groupid"):
        first = set(group.loc[group.secseq == 0, "subjectid"])
        second = set(group.loc[group.secseq == 1, "subjectid"])
        if first != second:
            excluded.append(int(gid))
            continue
        require(len(first) == 4 and group.city.nunique() == 1 and group.mgroupid.eq(gid).all(),
                "Candidate group is not an independent fixed matching unit")
        groups.append({"group_id": int(gid), "city": group.city.iloc[0],
                       "participant_ids": sorted(int(x) for x in first),
                       "session_labels": sorted(group.sessionid.unique().tolist())})
    require(len(groups) == 210, "Candidate sample changed; review before allocating")
    for city in sorted({g["city"] for g in groups}):
        pool = [g for g in groups if g["city"] == city]
        pool.sort(key=lambda g: hashlib.sha256(f"{SEED}:{city}:{g['group_id']}".encode()).hexdigest())
        n_dev = math.floor(0.30 * len(pool) + 0.5)
        for i, g in enumerate(pool):
            g["split"] = "development" if i < n_dev else "evaluation"
    counts = {s: sum(g["split"] == s for g in groups) for s in ("development", "evaluation")}
    return {
        "version": 1, "fixed_on": "2026-10-19", "seed": SEED,
        "source_doi": "10.5061/dryad.87301", "source_csv_sha256": digest,
        "selection": "N→P，四人跨阶段固定，每阶段十轮；排除跨阶段换组，不按行为筛选。",
        "excluded_membership_change_group_ids": excluded,
        "allocation": "各样本池内按 SHA256(seed:city:group_id) 排序，30% 四舍五入作开发集，其余作最终评估集；完整组包含两阶段及全部参与者。",
        "group_counts": counts,
        "scope": "15 个样本池的混合样本；按参与者等权，等价于完整小组等权。不代表总体人口或跨文化泛化。",
        "holdout": "只导出开发集统计；最终评估需显式选择 evaluation，且应在提示词和模型配置冻结后使用。结构核验已读取全部原始记录，不声称数据从未被读取。",
        "dependence": "候选组 groupid=mgroupid，按原文以小组为独立单位；同场不同固定组可分属两集，因此不是独立场次或新样本池验证。",
        "metrics": {
            "primary": {"name": "mean_round_normalized_W1", "range": [0, 1], "direction": "lower_is_better",
                        "formula": "mean over 20 (phase,round) cells of sum(k=0..19, abs(F_agent(k)-F_human(k)))/20",
                        "aggregation": "先在各阶段各轮汇总参与者分布，再算距离；20 格等权，不合并所有轮次，不按城市距离再平均。",
                        "reference": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.wasserstein_distance.html"},
            "secondary": ["逐轮平均贡献的 MAE（代币）", "P均值−N均值的差异及人机绝对误差（代币）",
                          "P阶段按接收者贡献−惩罚者贡献分五档的平均惩罚点数与非零比例（包含零惩罚）",
                          "P阶段第1–9轮受罚/未受罚、本人贡献低于/不低于组均值四类的下一轮贡献变化"],
            "punishment_bins": [list(x) for x in BINS],
            "paper_alignment": "原文 Fig.1 五档及包含不惩罚记录的分母；Fig.2 逐轮贡献。等贡献及更高贡献者受罚均属原文 antisocial punishment；本程序保留等贡献单档。",
            "interpretation": "N→P差值包含阶段与经历变化；受罚后调整为条件关联，均不作净因果解释。采用原文统计口径，但筛选及拆分后数值不是原论文全样本复现。",
            "missing_cell": "没有相应行为机会的条件统计为 null，报告样本数和缺失，不补零、不跳过后宣称全面改善。",
            "uncertainty": {"method": "95% percentile bootstrap", "repeats": REPEATS, "seed": SEED,
                            "human_unit": "在各样本池内重抽完整小组，保留全部20轮", "agent_unit": "重抽完整模拟小组",
                            "comparison": "B−C为主要对比，B−A为辅助；差值负数有利于B。使用同一真人重抽样，模型组独立重抽；不将20轮视作独立样本。",
                            "scope": "当前输出主指标及贡献曲线/阶段差值区间；惩罚与调整先提供描述统计，未生成显著性结论。"}},
        "agent_jsonl_schema": {
            "row": "每位Agent每阶段每轮一行，共80行/组；完整N十轮随后P十轮。",
            "required": ["group_id", "participant_id", "phase", "round", "contribution", "received_points", "targets"],
            "identifiers": "group_id跨模拟重复唯一；participant_id在组内唯一，跨阶段固定。phase只允许N/P。",
            "N": "received_points=null, targets=[]",
            "P": "received_points为0–30整数；targets含3个对象，每个含contribution（接收者0–20贡献）及points（0–10惩罚点）。",
            "failure_policy": "部分组、重复记录、非法行动、结算不一致均报错，不自动剔除或补零。运行失败另记工程日志，正式样本处理在试运行后冻结。"},
        "groups": sorted(groups, key=lambda g: g["group_id"])
    }


def fixed_plan(raw, digest):
    proposed = make_plan(raw, digest)
    if PLAN.exists():
        old = json.loads(PLAN.read_text())
        require(old == proposed, "Saved plan differs; do not silently reallocate or change metrics")
        return old
    OUT.mkdir(parents=True, exist_ok=True)
    save_json(PLAN, proposed)
    return proposed


def human_frames(raw, plan, split):
    ids = [g["group_id"] for g in plan["groups"] if g["split"] == split]
    d = raw.loc[raw.groupid.isin(ids)].rename(columns={
        "groupid": "group_id", "subjectid": "participant_id", "p": "phase", "period": "round",
        "senderscontribution": "contribution", "otherscontribution": "target_contribution",
        "punishment": "assigned_points", "recpun": "received_points"}).copy()
    d["phase"] = d.phase.map({"N-experiment": "N", "P-experiment": "P"})
    require(d.groupby(KEY).size().eq(3).all(), "Not three peer rows per person-round")
    require(d.groupby(KEY)[["contribution", "received_points", "city"]].nunique(dropna=False).eq(1).all().all(),
            "Person-round shared fields differ")
    person = d[KEY + ["city", "contribution", "received_points"]].drop_duplicates(KEY)
    peers = d.loc[d.phase == "P", KEY + ["contribution", "target_contribution", "assigned_points"]]
    validate_frames(person, peers)
    return person, peers


def valid_integer(series, low, high):
    return (pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)
            and series.notna().all() and series.between(low, high).all() and series.mod(1).eq(0).all())


def validate_frames(person, peers):
    require(not person.empty and not person[KEY].isna().any().any(), "Missing person records/IDs")
    require(not person.duplicated(KEY).any(), "Duplicate person-round record")
    require(set(person.phase) == {"N", "P"}, "Both N and P required")
    require(valid_integer(person["round"], 1, 10), "Invalid round")
    require(valid_integer(person.contribution, 0, 20), "Invalid contribution")
    for _, g in person.groupby("group_id"):
        require(g.participant_id.nunique() == 4 and len(g) == 80, "Each fixed group needs 4 people and 80 records")
        require(g.groupby(["participant_id", "phase"]).size().eq(10).all(), "Incomplete person-phase")
        require(g.city.nunique() == 1, "Group changes sample pool")
    require(person.loc[person.phase == "N", "received_points"].isna().all(), "N punishment must be absent")
    require(valid_integer(person.loc[person.phase == "P", "received_points"], 0, 30), "Invalid received points")
    require(set(peers.phase) == {"P"}, "Only P has peer punishment rows")
    require(valid_integer(peers.assigned_points, 0, 10) and valid_integer(peers.target_contribution, 0, 20),
            "Invalid peer punishment/contribution")
    counts = peers.groupby(KEY).size()
    expected = pd.MultiIndex.from_frame(person.loc[person.phase == "P", KEY])
    require(counts.eq(3).all() and set(counts.index) == set(expected), "Each P person-round needs three peer rows")
    lookup = {key: dict(zip(g.participant_id, g.contribution))
              for key, g in person.groupby(["group_id", "phase", "round"])}
    for key, g in peers.groupby(KEY):
        group, pid, phase, r = key
        members = lookup[(group, phase, r)]
        require(g.contribution.eq(members[pid]).all(), "Peer-row sender contribution differs")
        require(Counter(g.target_contribution) == Counter(v for p, v in members.items() if p != pid),
                "Peer contributions do not match the other group members")
    totals = ["group_id", "phase", "round"]
    sent = peers.groupby(totals).assigned_points.sum()
    received = person.loc[person.phase == "P"].groupby(totals).received_points.sum()
    require(np.array_equal(sent.sort_index().to_numpy(), received.sort_index().to_numpy()), "Punishment totals differ")


def group_histograms(person):
    ids = sorted(person.group_id.unique())
    cells = {cell: i for i, cell in enumerate(CELLS)}
    index = {gid: i for i, gid in enumerate(ids)}
    hist = np.zeros((len(ids), 20, 21))
    for row in person.itertuples():
        hist[index[row.group_id], cells[(row.phase, row.round)], int(row.contribution)] += 0.25
    return hist, person.groupby("group_id").city.first().reindex(ids).to_numpy()


def bootstrap_weights(strata, repeats=REPEATS, seed=SEED):
    rng = np.random.default_rng(seed)
    weights = np.zeros((repeats, len(strata)))
    for city in sorted(set(strata)):
        indices = np.flatnonzero(strata == city)
        weights[:, indices] = rng.multinomial(len(indices), np.full(len(indices), 1 / len(indices)), size=repeats)
    return weights / len(strata)


def w1_cells(a, b):
    return np.abs(np.cumsum(a - b, axis=-1)[..., :-1]).sum(axis=-1) / 20


def interval(values):
    return [float(x) for x in np.quantile(values, [0.025, 0.975], axis=0)]


def development_statistics(person, peers):
    hist, strata = group_histograms(person)
    means = hist @ np.arange(21)
    boot = bootstrap_weights(strata) @ means
    curve = []
    for i, (phase, r) in enumerate(CELLS):
        curve.append({"phase": phase, "round": r, "mean": float(means[:, i].mean()),
                      "ci95": interval(boot[:, i]), "probabilities_0_to_20": hist[:, i].mean(axis=0).tolist()})
    gap = means[:, 10:].mean(axis=1) - means[:, :10].mean(axis=1)
    return {"split": "development", "groups": len(hist), "people": len(hist) * 4,
            "person_rounds": len(person), "P_peer_records": len(peers), "round_contributions": curve,
            "phase_means": {phase: float(person.loc[person.phase == phase, "contribution"].mean()) for phase in ("N", "P")},
            "P_minus_N": {"estimate": float(gap.mean()), "ci95": interval(boot[:, 10:].mean(axis=1) - boot[:, :10].mean(axis=1))},
            "behavior": behavior_statistics(person, peers),
            "by_city": [{"city": city, "groups": int(g.group_id.nunique()),
                         "N_mean": float(g.loc[g.phase == "N", "contribution"].mean()),
                         "P_mean": float(g.loc[g.phase == "P", "contribution"].mean())} for city, g in person.groupby("city")],
            "ci_note": "2000次样本池内整组bootstrap；曲线区间为逐点95%区间，不是同时置信带。",
            "evaluation_behavior_exported": False}


def behavior_statistics(person, peers):
    deviation = peers.target_contribution - peers.contribution
    punishment = []
    for low, high in BINS:
        values = peers.loc[deviation.between(low, high), "assigned_points"]
        punishment.append({"deviation": [low, high], "opportunities": len(values),
                           "mean_points": float(values.mean()) if len(values) else None,
                           "positive_rate": float(values.gt(0).mean()) if len(values) else None})
    p = person.loc[person.phase == "P"].sort_values(["group_id", "participant_id", "round"]).copy()
    p["group_mean"] = p.groupby(["group_id", "round"]).contribution.transform("mean")
    p["change"] = p.groupby(["group_id", "participant_id"]).contribution.shift(-1) - p.contribution
    p = p.loc[p["round"] < 10]
    reactions = []
    for below in (True, False):
        for punished in (True, False):
            rows = p.loc[(p.contribution.lt(p.group_mean) == below) & (p.received_points.gt(0) == punished)]
            reactions.append({"below_group_mean": below, "received_punishment": punished,
                              "person_transitions": len(rows), "groups": int(rows.group_id.nunique()),
                              "mean_change": float(rows.change.mean()) if len(rows) else None,
                              "mean_prior_contribution": float(rows.contribution.mean()) if len(rows) else None})
    return {"punishment_by_contribution_difference": punishment, "next_round_change": reactions,
            "reaction_note": "P阶段内1→2至9→10；无跨阶段或末轮连接。描述关联，未控制选择偏差或回归均值。"}


def primary_score(agent, human, repeats=REPEATS):
    ah, astrata = group_histograms(agent)
    hh, hstrata = group_histograms(human)
    cells = w1_cells(ah.mean(axis=0), hh.mean(axis=0))
    ab = (bootstrap_weights(astrata, repeats, SEED + 1) @ ah.reshape(len(ah), -1)).reshape(repeats, 20, 21)
    hb = (bootstrap_weights(hstrata, repeats, SEED) @ hh.reshape(len(hh), -1)).reshape(repeats, 20, 21)
    distances = w1_cells(ab, hb).mean(axis=1)
    am, hm = ah.mean(axis=0) @ np.arange(21), hh.mean(axis=0) @ np.arange(21)
    agap, hgap = am[10:].mean() - am[:10].mean(), hm[10:].mean() - hm[:10].mean()
    return {"mean_round_normalized_W1": float(cells.mean()), "ci95": interval(distances),
            "round_distances": [{"phase": p, "round": r, "distance": float(v)} for (p, r), v in zip(CELLS, cells)],
            "mean_curve_MAE_tokens": float(np.abs(am - hm).mean()),
            "phase_gap_tokens": {"agent": float(agap), "human": float(hgap), "absolute_error": float(abs(agap - hgap))},
            "agent_groups": len(ah), "human_groups": len(hh),
            "uncertainty_note": "整组独立重抽；小模拟样本区间不稳定。距离在零附近有抽样偏差，区间不能单独证明B优于C。"}


def primary_difference(first, second, human, repeats=REPEATS):
    """Difference in distances, sharing the human bootstrap across both models."""
    histograms = [group_histograms(frame) for frame in (first, second, human)]
    boot = []
    for (hist, strata), seed in zip(histograms, (SEED + 1, SEED + 2, SEED)):
        boot.append((bootstrap_weights(strata, repeats, seed) @ hist.reshape(len(hist), -1)).reshape(repeats, 20, 21))
    reference = histograms[2][0].mean(axis=0)
    distances = [float(w1_cells(histograms[i][0].mean(axis=0), reference).mean()) for i in (0, 1)]
    delta = w1_cells(boot[0], boot[2]).mean(axis=1) - w1_cells(boot[1], boot[2]).mean(axis=1)
    return {"first_distance": distances[0], "second_distance": distances[1],
            "first_minus_second": distances[0] - distances[1], "ci95": interval(delta),
            "interpretation": "负数有利于第一个模型；主比较传入B、C。共用真人重抽样，模型小组独立重抽，不按轮次计算样本量。"}


def load_agent(path):
    people, peers = [], []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        require(set(KEY + ["contribution", "received_points", "targets"]).issubset(row), "Missing JSONL fields")
        require(isinstance(row["group_id"], str) and isinstance(row["participant_id"], str), "Use string agent IDs")
        base = {k: row[k] for k in KEY + ["contribution", "received_points"]}
        people.append(dict(base, city="simulated"))
        targets = row["targets"]
        require(isinstance(targets, list), "targets must be a list")
        if row["phase"] == "N":
            require(not targets, "N has no punishment targets")
        else:
            require(len(targets) == 3, "P needs three targets")
            for target in targets:
                peers.append({k: base[k] for k in KEY + ["contribution"]} |
                             {"target_contribution": target["contribution"], "assigned_points": target["points"]})
    person, dyads = pd.DataFrame(people), pd.DataFrame(peers)
    validate_frames(person, dyads)
    return person, dyads


def draw_figure(stats):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout="constrained")
    for phase, color, label in [("N", "#64748b", "N：无惩罚（先进行）"), ("P", "#0f766e", "P：有惩罚（后进行）")]:
        rows = [r for r in stats["round_contributions"] if r["phase"] == phase]
        axes[0].plot(range(1, 11), [r["mean"] for r in rows], color=color, marker="o", label=label)
        axes[0].fill_between(range(1, 11), [r["ci95"][0] for r in rows], [r["ci95"][1] for r in rows], color=color, alpha=.13)
    axes[0].set(xlabel="阶段内轮次", ylabel="平均贡献（0–20 代币）", ylim=(0, 20), xticks=range(1, 11), title="贡献随轮次的变化")
    axes[0].legend(frameon=False, fontsize=10)
    rows = stats["behavior"]["punishment_by_contribution_difference"]
    axes[1].bar(range(5), [r["mean_points"] for r in rows], color=["#0f766e"] * 2 + ["#b45309"] * 3)
    axes[1].set(xticks=range(5), xticklabels=["−20～−11", "−10～−1", "0", "1～10", "11～20"],
                xlabel="接收者贡献 − 惩罚者贡献（代币）", ylabel="平均惩罚点数（含零惩罚）", title="惩罚指向哪些贡献者")
    for i, row in enumerate(rows):
        axes[1].text(i, row["mean_points"] + .05, f"{row['mean_points']:.2f}", ha="center", fontsize=10)
    axes[1].set_ylim(0, max(r["mean_points"] for r in rows) * 1.28)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.15)
        ax.set_axisbelow(True)
    fig.suptitle(f"真人开发集：{stats['groups']} 组 / {stats['people']} 人（Herrmann 等，2008）", fontsize=15)
    fig.supxlabel("左图阴影为整组重抽的逐点 95% 区间；N→P 差异包含阶段与经历变化。右图为描述统计。", fontsize=10)
    fig.savefig(OUT / "开发集概览.png", dpi=180)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("reference", help="Prepare the development reference.")
    for command in ("score", "compare"):
        entry = sub.add_parser(command)
        entry.add_argument("trajectory", type=Path)
        if command == "compare":
            entry.add_argument("comparator", type=Path)
        entry.add_argument("--reference", choices=["development", "evaluation"], default="development")
        entry.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    raw, digest = read_source()
    plan = fixed_plan(raw, digest)
    scoring = args.command in ("score", "compare")
    split = args.reference if scoring else "development"
    person, peers = human_frames(raw, plan, split)
    if scoring:
        agent, agent_peers = load_agent(args.trajectory)
        require(not args.output.exists(), "Output already exists; preserve prior scores")
        result = {"reference": split, "source_csv_sha256": digest,
                  "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
                  "scoring_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "trajectory_sha256": hashlib.sha256(args.trajectory.read_bytes()).hexdigest(),
                  "score": primary_score(agent, person), "agent_behavior": behavior_statistics(agent, agent_peers),
                  "human_behavior": behavior_statistics(person, peers)}
        if args.command == "compare":
            other, _ = load_agent(args.comparator)
            result["comparator_sha256"] = hashlib.sha256(args.comparator.read_bytes()).hexdigest()
            result["primary_comparison"] = primary_difference(agent, other, person)
        save_json(args.output, result)
        print(json.dumps(result.get("primary_comparison", result["score"]), ensure_ascii=False, indent=2))
    else:
        result = development_statistics(person, peers)
        result["source_csv_sha256"] = digest
        save_json(OUT / "开发集统计.json", result)
        draw_figure(result)
        print(json.dumps({"split_groups": plan["group_counts"], "development_phase_means": result["phase_means"],
                          "development_P_minus_N": result["P_minus_N"], "evaluation_behavior_exported": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
