"""Shared task knowledge derived only from an agent's delivered observation.

This is a rule-interpretation capability, not measured individual knowledge or
skill. It supplies no preferred action, inferred norm, or unseen world state.
"""

from copy import deepcopy
from collections import Counter
from decimal import Decimal


ORIGINAL_CAPABILITY_VERSION = "public-goods-visible-rules-v1"
SCOPE_CAPABILITY_VERSION = "public-goods-visible-rules-v2"
VERIFIED_CAPABILITY_VERSION = "public-goods-visible-rules-v3"
STAGE_CAPABILITY_VERSION = "public-goods-visible-rules-v4"
CAPABILITY_VERSION = "public-goods-visible-rules-v5"


def _number(value, name):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite nonnegative number")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return result


def _text(value):
    return format(value.normalize(), "f")


def _contribution_knowledge(rules):
    keys = ("group_size", "retained_token_income", "return_per_group_token")
    if not all(key in rules for key in keys):
        return None
    size = rules["group_size"]
    if type(size) is not int or size <= 0:
        raise ValueError("group_size must be a positive integer")
    retained = _number(rules["retained_token_income"], "retained_token_income")
    returned = _number(rules["return_per_group_token"], "return_per_group_token")
    total = size * returned
    return {
        "scope": "保持其他成员贡献不变，每将1个代币从保留改为投入公共池。",
        "per_added_contribution_token": {
            "own_return": float(returned),
            "own_net_change": float(returned - retained),
            "group_total_return": float(total),
            "group_net_change": float(total - retained),
        },
        "explanation": (
            "个人返还等于每个公共池代币的个人返还率；个人净变化还要扣除放弃的保留收益。"
            "全组总返还等于人数乘以个人返还率；全组净变化再扣除该投入者放弃的保留收益。"
            "这些是收益关系，不是行动建议。"
        ),
        "source_paths": [f"/rules/{key}" for key in keys],
    }


def _deduction_knowledge(rules):
    keys = ("income_deduction_per_point_received", "cost_per_point_sent", "period_income_formula")
    if not all(key in rules for key in keys):
        return None
    loss = _number(rules[keys[0]], keys[0])
    cost = _number(rules[keys[1]], keys[1])
    formula = rules["period_income_formula"]
    if not isinstance(formula, str):
        raise ValueError("period_income_formula must be text")
    normalized = "".join(formula.split())
    base = f"max(0,first_stage_income-{_text(loss)}*total_received_points)"
    recognized = normalized == base + f"-{_text(cost)}*total_sent_points"
    recognized = recognized or (cost == 1 and normalized == base + "-total_sent_points")
    result = {
        "income_deduction_per_point_received": float(loss),
        "cost_per_point_sent": float(cost),
        "period_income_formula": formula,
        "floor_formula_recognized": recognized,
        "source_paths": [f"/rules/{key}" for key in keys],
    }
    if recognized:
        cost_effect = (
            "本人发送成本仍可能让最终本轮收益为负。" if cost > 0
            else "当前发送成本为0，因此该公式的最终本轮收益不会低于0。"
        )
        result.update({
            "floor_before_own_sending_cost": 0,
            "explanation": (
                "先从第一阶段收益减去收到点数造成的扣减，并将这一结果限制为不低于0；"
                "再减去本人发送点数的成本。因此，收到的点数越多也不会让前一步低于0，"
            ) + cost_effect,
        })
    else:
        result["explanation"] = "当前公式不属于本视图可解释的已知形式；以原始规则公式为准，不推断收益下限。"
    return result


def _temporal_context(observation):
    """Distinguish the last condition from the last round using visible rules."""
    rules, current = observation["rules"], observation["round"]
    total = rules.get("rounds")
    if total is not None and (type(total) is not int or total < current):
        raise ValueError("Observed condition rounds must be an integer at least the current round")
    remaining = None if total is None else total - current
    ends = rules.get("experiment_ends_after_this_phase")
    if ends is not None and type(ends) is not bool:
        raise ValueError("Observed condition-end rule must be boolean")
    # When further rounds are announced, the experiment cannot end this round,
    # even if the status of the condition itself is not announced (as in N).
    ends_this_round = False if remaining is not None and remaining > 0 else (
        ends if remaining == 0 else None
    )
    sources = ["/phase", "/round"]
    if "rounds" in rules:
        sources.append("/rules/rounds")
    if "experiment_ends_after_this_phase" in rules:
        sources.append("/rules/experiment_ends_after_this_phase")
    statement = (f"当前为本条件第{current}/{total}轮，本轮之后本条件还剩{remaining}轮。"
                 if total is not None else "未公布本条件总轮数，不能计算剩余轮数。")
    if ends is True:
        if remaining == 0:
            statement += "本轮是本条件最后一轮，完成后实验结束。"
        elif remaining is not None:
            statement += "本条件完成后实验结束，但当前不是最后一轮。"
        else:
            statement += "本条件完成后实验结束，无法确定是否为当前这一轮。"
    elif ends is None:
        statement += "当前信息未说明本条件结束后实验是否结束。"
    return {
        "condition": observation["phase"],
        "current_round": current,
        "condition_rounds": total,
        "rounds_remaining_after_current": remaining,
        "current_is_final_round_of_condition": None if total is None else current == total,
        "experiment_ends_after_this_condition": ends,
        "experiment_ends_after_current_round": ends_this_round,
        "statement": statement,
        "source_paths": sources,
    }


def _latest_scoped_feedback(observation):
    """Summarize one visible feedback record, never complete its hidden edges."""
    latest = None
    for index, row in enumerate(observation.get("history", [])):
        if isinstance(row, dict) and row.get("type") == "round_feedback" and row.get("phase") == "P":
            latest = (row, f"/history/{index}")
    if latest is None:
        return None
    row, path = latest
    received = row.get("received_points")
    assigned = row.get("assigned_points_by_position")
    if received is not None and (type(received) is not int or received < 0):
        raise ValueError("Observed received points must be a nonnegative integer")
    if assigned is not None and (type(assigned) is not list
                                or not assigned
                                or any(type(value) is not int or value < 0 for value in assigned)):
        raise ValueError("Observed assigned points must be a nonempty list of nonnegative integers")
    sent = None if assigned is None else sum(assigned)
    any_visible = (received is not None and received > 0) or (sent is not None and sent > 0)
    sources = [path]
    # Null means unknown, never zero. A positive visible edge proves existence
    # of some group sending; zero on self-related edges proves no such absence.
    statement = "本记录只覆盖该轮本人收到的点数及本人发送的点数，其他成员之间的发送不可见。"
    if any_visible:
        statement += "已见正点数，因此该轮组内至少发生过一次发送；不能据此确定全组总量或其他发送关系。"
    else:
        statement += "即使本人收到和发送均为0，也不能推出该轮全组均未发送；全组是否发生过发送仍未知。"
    return {
        "condition": "P", "round": row.get("round"),
        "own_received_points": received,
        "own_assigned_points_by_position": deepcopy(assigned),
        "own_sent_points_total": sent,
        "group_any_punishment": True if any_visible else None,
        "other_to_other_points": None,
        "statement": statement,
        "source_paths": sources,
    }


def _current_stage_facts(observation):
    """Use only this stage's delivered values, never substitute historical values."""
    stage = observation["stage"]
    result = {
        "condition": observation["phase"], "round": observation["round"], "stage": stage,
        "contributions": {"status": "not_yet_revealed", "own": None, "others_by_position": None,
                          "source_paths": ["/phase", "/round", "/stage"]},
    }
    if stage == "punishment":
        # World has resolved the simultaneous contribution batch before this view.
        # Missing delivered data is an invalid input, not a license to use old X.
        own, others = observation.get("own_contribution"), observation.get("other_contributions")
        if (type(own) is not int or own < 0 or type(others) is not list or not others
                or any(type(row) is not dict or type(row.get("position")) is not int
                       or row["position"] != index + 1 or type(row.get("contribution")) is not int
                       or row["contribution"] < 0 for index, row in enumerate(others))):
            raise ValueError("Current revealed contribution values are required at the sending stage")
        result["contributions"] = {
            "status": "revealed", "own": own, "others_by_position": deepcopy(others),
            "source_paths": ["/phase", "/round", "/stage", "/own_contribution", "/other_contributions"],
        }
        result["statement"] = (
            f"本轮贡献已完成并公开：本人{own}，其他成员按本轮位置为{[row['contribution'] for row in others]}。"
            "这些是当前已知事实；旧贡献阶段的未知或预测已经被这些观察替代。"
            "本轮其他成员发送的扣减点尚未公开，只有对此仍需预测。"
        )
    elif stage == "contribution":
        result["statement"] = (
            "本轮贡献正在同时选择，尚未公开；历史中的贡献值只属于历史轮次。"
            "即使上一轮已经公开，也不能填作本轮已知贡献。"
        )
    else:
        # These views are used in audits as well as decision stages. Preserve the
        # delivered feedback scope instead of fabricating fresh decision facts.
        result["contributions"]["status"] = "see_delivered_history"
        result["statement"] = "当前没有新的决策任务；已结算的贡献与结果按反馈中标明的条件和轮次读取。"
    return result


def _payment_schedule(observation):
    rules = observation["rules"]
    result = {}
    if "fresh_tokens_each_round" in rules:
        result["round_endowment"] = {
            "amount": float(_number(rules["fresh_tokens_each_round"], "fresh_tokens_each_round")),
            "frequency": "each_round", "unit": "contribution_tokens",
            "source_paths": ["/rules/fresh_tokens_each_round"],
        }
    # Do not announce a later condition/payment when it is not visible yet.
    if observation["phase"] == "P" and "phase_lump_sum" in rules:
        amount = _number(rules["phase_lump_sum"], "phase_lump_sum")
        paths = ["/rules/phase_lump_sum"]
        for index, row in enumerate(observation.get("history", [])):
            if row.get("type") == "new_experiment_announced" and row.get("phase") == observation["phase"] and "lump_sum" in row:
                if _number(row["lump_sum"], "lump_sum") != amount:
                    raise ValueError("Visible lump-sum announcement conflicts with the current rule")
                paths.append(f"/history/{index}/lump_sum")
        result["phase_payment"] = {
            "condition": observation["phase"], "amount": float(amount),
            "frequency": "once_at_condition_start", "unit": "income",
            "statement": "条件开始时计入收益一次。不是每轮支付，也不增加每轮可贡献的代币。",
            "source_paths": paths,
        }
    return result


def _contribution_history(observation):
    """Count every delivered feedback value once; do not invent missing rounds."""
    history = observation.get("history", [])
    if type(history) is not list:
        raise ValueError("Visible history must be a list")
    size = observation["rules"].get("group_size")
    if type(size) is not int or size < 2:
        raise ValueError("Historical contribution counts require a valid group size")
    phases, seen = {}, set()
    for index, row in enumerate(history):
        if type(row) is not dict:
            raise ValueError("Visible history events must be objects")
        if row.get("type") != "round_feedback":
            continue
        phase, round_ = row.get("phase"), row.get("round")
        if phase not in ("N", "P") or type(round_) is not int or round_ < 1:
            raise ValueError("Invalid historical contribution scope")
        if phase == "P" and observation["phase"] == "N":
            raise ValueError("Later condition feedback cannot be visible in N")
        if phase == observation["phase"] and (round_ > observation["round"] or
                (round_ == observation["round"] and observation["stage"] in ("contribution", "punishment"))):
            raise ValueError("Unsettled round cannot appear in historical feedback")
        if (phase, round_) in seen:
            raise ValueError("Duplicate historical contribution round")
        seen.add((phase, round_))
        own, others = row.get("own_contribution"), row.get("other_contributions")
        if (type(own) is not int or own < 0 or type(others) is not list or len(others) != size - 1
                or any(type(item) is not dict or type(item.get("position")) is not int
                       or item["position"] != i + 1 or type(item.get("contribution")) is not int
                       or item["contribution"] < 0 for i, item in enumerate(others))):
            raise ValueError("Incomplete or invalid historical contribution values")
        peer_values = sorted(item["contribution"] for item in others)
        values = [own, *peer_values]
        if "group_total_contribution" in row and row["group_total_contribution"] != sum(values):
            raise ValueError("Historical group total conflicts with visible contribution values")
        entry = phases.setdefault(phase, {"rounds": [], "group_value_counts": Counter()})
        entry["rounds"].append({"round": round_, "own": own, "others_sorted": peer_values,
                                "source_path": f"/history/{index}"})
        entry["group_value_counts"].update(values)
    for entry in phases.values():
        counts = entry["group_value_counts"]
        entry["group_value_counts"] = [{"value": value, "count": counts[value]} for value in sorted(counts)]
        entry["observed_rounds"] = len(entry["rounds"])
        entry["observed_person_rounds"] = sum(counts.values())
        entry["all_observed_values_equal"] = len(counts) == 1
    return {
        "scope": "全部已送达的已结算轮次反馈。按条件分别统计，未覆盖的轮次不推断。",
        "conditions": phases,
        "statement": "每条反馈只计一次。others_sorted 是该轮其他成员数值的排序，不是固定身份。当前尚未结算轮次另见 current_stage。",
    }


def build_capability_view(observation, *, revision=CAPABILITY_VERSION):
    """Return a small detached view; never read a World object or human target."""
    if revision not in (ORIGINAL_CAPABILITY_VERSION, SCOPE_CAPABILITY_VERSION,
                        VERIFIED_CAPABILITY_VERSION, STAGE_CAPABILITY_VERSION, CAPABILITY_VERSION):
        raise ValueError("Unknown capability revision")
    if not isinstance(observation, dict) or not isinstance(observation.get("rules"), dict):
        raise ValueError("A delivered observation with rules is required")
    phase, stage, round_ = (observation.get(key) for key in ("phase", "stage", "round"))
    if phase not in ("N", "P") or stage not in ("contribution", "punishment", "feedback", "finished"):
        raise ValueError("Unknown observed condition or stage")
    if phase == "N" and stage == "punishment":
        raise ValueError("Inconsistent observed condition and stage")
    if type(round_) is not int or round_ <= 0:
        raise ValueError("Observed round must be a positive integer")
    if stage in ("contribution", "punishment") and not isinstance(observation.get("action_schema"), dict):
        raise ValueError("An action stage requires the observed action schema")

    instructions = {
        "contribution": "本次只选择公共池贡献数额；合法输出以 action_schema 为准。condition 是实验条件，stage 才是当前任务。",
        "punishment": "本次选择按本轮显示位置发送的扣减点数；合法输出以 action_schema 为准。condition 是实验条件，stage 才是当前任务。",
        "feedback": "本次是反馈读取，没有新的行动提交任务。",
        "finished": "本次实验已结束，没有新的行动提交任务。",
    }
    rules = observation["rules"]
    result = {
        "version": revision,
        "task": {
            "condition": phase, "stage": stage, "round": round_,
            "action_schema": deepcopy(observation.get("action_schema", {})),
            "instruction": instructions[stage],
        },
        "shared_rule_knowledge": {},
        "information_limits": [],
    }
    contribution = _contribution_knowledge(rules)
    if contribution is not None:
        result["shared_rule_knowledge"]["contribution_payoff"] = contribution
    limits = result["information_limits"]
    if revision != ORIGINAL_CAPABILITY_VERSION and rules.get("same_group_each_round") is True:
        limits.append({
            "statement": "每轮参与的是同一个固定小组；显示位置不能跨轮对应具体成员，但这不表示成员更换。",
            "source_paths": ["/rules/same_group_each_round"],
        })
    if stage == "contribution" and rules.get("contributions_are_simultaneous") is True:
        limits.append({
            "statement": "本轮其他成员的同时贡献尚不可见；行为预期不能当作已经观察到的贡献。",
            "source_paths": ["/stage", "/rules/contributions_are_simultaneous"],
        })
    if rules.get("anonymous_positions"):
        limits.append({
            "statement": "其他成员的显示位置每轮重排，位置不是持久身份，不能据此跨轮认人。",
            "source_paths": ["/rules/anonymous_positions"],
        })
    if phase == "P":
        deduction = _deduction_knowledge(rules)
        if deduction is not None:
            result["shared_rule_knowledge"]["deduction_payoff"] = deduction
        if stage == "punishment" and rules.get("deductions_are_simultaneous") is True:
            limits.append({
                "statement": "其他成员本轮同时发送的扣减点尚不可见，不能将预计的扣减写成已发生。",
                "source_paths": ["/stage", "/rules/deductions_are_simultaneous"],
            })
        if rules.get("punishers_are_anonymous") is True:
            limits.append({
                "statement": (
                    "本人收到的扣减不标明发送者身份。仅凭本人发送及收到的点数，"
                    "不能推出其他人之间的扣减，也不能认定全组无人发送或收到点数。"
                ),
                "source_paths": ["/rules/punishers_are_anonymous"],
            })
        if revision != ORIGINAL_CAPABILITY_VERSION:
            result["task"]["direction"] = {
                "incoming": "别人发送→本人收到：判断本人所受扣减风险。",
                "outgoing": "本人发送→当前显示的其他成员收到：判断本人是否向他人发送及其理由。",
                "history_fields": "received_points 是本人收到；assigned_points_by_position 是本人按当轮位置发送；均不是全组记录。",
            }
    if revision in (VERIFIED_CAPABILITY_VERSION, STAGE_CAPABILITY_VERSION, CAPABILITY_VERSION):
        result["verified_context"] = {
            "temporal": _temporal_context(observation),
            "interpretation": "由当前可见记录计算；null 表示未知。只核对事实边界，不规定偏好或行动。",
        }
        if phase == "P":
            result["verified_context"]["latest_settled_round"] = _latest_scoped_feedback(observation)
            result["verified_context"]["feedback_scope"] = (
                "latest_settled_round 仅指最近一条已完成轮次反馈；不是本轮尚未结算的结果，也不是全组发送记录。"
                "若为 null，表示尚无已公布的该条件轮次反馈。其他历史仍以 observation.history 为准。"
            )
        if revision in (STAGE_CAPABILITY_VERSION, CAPABILITY_VERSION):
            result["verified_context"]["current_stage"] = _current_stage_facts(observation)
        if revision == CAPABILITY_VERSION:
            result["verified_context"]["contribution_history"] = _contribution_history(observation)
            result["verified_context"]["payment_schedule"] = _payment_schedule(observation)
    return result
