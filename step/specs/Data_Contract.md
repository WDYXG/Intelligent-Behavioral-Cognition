# Data Contract：Evidence、路由与确定性状态投影

版本：1.1.1｜2026-09-20｜状态：当前设计权威；首个正式路由与确定性 AgentState 投影已实现。

本文件维护来源无关的数据合同。系统运行职责见 [Architecture Spec](Architecture_Spec.md)，实验对目标、时间和输入的具体选择见 [Experiment Spec](Experiment_Spec.md)。WVS 的逐字段语义以[最终来源审计](../00_persona/working/WVS7_full_audit_2026-09-14/final_audit_v0.3/final_field_status.json)和[冻结的 WVS 路由目录](../00_persona/rules/WVS_Evidence_Routing_v1.json)为准；后者全部为 proposed、未启用。历史 [Evidence Routing v1](../00_persona/rules/Evidence_Routing_v1.md)原位冻结，不再作为当前维护入口。

本次补充 P/L1–L8 的表示、P/M0/X0 初始化语义、ontology 与使用授权的区别、职业/技能证据及真实/合成人口的接口边界。完整认知机制、能力调用、GM 执行和评价职责由 Architecture Spec 维护，本文件不定义它们的具体算法。逻辑合同版本更新不修改历史 JSON Schema、Evidence、WVS 目录或选样产物，也不自动赋予投影权限。

## 1. Source Adapter 与 Evidence

Adapter 从单一来源读取原始记录，产出不可变的来源快照、标准化快照、逐项 Evidence 和处理清单；**不直接决定 AgentState**。原值、规范化值、source_dataset/version、source_record_id、source_field/locator、evidence_id、source_hash、observation_time、reference_period、ingested_at、missing_reason、原码解释、来源限制及转换规则版本必须可回溯。文本要有片段定位及抽取器版本；聚合资料不得伪装为个人观察。来源身份不跨数据集拼接；来源内部已有可靠的官方关联可保留并记录依据，其他来源的相似记录不能补全同一个真人。

Evidence 表示来源中的实际断言或观察，须区分自报、直接观察、行为记录、公开记录、确定性派生、文本抽取与总体统计，并保留适用范围、许可与禁止推断。`observed` 不保证自报内容客观为真；原始来源、标准化活动与后续解释可沿引用分开检查。新版本只能追加，不原地改变已发布证据。任何模型推断或合成结果不得伪装成真人观察。

WVS 是首个已接入来源，题号不定义核心接口。未来 CGSS、ESS、行为数据和实验数据原则上新增各自 Adapter、来源审计、Source Mapping 和必要的 ontology 条目；不复用 WVS 的编码语义，不为每个来源改一套核心 Evidence。无法无损表达的新语义须显式版本升级，迁移方式见第 6 节。

历史 v0.1 Adapter 文字曾包含 Persona 投影；当前职责拆为 Adapter 产证据与独立 Projector 产状态。旧 JSON Schema 和已发布产物原位保存，不能通过修改旧文件来假装已经升级。

## 2. 缺失、时间、来源限制

缺失是 unknown，保留来源缺失原因，不补零、均值、中立或 LLM 想象；合法的否定码、负码、“不知道/难说”类别不能仅按字面或正负判断为缺失。保留 observation_time、reference_period、ingested_at、simulation_time 与状态 valid_from/until 的区别及各自精度；未知的时间语义明确标 null/原因。top-code 保留上下界意义，不变成精确值；条件题、跨题矛盾原样保存并记录限制，不自动修复。来源缺失、未测量、语义待核、未批准输入和目标隔离是不同原因，不能全部改写成“受访者未回答”。

来源审计中的 EXCLUDED、未测量、投影 hold、编码例外和禁止推断始终优先。WVS 的 14 个 hold（Q94–Q105、Q277/Q278）、Q263 排除及 Q290 粒度限制等仍由来源审计/配置约束；不得借改写 ontology 或状态层绕过。Q286 的 2017 年参考时期、Q274=7 的下界、Q284=-5 的有效性等要在实际启用映射时继承，不在本文件重建 291 题解释表。

### 2.1 Ontology / Source Mapping

Ontology 定义来源无关的概念、类型/尺度、单位、主体、时间范围、unknown 含义和限制；个人初始属性另标其 P 内的 L1–L8 分类（完整定义见 Architecture Spec 第 3 节）。L0 的来源元数据独立于人格属性。Ontology 能描述知识、技能、关系、机制相关特征和策略倾向，但建立概念不表示已经测得值，更不构成 RouteDecision。

Source Mapping 记录来源字段或片段到概念的语义对应、条件、精度/信息损失及批准版本。相同中文名称不证明跨来源测量等价；不等价时保留限定概念或 Evidence-only。Ontology 分类、来源可解释性与实验输入授权各有职责：完整 L1–L8 是 P 的分类，不是把八层分别路由到 M/X 的规则；一个字段的具体实验用途由第 3 节决定。

职业、任职时间、资格、知识测量、技能测量和工具授权依据应能分别表示及引用证据。例如可保存有来源的历史职业，不因此创建工程技能、当前职业或资格有效期。学历和职业不能代替 L3/L4 的实际测量；授权配置也不能反写为“个人拥有该技能”的观测。个人证据向 Architecture Spec 的能力接口提供受控引用/视图，具体职业—Skill 授权规则仍待设计。

## 3. 路由与启用门槛

| route | 用途 | Agent 输入资格 |
| --- | --- | --- |
| P_INIT | 初始化 P 内 L1–L8 的个人属性，保留初始时点语义 | 需正式批准、有个人证据的映射；不要求属性现实中永久稳定 |
| M0_CANDIDATE | 初始化范围特定的长期知识、经验、关系信念、惯例或规范判断候选，`consolidated=false` | 需正式批准；不证明长期性或已经巩固 |
| X0_INIT | 初始化有明确时间范围的当前状态 | 需时间对齐和正式批准 |
| VALIDATION_TARGET | 隔离的候选真人目标 | 永不直接输入；候选不等于有效行为标签 |
| POPULATION_CALIBRATION | 独立总体/权重校准 | 不作为个体事实 |
| EVIDENCE_ONLY | 留存但不投影 | 不输入 |
| METADATA | 必需的追溯/接口元数据 | 不能当人格捷径 |
| EXCLUDED | 禁止下游使用 | 不输入、不评分 |

字段目录的 `proposed_route` 不产生授权。每个正式实验须先冻结 ExperimentRolePlan，再对具体证据产生唯一、版本化的 RouteDecision（experiment_id、evidence_id、route、reason、ontology_key、rule_id/version、来源审计引用/哈希、目标阻断原因、状态）。同一实验一条证据只有一个有效用途；跨实验改用途须新版本，不改原 Evidence。未有正式决定时默认 EVIDENCE_ONLY。

RolePlan 表达具体实验的输入、目标、时间和隔离条件，在投影前冻结并被执行，不在通用数据标准化时为每条记录重新制定。RouteDecision 还须关联 RolePlan 版本/哈希、获准的输出语义/路径、记录级时间/条件检查结果与未获准原因。同一来源字段可在不同实验获得不同用途；同一实验的同一 evidence_id 不得同时成为输入和目标，或同时以 P_INIT 与 M0_CANDIDATE 等多条有效路由投影。联合映射依赖的每条证据也都要通过门槛。

检查顺序：来源/Schema/哈希匹配 → 排除与来源限制 → 原码/缺失 → 目标闭包隔离 → 条件/时间/许可 → ontology 与映射审批 → 唯一路由。任何环节不满足应明确拒绝或保持 Evidence-only 并记原因。不能让字段建议、LLM 总结或运行程序自行提升权限。

旧 WVS 目录的 P_INIT/M0_CANDIDATE/X0_INIT 数量及 12 人对应稀疏/丰富标签保持历史含义；本次不修改历史路由。新实验用独立版本化映射明确完整 P 的初始化许可，同时对小规模决策输入另设许可视图并验证覆盖。不得把旧建议强制当新 P 的填值清单，也不得为迎合新架构撤销原来源限制。

## 4. 目标隔离与输入许可

实验先定义预测对象、信息截止点、受访者划分、输入 allowlist、目标字段/事件及其语义闭包。闭包覆盖同义文字、翻译、派生得分、组合指标、提示词示例、记忆、检索索引和可能泄漏的反馈。相关变量不自动等于泄漏；哪些可用必须按预先声明的信息条件判定，不以测试结果倒推。Y_human 仅由离线评估受控读取；校准信息须独立于测试目标或明确其信息条件。

P 的完整结构不是通往所有 Evidence 的索引。Agent 的最小输入视图只能暴露获准值/引用及必要 unknown；受限审计 provenance 与运行可见视图分开，不能借缺失模式、目标名、原始载荷或可任意解引用的 evidence_id 泄漏目标。未知位置不得携带未授权的答案。即使 RolePlan 声明没有真人目标，来源限制、时间和输入许可仍须满足。

## 5. Deterministic Projector 与状态合同

Projector 只接受已授权 RouteDecision 和版本化 mapping rule，不使用 LLM 自动总结。每条 StateValue 至少含 ontology_key、typed value、value_status（observed/derived/scenario_assumption/simulated/unknown）、evidence_refs、source field、observation/reference time、mapping_rule_id/version、time_scope、limitations；导出可沿 evidence_id 回读原记录。映射规则显式规定输入字段集合、代码/条件、拒绝分支、类型、未知语义、来源禁推断、测试例与版本。多个证据支持同一状态时去重并保留全部引用；矛盾不能被自动化解成虚构一致事实。

### 5.1 P、M0、X0 的初始化边界

| 初始化对象 | 允许承载 | 输出约束 |
| --- | --- | --- |
| P | 被实验授权使用的初始个人信息，组织在 L1–L8 | 不是把原八层拆给 M/X；只填有个人证据的值，运行期冻结 |
| M0 | 实验明确选定、范围特定的长期状态初始候选 | 自报或初始候选 `consolidated=false`，不证明长期性、规范内化或模拟中已学会 |
| X0 | 与 simulation time 对齐的初始情境/快速认知状态 | 不把历史回答冒充即时事实，不把 World truth 与个人判断混同 |

保留 P 中 L1–L8 全部逻辑容器；已定义但未观测的属性值为 null/unknown，并保留缺失状态与限制。完整结构不要求枚举未知的所有未来叶字段；未定义属性字典或机制参数须标待设计，不能为使容器“完整”自创字段值、默认学习率或策略概率。具体实验可规定最低输入要求，但不得用 LLM 补造经历、心理、技能、关系或倾向来达到要求。

M0/X0 的实验初始值不意味着把 P/L3 的知识、P/L6 的初始关系或 P/L7 的机制属性整体移出 P。同一构念可有初始基线与后续判断两种语义，但输入证据的正式 route 仍唯一。若运行初值需从获准 P 值建立引用或确定性派生视图，须由明示初始化规则声明依赖、目标语义和时间，不新增独立观察、不重复计算支持；本轮不选择任何这样的具体映射。无法区分语义时保持未启用，不复制到多个容器。

没有合法依据的 M0 项可保持 unknown；未知 M0 不默认中立。X0 中如需实验统一的公共情境，必须单独引用情景配置并标 `scenario_assumption`，不能作为真人 P 的观察；本轮未确定此类值。运行产生的 X/M 变化标为模拟状态，绝不回写来源 Evidence 或初始 P。

### 5.2 AgentState v1 与 provenance

AgentState v1 的逻辑外壳保留 schema_version、agent_id、source_identity、experiment_id、P/M0/X0、state_version、initialization_manifest，并预留 ontology、RouteDecision/RolePlan、映射版本与 P 初始版本/哈希的引用。P 含 L1–L8，运行记录中的 M/X 引用其 M0/X0 初值。value_status 不能代替来源断言类型，missing_reason/原码、来源限制、时间精度和冲突标记须直接保存或通过受控引用无损回读。确实无个人证据的 unknown 可无 evidence_refs，须注明未观测原因；不能伪造 evidence_id 填满结构。

MEntry 另含 scope、basis、consolidated、支持/反证事件、更新时间和过期策略；M0 自报候选默认未巩固，后续巩固参数未定时明确未定。Episodic Memory 引用本 Agent 的 observation_id/可知行动和交付时间，X/M 的判断可引用这些记录但不得将其直接等同为信念。具体写入流程见 Architecture Spec。

相同 Evidence、ontology/映射、RolePlan、RouteDecision 及初始化配置必须确定性复现相同状态及规范化哈希。执行时刻等运行元数据与确定性状态载荷分开；规范化序列化细节需在实现时明确版本，不将系统时钟、随机生成值或模型总结混入个人属性。此要求只保证初始化，不保证后续 LLM 输出逐字一致。

### 5.3 Population 与专业能力的扩展数据边界

真实与合成人口可共享初始化外壳和 P/L1–L8 分类，但未来 identity/provenance 必须有可区分的 human-grounded/synthetic 分支。真人保留来源内身份；合成人保留 synthetic_id、人口生成版本、分布约束、依赖假设、seed 和生成来源，不假造真实 source_record_id。合成值须有独立 synthetic 标记与生成 provenance，不能复用 `observed` 或冒充真人 Evidence；这些新增表示需版本化机器合同后才能启用。

总体校准资料保留总体、分组、时间、统计/权重与来源限制，独立于个人属性值；生成器的输出也不是总体统计直接测得的个人事实。向未来共同 Projector 接入的是有明确身份和生成依据的输入，不能把合成补值混进真人状态后保留“全部真实”的标签。首轮只使用已有真实受访者。

专业能力接口引用 ontology_key、个人证据/状态版本、适用时期和限制，以及独立的能力配置/授权依据；共享知识库、模型或 Tool 的版本属于系统配置，不填入个人已测得知识/技能。新增 Adapter、Population 或能力消费者通常不需改变 Evidence 核心含义；确有类型或必填语义缺口时按第 6 节升级，不能临时塞入旧枚举。

### 5.4 下游合同的连接点

下游运行合同包括 recipient-specific Observation、只读 DecisionContext、ActionRequest、ActionResult、StateTransition 与 RunManifest；请求和结果各自持有 request_id，可回溯 World 前后版本。RunManifest 至少固定数据/规则/角色/代码/模型/提示词/工具/场景版本与哈希、初始人口/状态引用、随机种子、温度、时间对齐、学习开关、调度/行动顺序、外部输入模式与错误策略，并关联实际 Prompt/模型响应、工具记录及完整状态/互动/世界轨迹。未知模型版本信息须如实标未知，不能仅凭 seed 声称可完整重现。接口行为由 Architecture Spec 定义；这些逻辑字段尚不是已实现的 JSON Schema。

## 6. 数据不变量与兼容性

检查至少覆盖：证据 ID 唯一、来源定位/时间可追溯或明确未知、规则 ID/版本存在、路线与状态路径一致、聚合不写个人、禁止推断继承、目标不入输入、跨源身份不合并、清单哈希吻合，以及缺失/top-code/合法特殊码/条件题/矛盾/时间/目标派生污染的正反例。新增初始化检查应覆盖 L1–L8 容器保留且不填造、职业不推出技能、M0 未巩固标记、真实/合成来源分离及派生视图不重复计证。结构校验、来源校验、投影校验和行为效度是不同层次；前者通过不推出后者。

既有 `evidence_record_v0.1.json` 的 projection 枚举不容纳全部新路由，因此 RouteDecision 是旁表；不改旧 Evidence。`persona_initial_state_v0.1.json` 实际使用 stable_attributes、slow_attributes、fast_state、long_term_normative_state；不能机械改名为 P/L1–L8 或 P/M/X，也不能沿用其中 update_policy 让当前 P 在 run 内变动。旧 source_identity 固定不跨源、旧属性要求非空 evidence_ids，均不直接支持合成人口或无证据 unknown 的新外壳；`concordia_persona_input_v0.1.json` 也不是完整 AgentState v1。机器合同见[历史 JSON Schema 目录](../00_persona/contracts/README.md)。

未来迁移须声明源/目标 Schema 与映射版本、字段级语义和时间变化、身份/unknown/provenance 的处理、兼容性测试及新输出 manifest，保留旧文件原字节。新层级路径、合成身份或新枚举用明确的新机器合同表达；旧 Evidence 能提供的信息通过受控旁表或 adapter 关联，不能强改原记录。与核心含义兼容的来源扩展可在版本化 Source Mapping/受限扩展中表达；不兼容的修改需升级核心合同并报告信息损失。

本轮经用户授权冻结 Experiment Spec 所链接的 RolePlan 与版本化映射，运行期 P/M 冻结。完整候选、获准初始化的完整 P、决策许可视图分离，不因场景最小化而人为裁减个人资料。实现使用 agent_state_v1.0.0 外壳与显式语义检查，未冒用旧 persona_initial_state_v0.1 Schema；通用外部 JSON Schema 校验器尚待实现。历史候选目录、Schema 和 12 人选择结果保持原位原字节，不自动满足新合同。
