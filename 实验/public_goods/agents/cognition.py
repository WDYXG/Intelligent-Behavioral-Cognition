"""Judgment contract and isolated preparation of versioned cognitive state.

Structured reports are experimental representations, not validated mechanisms.
"""

from copy import deepcopy
import json

from public_goods.agents.runner import parse_json
from public_goods.environment.world import InvalidAction, require

ORIGINAL_REVISION = "tower-aligned-v1"
SCOPE_REVISION = "scope-clarified-v2"
VERIFIED_REVISION = "verified-context-v3"
STAGE_REVISION = "stage-aware-v4"
PROMPT_REVISION = "factual-history-v5"
ORIGINAL_JUDGMENT_PROMPT = (
    "你是实验中的一名参与者。先更新当前判断，暂不选择行动。"
    "仅使用 observation 中本人可见的信息、agent_state 的授权状态及 capability_view 的共同规则说明。"
    "agent_state.X 是本人先前的临时判断，可能有误，不能当作新证据；P 是冻结初始表示，M 未启用长期更新。"
    "P 中未知属性不得自行补造；你的当前自我评价不能改写 P 或冒充已内化的长期信念。"
    "先区分实验条件 phase 和当前行动 stage；只分析 capability_view.task 指定的本次任务。"
    "capability_view 的数值是已公布规则的计算，不是他人意图或推荐行动。"
    "facts 只描述可观察事实；behavior_expectation 只预测别人可能怎样行动。"
    "结合本次新增观察检查先前判断，可以保留、修订或回答未知；重复自己的旧判断不会增加证据。"
    "证据不足可以回答未知，不得把推测写成已观察事实。"
    "每项输出 assessment、evidence 和 uncertainty；assessment 是简短判断或 null，"
    "evidence 是 observation 内的 JSON Pointer 路径列表，uncertainty 为 unknown/low/medium/high。"
    "路径以 observation 对象为根，例如 /round、/rules/group_size；只能引用本人实际可见的信息，路径不重复。"
    "history 包含阶段开始等多类事件，数组索引不等于实验轮次；引用历史时从 history_references 选择完整 path，"
    "例如直接引用某条事件路径，不要自行按轮次推算数组下标。"
    "未知时 uncertainty 为 unknown，assessment 可以为 null，也可以简短解释为什么无法判断；"
    "evidence 可以为空，也可以引用导致无法判断的可见信息。"
    "其他情况下给出依据及不确定性；low/medium/high 表示不确定性从低到高，不是自信程度。"
    "other_contributions 的位置只在该轮有效，不能推断跨轮身份。"
    "只返回 judgment_schema 中声明字段的一个 JSON 对象，不添加解释或 Markdown。"
)
NORM_INSTRUCTION = (
    "norm_recognition 识别当前任务中可能相关的社会期待及其依据，区分正式规则、观察到的行为与候选规范。"
    "normative_expectation 表示你推测别人认为你应该怎样做；personal_norm 表示你自己认为怎样合适。"
    "两者与别人可能怎样做分开，平均贡献不能直接作为应该怎样做的结论。"
    "personal_norm 可以没有外部证据；不确定时仍可回答未知。不要预设某种行为必然正确。"
    "当前个人评价允许包含利益冲突与理由，但本步不选择具体行动数额；行动交由下一步决定。"
    "只分析本次 schema 中列出的判断，识别某种期待不等于必须遵从，也不证明已经形成规范。"
)
ORIGINAL_DECISION_PROMPT = (
    "你是实验中的一名参与者。根据 observation、agent_state.X 的本次判断和 capability_view 选择本次行动。"
    "实验条件 phase 与当前行动 stage 不同；只完成 capability_view.task 指定的当前任务。"
    "P/M 是只读状态，未知属性不得补造；共同规则说明不规定你的偏好或行动。"
    "judgment 是可修订的判断，不是行动指令；实验规则以 observation 为准。"
    "只返回符合 observation.action_schema 的一个 JSON 对象，不添加字段、解释或 Markdown。"
    "other_contributions 的位置只在该轮有效，不能作为跨轮的参与者身份。"
)

SCOPE_JUDGMENT_PROMPT = ORIGINAL_JUDGMENT_PROMPT + (
    "引用与使用信息是两回事：capability_view 是原规则的派生说明，引用计算或边界时，"
    "从其中 source_paths 选取实际支持结论的 observation 路径；不要引用 /capability_view 或 /agent_state。"
    "引用路径存在不等于支持结论；逐项检查判断的主体、对象、轮次与证据范围。"
    "规则若声明 same_group_each_round=true，成员固定；匿名位置重排只表示不能把位置跨轮对应到具体成员，不表示换了成员。"
    "本人观察不能自动推广到全组；没有观察到某行为与已观察到该行为为零不同。"
)
SCOPE_DECISION_PROMPT = ORIGINAL_DECISION_PROMPT + (
    "行动对象仅含 action_schema 声明的业务键；不要添加 type、json_object、action 包装或理由。"
    "不要机械沿用判断中的结论；若与可见规则、主体或对象矛盾，应依据原始观察选择合法行动。"
)

VERIFIED_JUDGMENT_PROMPT = SCOPE_JUDGMENT_PROMPT + (
    "capability_view.verified_context 是程序按本人可见记录计算的事实边界，不是模型推测。"
    "先核对其中的轮次、剩余轮数和观察范围，再填写 facts；其他判断也必须遵守这些边界。"
    "最后一个实验条件不等于该条件的最后一轮；还有后续轮次时，不得以实验马上结束为事实前提。"
    "只知道本人的收发记录时，数值为0仅适用于本人及记录所标轮次；该轮的全组结论须遵守对应派生字段。"
    "最近一轮的派生摘要不能覆盖更早记录；讨论其他轮次须查对应原始记录，不能将最近一轮未知扩展为所有历史未知。"
    "其中 null 或 unknown 表示不能确定，不表示0、没有发生或不会发生；不可写成全组均未发生。"
    "简短描述当前轮次；涉及本人历史反馈时明确标注本人和历史轮次，涉及全组时保留未知范围。"
    "引用派生事实时仍只引用其 source_paths 中支持该事实的 observation 路径，不引用派生视图本身。"
)
VERIFIED_DECISION_PROMPT = SCOPE_DECISION_PROMPT + (
    "先用 capability_view.verified_context 核对轮次和事实范围；模型判断不能改变这些可核验事实。"
    "条件结束后实验结束不代表当前轮结束后实验结束，须按剩余轮数区分；未知不等于没有发生。"
)

STAGE_JUDGMENT_PROMPT = VERIFIED_JUDGMENT_PROMPT + (
    "本步先读取 verified_context.current_stage，再检查 transition_context 标明的旧X适用范围。"
    "已公开的本轮行为属于事实，不再预测；新一轮尚未公开的行为不能用历史值填作事实。"
    "对每个判断字段单独更新：facts 已承认公开的信息，其他字段不能又称未知。"
    "保留旧判断作为过去的判断；旧事实和预测若已被新观察替代，不能因沿用旧文字而继续当作当前未知。"
    "主观评价可以保持或修订，但理由须使用当前有效事实；未知只限尚未公开或确实无法推断的内容。"
    "本步的行为预测对象以 judgment_schema 为准，不预测已经发生且可见的同一轮行为。"
    "transition_context 只是状态适用范围说明，不是外部证据；仍仅引用 observation 中的原始路径。"
)
STAGE_DECISION_PROMPT = VERIFIED_DECISION_PROMPT + (
    "当前已公开与尚未公开的信息以 verified_context.current_stage 为准；已公开值不是预测。"
    "若本次判断仍沿用过期的未知或历史数值，以当前原始观察为依据，不因旧判断丢弃已公开信息。"
)

JUDGMENT_PROMPT = STAGE_JUDGMENT_PROMPT + (
    "先核对 verified_context.payment_schedule 和 contribution_history。"
    "每轮新发的贡献代币与条件开始时的一次性收益分别记账；一次性收益不能写成每轮支付或可追加贡献的代币。"
    "概括历史时使用对应条件的全部已见反馈，不以最近一轮替代整个历史。"
    "只有该范围内每条贡献都相同，才能称全员始终贡献同一数额；少数不同值也必须保留。"
    "历史统计只覆盖已结算轮次；本轮已公开贡献单独见 current_stage。"
    "这些统计不证明社会规范，也不规定行为。引用时仍使用其中提供的 observation 原始路径。"
)
DECISION_PROMPT = STAGE_DECISION_PROMPT + (
    "核对 payment_schedule 的支付频率及 contribution_history 中的完整历史统计。"
    "旧判断或本次判断若遗漏不同的历史值，以原始记录及其派生统计为准；统计不规定行动数额。"
)


def prompt_pair(revision=PROMPT_REVISION):
    pairs = {ORIGINAL_REVISION: (ORIGINAL_JUDGMENT_PROMPT, ORIGINAL_DECISION_PROMPT),
             SCOPE_REVISION: (SCOPE_JUDGMENT_PROMPT, SCOPE_DECISION_PROMPT),
             VERIFIED_REVISION: (VERIFIED_JUDGMENT_PROMPT, VERIFIED_DECISION_PROMPT),
             STAGE_REVISION: (STAGE_JUDGMENT_PROMPT, STAGE_DECISION_PROMPT),
             PROMPT_REVISION: (JUDGMENT_PROMPT, DECISION_PROMPT)}
    require(revision in pairs, "Unknown prompt revision")
    return pairs[revision]


NORM_FIELDS = frozenset({"norm_recognition", "normative_expectation", "personal_norm", "enforcement_judgment"})
CONTRACT_VERSION = 4


def judgment_schema(phase, include_norms, stage="contribution", *, contract=CONTRACT_VERSION, revision=PROMPT_REVISION):
    require(contract in (3, CONTRACT_VERSION), "Unsupported judgment contract")
    require(revision in (ORIGINAL_REVISION, SCOPE_REVISION, VERIFIED_REVISION, STAGE_REVISION, PROMPT_REVISION), "Unknown prompt revision")
    fields = {
        "facts": "本人当前可见的事实，不加入尚未观察到的内容。",
        "behavior_expectation": "别人可能怎样行动；无依据可以未知。",
    }
    if phase == "P":
        fields["sanction_risk"] = "当前已公布规则下，受到扣减的可能性及其收益影响。"
    if include_norms:
        if contract >= 4:
            fields["norm_recognition"] = "当前任务中候选社会规则/期待及其依据；区分正式规则、行为事实与社会规范，允许未知。"
        fields.update({
            "normative_expectation": "推测别人认为我应该怎样做；与行为预测分开。",
            "personal_norm": "我自己认为怎样合适；与他人期待分开。",
        })
        if contract >= 4 and phase == "P" and stage == "punishment":
            fields["enforcement_judgment"] = "是否存在支持或反对执行某项候选规范的理由、成本与反证；本步不选择点数，不假定必须执行。"
    if contract >= 4 and revision in (SCOPE_REVISION, VERIFIED_REVISION, STAGE_REVISION, PROMPT_REVISION) and phase == "P":
        fields["sanction_risk"] = "别人作为发送者、本人作为接收者：本人可能收到扣减的风险与收益影响；不混入本人是否应向别人发送点数。"
        if "enforcement_judgment" in fields:
            fields["enforcement_judgment"] = (
                "本人作为执行者、当前显示的其他成员作为对象：分别说明本人向他人发送扣减点的支持与反对理由、成本和反证。"
                "别人可能惩罚本人的理由属于 sanction_risk，不能直接当作本人惩罚别人的理由；"
                "如风险影响本人执行决策，应说明关联并保持主体、对象清楚。允许认为没有支持理由，本步不选择点数。"
            )
    if revision in (STAGE_REVISION, PROMPT_REVISION):
        fields["facts"] = "当前条件、轮次、行动阶段及本阶段已经公开的事实；与历史轮次和未公开行为分开。"
        if stage == "contribution":
            fields["behavior_expectation"] = "预测其他成员本轮尚未公开的贡献；历史贡献只是预测依据，不是本轮已知值。"
        elif stage == "punishment":
            fields["behavior_expectation"] = (
                "只预测其他成员本轮尚未公开的扣减选择；本轮各人贡献已公开，直接作为事实，不再预测或称未知。"
            )
            if include_norms:
                fields["personal_norm"] = "依据本轮已公开贡献，评价本人现在如何处理扣减选择；可保持或修订旧评价，不选择数额。"
                if "enforcement_judgment" in fields:
                    fields["enforcement_judgment"] += "依据本轮已公开的本人及其他成员贡献分析；不得以本轮贡献不可见为前提。"
    return {
        "fields": fields,
        "each_field": {
            "assessment": "null or a nonempty string of at most 320 characters",
            "evidence": "unique JSON Pointer paths within observation; max 200 characters each; unknown may have no evidence",
            "uncertainty": ["unknown", "low", "medium", "high"],
        },
        "unknown": "assessment may be null or a short explanation; evidence may be empty or reference visible information",
    }


def judgment_transition_context(observation, previous_x, *, revision=STAGE_REVISION):
    """Describe the time scope of an unchanged old X, without judging its text."""
    current = {key: observation[key] for key in ("phase", "round", "stage")}
    previous = None if previous_x is None else {key: previous_x[key] for key in current}
    if previous is None:
        relation, instruction = "no_previous", "没有先前X，依据当前观察形成判断。"
    elif previous["phase"] != current["phase"]:
        relation, instruction = "condition_changed", "实验条件已改变；旧X属于上一条件。按当前规则和当前任务重新核对，过去经历仍保留。"
    elif previous["round"] != current["round"]:
        relation, instruction = "round_changed", "轮次已改变；旧X的事实与预测只属于旧轮次，本轮公开状态须重新读取。"
    elif previous["stage"] == "contribution" and current["stage"] == "punishment":
        relation, instruction = "contributions_revealed", (
            "同一轮已从贡献选择进入扣减选择，当前观察已公开全部贡献。"
            "旧X对本轮贡献的未知和预测已由当前值替代；每个字段都应据此重新核对理由。"
        )
    else:
        relation, instruction = "same_scope", "条件、轮次、阶段未改变；仍须核对当前观察，旧判断不是额外证据。"
    return {"previous_scope": previous, "current_scope": current, "relation": relation,
            "instruction": instruction,
            "history_policy": ("这里只说明旧判断的时间范围。可见经历仍在 observation.history；旧判断若有提供，也不作为事实证据。"
                               if revision == PROMPT_REVISION else
                               "旧X原文和可见经历保留；仅说明时间适用范围，不自动改写、认可或删除判断。")}


def history_references(observation):
    """Expose index labels for already visible events, never hidden World state."""
    return [{"path": f"/history/{index}", **{key: row[key] for key in ("type", "phase", "round") if key in row}}
            for index, row in enumerate(observation["history"])]


def evidence_exists(observation, pointer):
    """Validate references against the visible observation, never the audit log."""
    if type(pointer) is not str or not pointer.startswith("/") or len(pointer) > 200:
        return False
    node = observation
    for token in pointer[1:].split("/"):
        # Reject malformed escapes rather than accepting ambiguous references.
        i = 0
        while i < len(token):
            if token[i] == "~":
                if i + 1 >= len(token) or token[i + 1] not in "01":
                    return False
                i += 1
            i += 1
        key = token.replace("~1", "/").replace("~0", "~")
        if type(node) is dict and key in node:
            node = node[key]
        elif type(node) is list and key.isascii() and key.isdigit() and str(int(key)) == key and int(key) < len(node):
            node = node[int(key)]
        else:
            return False
    return True


def parse_judgment(text, observation, include_norms, max_bytes=8192, *, normalizations=None, contract=CONTRACT_VERSION):
    judgment = parse_json(text, max_bytes)
    fields = judgment_schema(observation["phase"], include_norms, observation["stage"], contract=contract)["fields"]
    require(type(judgment) is dict and set(judgment) == set(fields),
            "Judgment must contain exactly the declared fields")
    errors = []
    for field, belief in judgment.items():
        try:
            # Only remove one redundant wrapper when the resulting reference exists.
            # Preserve the original response separately; do not invent evidence.
            if type(belief) is dict and type(belief.get("evidence")) is list:
                for index, pointer in enumerate(belief["evidence"]):
                    if (type(pointer) is str and len(pointer) <= 200 and pointer.startswith("/observation/")
                            and not evidence_exists(observation, pointer)):
                        candidate = pointer[len("/observation"):]
                        if evidence_exists(observation, candidate):
                            belief["evidence"][index] = candidate
                            if normalizations is not None:
                                normalizations.append({"field": field, "index": index, "from": pointer, "to": candidate})
            validate_belief(field, belief, observation)
        except InvalidAction as error:
            errors.append(f"{field}: {error}")
    require(not errors, "; ".join(errors))
    return judgment


class CognitiveUpdater:
    """The only runtime writer of X; models and Decision only receive copies."""

    def __init__(self, state, include_norms, max_response_bytes=8192):
        self.state = state
        self.include_norms = include_norms
        self.max_response_bytes = max_response_bytes

    def validate(self, text, observation, *, normalizations=None):
        return parse_judgment(text, observation, self.include_norms, self.max_response_bytes,
                              normalizations=normalizations)

    def prepare(self, observation, judgment):
        # Recheck the proposed write independently of the caller's parsed object.
        checked = self.validate(json.dumps(judgment, ensure_ascii=False), observation)
        return self.state.prepare(observation, checked)

    def commit(self, transition):
        self.state.commit(transition)

    def prepare_feedback(self, observation):
        return self.state.prepare_feedback(observation)

    def discard(self):
        self.state.discard_pending()

    @staticmethod
    def readonly_view(snapshot):
        # Episodic content is supplied once, through observation.history.
        return deepcopy({key: snapshot[key] for key in ("P", "M", "X", "version")})


def validate_belief(field, belief, observation):
    require(type(belief) is dict and set(belief) == {"assessment", "evidence", "uncertainty"},
            "Each judgment field needs assessment, evidence and uncertainty only")
    assessment, evidence, uncertainty = belief["assessment"], belief["evidence"], belief["uncertainty"]
    require(type(uncertainty) is str and uncertainty in ("unknown", "low", "medium", "high"),
            "Invalid uncertainty category")
    require(type(evidence) is list, "evidence must be a list")
    errors = []
    for pointer in evidence:
        if not evidence_exists(observation, pointer):
            errors.append(f"evidence path unavailable: {str(pointer)[:200]}; reference only paths that exist in the visible observation")
    if all(isinstance(p, str) for p in evidence) and len(set(evidence)) != len(evidence):
        errors.append("Evidence paths must be unique")
    if assessment is None:
        if uncertainty != "unknown":
            errors.append("A null assessment requires unknown uncertainty")
    else:
        if not (type(assessment) is str and bool(assessment.strip()) and len(assessment) <= 320):
            errors.append("Assessment must be concise text or null")
        if not evidence and field != "personal_norm" and uncertainty != "unknown":
            errors.append("A stated assessment needs visible evidence")
    require(not errors, "; ".join(errors))

