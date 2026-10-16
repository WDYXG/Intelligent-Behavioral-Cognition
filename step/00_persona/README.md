# Persona 数据阶段：导航

当前证据、路由、投影规则见 [Data Contract](../specs/Data_Contract.md)，系统和实验边界分别见 [Architecture Spec](../specs/Architecture_Spec.md)、[Experiment Spec](../specs/Experiment_Spec.md)。本目录的[研究日志](PERSONA研究日志.md)是唯一决定沿革记录，不作为当前设计规则的替代入口。

`raw/` 保存原始资料、来源和许可；`working/` 保存来源审计；`rules/` 保存版本化的 WVS 来源解释/未启用路由目录；`contracts/` 保存历史机器合同；`pipeline/` 是来源处理实现；`derived/` 保存按运行固定的结果；`presentation/` 保存审阅展示工具。已有 raw、derived 与 WVS 历史校验链原位保留。

[WVS 正式全量运行清单](derived/WVS7_CHN_2018/WVS_full_2026-09-17T09-33-15-228Z/manifest.json)记录 3,036 人及 880,440 条 Evidence。它不是 3,036 个 AgentState，也不是行为验证。目前没有正式 RouteDecision 或 AgentState v1；后续仅在实验角色与少量映射单独确认后才能投影。
