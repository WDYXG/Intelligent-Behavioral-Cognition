# 机器合同目录

当前证据、路由、投影与 AgentState 的设计权威是 [Data Contract](../../specs/Data_Contract.md)。这里的 v0.1 JSON Schema 按原位置保留，供历史产物复现和明确版本的消费者使用；它们不等于 AgentState v1 已实现。

`source_snapshot_v0.1.json`、`evidence_record_v0.1.json`、`persona_initial_state_v0.1.json`、`processing_manifest_v0.1.json` 关联既有 WVS 处理链；`concordia_persona_input_v0.1.json`、`source_registry_v0.1.json` 及 fixture 是旧接口/来源注册材料。不要改写被历史运行清单固定的 Schema。旧 Adapter/Validation 文字合同已归档，当前规则以 Data Contract 为准。
