# Architecture Spec v1：真实数据驱动自适应社会 Agent

版本：1.0.0-draft｜日期：2026-09-18｜状态：可审阅规范，尚未冻结、尚未实现运行时。

本规范落实用户新项目定义，不把架构设计写成已有实现，也不代替毕业设计的研究验证。

## 1. 研究目标与证据等级

项目：Data-Grounded Adaptive Social Agent Framework。

毕业设计研究问题仍是“大语言模型智能体的社会规范行为模拟与拟人性验证”。数据与状态架构是支持这一问题的基础设施，不是独立的拟人性证据。

| 类型 | 当前内容 | 不可外推的结论 |
| --- | --- | --- |
| 用户已明确的方向 | 数据源无关、证据驱动；Evidence/P/M/X/World/Y/Decision/Evaluation；先小闭环后扩规模 | 这些模块已经实现或优于其他架构 |
| 既有研究方向 | 常识、专业、个性知识分层；四组消融；真人留出行为验证 | 加一层必然提高拟人性 |
| 论文概念的项目解释 | 模型塔的个性知识通过 P/M/X 进入决策，专业知识通过受控模块进入决策 | 论文直接提出了本文件的八部分接口或已验证本项目 |
| 本文件设计选择 | 单写者、版本化快照、路由旁表、目标隔离、按阶段启用学习 | 唯一正确的人类认知结构 |
| 待验证假设 | 个体状态改善行为一致性；动态学习改善跨时间适应 | 真实认知方程、稳定因果规律、现实政策预测能力 |

**范围提醒：** 新框架比原先 RQ1–RQ4 更广。异构数据、大规模模拟、规范涌现、合成人口和政策实验均为后续方向，不应成为完成本科核心验证的前置条件。本规范不新增毕业设计题目或替用户决定扩大研究范围。

## 2. 当前基线和本轮边界

当前正式证据产物为 [WVS 全量运行清单](../00_persona/derived/WVS7_CHN_2018/WVS_full_2026-09-17T09-33-15-228Z/manifest.json)。其[既有回读报告](../00_persona/derived/WVS7_CHN_2018/WVS_full_2026-09-17T09-33-15-228Z/readback_verification.json)记录：3,036 人、880,440 条证据、每人 290 个字段、无 Persona 投影。这里引用历史报告，不声称本轮重跑全量转换。

本轮仅形成架构与路由规范、逐字段路由目录和目录入口；不改原始数据、既有 Evidence、历史审计和 Concordia 代码，不调用 LLM，不生成 3,036 个 Agent。

配套阅读：[Evidence Routing v1](../00_persona/rules/Evidence_Routing_v1.md)。新规范不能撤销来源审计中的具体限制。

## 3. 最小结构与生命周期

```text
Source Adapter → immutable Evidence → Routing + deterministic Projection
                                          ↓
                                     P / M0 / X0
                                          ↓
World → Observation → X update → Decision(P,M,X,Observation,Domain)
  ↑                                  ↓ ActionRequest
  └── world transition ← GM validation + handler → Y_sim
                              ↓ visible feedback
                         X update → eligible consolidation → M

sealed Y_human + read-only logs → Evaluation → reports (no runtime writeback)
```

Y_human 是隔离保存的真人目标；Y_sim 是本次模拟的行动/结果。二者不共享供 Agent 检索的存储。反馈必须来自世界事件，不能来自“与真人答案是否一致”的评分。

| 部分 | 保存什么 / 输入 | 唯一合法写入者 | 更新频率与生命周期 | 对外接口 |
| --- | --- | --- | --- | --- |
| Evidence | 来源事实、原值/标准值、缺失、时间、定位、限制 | Source Adapter 只追加新版本，不原地改旧证据 | 采集/标准化时；跨实验保留 | 按 ID 取证；运行 Agent 无任意扫描权 |
| P | 实验内冻结的背景、基线资源/知识、关系；有依据才纳入稳定倾向 | 初始化 Projector；运行期无人可写 | 一次实验冻结；修订须新版本/新 run | 最小只读 PersonaView |
| M | 特定长期信念、经验与规范判断；初始候选及巩固来源 | 初始化 Projector；运行期 Consolidator | 跨多步更新；衰减/修正/遗忘必须留日志 | 带版本的 LearnedStateView |
| X | 当前观察、短期判断、情绪、感知规范、当前资源 | 初始化 Projector；运行期 StateUpdater | 每步/事件更新；可过期 | ContextStateView |
| World | 客观状态、制度、权限、资源账本、事件和后果 | WorldEngine / GM 经 handler 验证后提交 | 每次已接受行动/外部事件；run 内共享 | 受限 Observation；GM 私有完整视图 |
| Y | 行动请求、接受/拒绝、实际执行、后果、时间 | 运行事件记录器；真人目标由独立目标加载器导入 | 追加，不重写历史行动 | 隔离的行动日志与评估输入 |
| Decision | 决策编排、模型/工具配置；不是额外持久状态库 | 配置管理器在 run 前冻结 | 每次行动生成请求，不直接修改状态 | DecisionContext → ActionRequest |
| Evaluation | 指标、诊断、置信区间和报告 | 离线 Evaluator | 只读评估；调参仅进入新的开发 run | EvaluationReport |

“唯一写入者”是服务职责，不是强制八个进程。最小实现可在同一程序内，以类型边界和授权检查实现隔离。

### 3.1 状态归属的判定

先判断“描述的是什么、在哪个时间尺度、谁能观察”，再选择层级。不能只按字段名归类。

- P 中的收入档位是基线自报，不等于 World 中可支出的精确余额。若场景需要余额，须另外声明场景参数，不能把档位变成精确资产。
- P 中的既往职业经历可冻结；X 保存当前就业状态。失业事件更新 X，不抹掉 P 中的工作经历。
- World 中的处罚概率是真实机制；X 中的感知处罚风险是信念，允许不一致。
- P 可保存一般学习倾向，但 WVS 没有直接测量时保持缺失，不从年龄、学历或单道态度题推算学习率。
- M 中的“对某机构的信任”不应同时复制成 P 中不可更新的同一事实。不同层引用同一证据时须有不同语义，不能制造双重事实源。
- 事实归属与快慢是设计选择。运行中确需改变 P 时，停止当前 run 或进入预先定义的新阶段并生成新版本；不临时把 P 改成可写。

### 3.2 时间语义

至少区分 observation_time（记录时间）、reference_period（回答针对的时期）、ingested_at（入库时间）、simulation_time（模拟时钟）、valid_from/until（状态适用区间）。未知保持 null，并附原因。

WVS 2018 快照不能不加说明地当成 2026 年现状。首个小闭环可选择“与调查时期对齐的情景”或“明确标为反事实初始化的情景”，但必须在 run 配置中选定。Q286 针对 2017 年家庭收支，不是 2018 年即时现金状态。缺失 reference_period 不能自动解释为“现在”。

## 4. 数据与运行接口（逻辑合同）

以下为待实现接口要求，不是已经生效的 JSON Schema。冻结之后再实现 Schema、Projector 和 Concordia 适配器；禁止用下列表格声称运行时校验已完成。

| 合同 | 必需字段 / 约束 |
| --- | --- |
| EvidenceRef | evidence_id、source_dataset/version、source_record_id、source_locator、observation_time、reference_period、source_hash；允许通过证据 ID 解析，但最终导出必须可追溯 |
| RouteDecision | experiment_id、evidence_id、route、reason、ontology_key、rule_id/version、source_audit_ref/hash、target_block_reason、decision_status；用途每个实验单独冻结 |
| StateValue | ontology_key、value、value_status、evidence_refs、mapping_rule_id/version、time_scope、limitations；value_status 区分 observed/derived/scenario_assumption/simulated/unknown |
| AgentState | schema_version、agent_id、source_identity、experiment_id、P、M、X、state_version、initialization_manifest；source_identity 不跨来源合并 |
| MEntry | StateValue 加 scope、basis、consolidated、support_event_ids、counterevidence_ids、last_updated、expiry_policy；M0 自报初始化默认 consolidated=false |
| Observation | observation_id、recipient、event_ref、visibility、content、observed_at、channel；只暴露该接收者允许看见的信息 |
| DecisionContext | P/M/X 的固定版本、当前 Observation、允许工具和行动空间、知识库版本；不含 Y_human、评估分数或 GM 私有状态 |
| ActionRequest | request_id、actor_id、time、action_type、arguments、basis_state_version；理由可记录但不是行为机制的独立证据 |
| ActionResult | request_id、accepted、rejection_reason、executed_action、outcome_event_ids、world_version_before/after；提出不等于执行 |
| StateTransition | event_id、writer、before/after_version、allowed_paths、cause_refs、rule_version、timestamp；所有改动可审计 |
| RunManifest | 数据/规则/角色划分/代码/模型/提示词/工具/场景版本及哈希，随机种子、温度、时间对齐策略、学习开关、错误策略 |

缺失值不默认补均值、零、普通人或“中立”。决策视图明确标注 unknown；LLM 若据常识猜测，必须标为推测，不能回写为 observed。观测来源和机制内部推断分开记录。

### 4.1 决策到世界的提交规则

1. StateUpdater 只读取该 Agent 的合法 Observation，形成 X 的新版本。
2. 构建只读 DecisionContext；模型与工具只能返回请求或提议。
3. GM/handler 检查 actor、权限、参数、资源和状态版本；无效请求记录拒绝，不伪造行动成功。
4. 将 World 变化与对应 ActionResult/Y_sim 作为同一事务提交，或使用可恢复事件提交机制；request_id 保证重试幂等。
5. ObservationBuilder 按可见性生成反馈，再更新 X。发生错误时保留事件与失败原因，不能悄悄跳过。
6. 学习开关开启时，Consolidator 在预设时点检查候选事件；它只能写 M，不能改 P、World 或历史 Y。

Concordia 是承载这一循环的运行框架，不意味着现有原型已满足这些权限或事务要求；后续接入需逐项核对。

### 4.2 专业层与世界规则

专业层提供规范知识、领域推理、检索和工具；World 决定哪些行动在物理/系统上可执行及其后果。正式规则可以存在于 World，但 Agent 只通过发布、观察或学习获知。

“违反社会规范”不自动等于“系统拒绝执行”。例如允许 Agent 插队再触发制裁，才能研究是否遵守规范；如果 handler 直接禁止所有违规，测到的是权限约束，不是规范行为。

专业知识必须有来源、适用群体、时期、作用域和版本。冲突知识并存时显式展示冲突；不能让规范知识库检索到当前受试者的留出答案。

## 5. N1–N6 是跨层机制，不是新状态层

| 机制 | 输入与位置 | 可产生的结果 | 不应作的推断 |
| --- | --- | --- | --- |
| N1 规范识别 | Observation、Domain、P/M → X | 某情境可能涉及的规范及不确定性 | 识别到规范就会遵守 |
| N2 描述性规范 | 可见的他人行为 → X；多次证据可进入 M | “别人通常怎样做”的主观估计 | 直接读取全体 Agent 的真实统计 |
| N3 命令性规范 | 可见评价/奖惩/表达 → X/M | “别人赞成怎样做”的判断 | 用多数行为等同于道德赞成 |
| N4 规范内化 | 多次支持/反证 + 巩固规则 → M | 情境特定规范判断的稳定化 | 单道赞成题证明心理内化 |
| N5 社会学习 | 可见行为和反馈、P 中有依据的倾向 → X/M | 信念修订、遗忘或保留 | 模型理由就是实际学习路径 |
| N6 规范执行 | Decision → Y；World 裁定后果 | 遵守、抵抗、规避、劝阻、惩罚等行动 | 处罚结果直接证明动机 |

## 6. X → M 的最小可审计设计

此节是后续机制接口，不在本轮实现。首个 vertical slice 建议关闭 M 更新，先验证读写边界。

开启学习前必须冻结：候选事件类型、去重键、证据可靠性规则、正反证据处理、最低观察次数/时间跨度、更新幅度上限、衰减/过期策略和冲突处理。阈值是实验参数，不从人口属性臆造，也不宣称心理定律。

一次观察先进入 X。只有达到已声明条件才创建或修订 MEntry；重复检索同一事件不增加支持次数。反证既可降低置信也可阻止巩固，具体规则版本化。遗忘是失活/过期标记，不删除审计轨迹。

初始化的 M0 与运行中巩固的 M 必须可区分：前者是有来源但长期性未证实的状态假设，后者是模拟机制的产物；两者都不自动等于真人认知。

## 7. 防泄漏与权限边界

- 先定义实验预测对象和信息截止时间，再投影输入。未冻结 ExperimentRolePlan 时，候选路由不得进入正式 AgentState。
- 留出隔离覆盖原始题、同义改写、派生得分、组合指标、提示词示例、记忆、检索索引和调参反馈。不只是删除一个字段名。
- Y_human 只能由 Evaluation 的受控加载器读取。GM、Decision、ObservationBuilder、Projector 无权访问目标载荷。
- 人口权重/总体标签分布若用于校准，须来自独立开发/校准数据，或明确声明其信息条件；不得用测试目标分布调参后再当独立验证。
- 同一个受访者的可用输入和目标可以用于个体预测，但必须按任务/时间隔离，输入不能直接包含目标答案。估计泛化时另外处理受访者级训练/测试划分。
- 一个实验的目标在另一实验中可成为输入，但必须新建角色划分和 run；不能运行中切换用途。
- 私密/敏感证据按最小必要原则暴露，外部模型访问需符合来源许可和研究授权；日志/网页回放不默认公开真人标识或完整原始回答。

## 8. 与现有 v0.1 合同兼容

| 既有产物 | v1 处理 |
| --- | --- |
| EvidenceRecord v0.1 的 projection 枚举 | 保持原始证据不变；新路由保存在旁表，以 evidence_id 关联，禁止直接塞入不兼容枚举 |
| PersonaInitialState v0.1 stable/slow/fast/normative | 按字段语义显式迁移；不能把 slow_attributes 整体改名 M，因为其中含家庭/教育等背景 |
| Source Adapter v0.1 同时包含 Persona 投影 | 历史行为保留；新流程拆分 Source Adapter（证据）与 Projector（状态），不默默更改旧合同含义 |
| ConcordiaPersonaInput v0.1 prompt_profile/memory_seeds | 不直接用它承载完整 P/M/X；新增版本化 AgentState adapter，并保留旧实验回放能力 |
| 旧规范模型/Norm Model | 迁移为 N1–N6 的跨层职责，不新建第九个顶层状态 |

新数据源原则上只新增 Adapter、来源映射及必要的 ontology 条目。缺乏可映射语义时保留 Evidence-only，不为“统一”强行压成 WVS 编码。若确需新顶层结构，先提出无法被现有层表示的反例，再讨论版本升级。

## 9. 实验设计与完成门槛

| 阶段 | 必需验收 | 能声称什么 |
| --- | --- | --- |
| 当前规范 | 八部分边界、路由覆盖、审计限制继承、未决选择明确 | 形成可审阅设计 |
| 投影小样本（后续 10–30 人） | Schema/引用/缺失/排除/时间/泄漏测试；人工 spot check；相同输入确定性复现 | 这些样本能正确转换，不代表人口或行为 |
| 最小闭环 | 一次正常行动、一次拒绝、可见性隔离、P 冻结、世界与日志一致、错误恢复；M 更新关闭 | 架构可运行，不是拟人性验证 |
| 动态学习 | 去重、反证、巩固、衰减、遗忘及关闭学习对照 | 实现了可审计机制，不证明机制符合真人 |
| 批量 materialize | 前述门槛通过；固定同一规则；分块、幂等、统计和抽查报告 | 规模化转换成功，不自动具有代表性 |
| 行为验证 | 独立真人目标、基线、匹配信息/行动空间、指标/置信区间、稳健性 | 仅在对应样本和任务范围内的拟真证据 |

保留 A 基础、B 专业、C 个体化、D 专业+个体化的 2×2 主实验。共同 World 规则和任务信息一致；“专业层关闭”不能同时改变实际奖惩。P/M0/X0 中哪些属于个体化输入必须显式列出，各组仍应有必要的公共情境 X。

先在固定学习配置下比较四组；再单独比较“无 M 更新 / 有 M 更新”，避免同时改专业层、个体化和学习后无法归因。交互效应可以估计，但对系统配置的作用不等于人类心理因果机制。

主要指标仍包括个体一致性、类别平衡指标、群体分布偏差和真人复测参考；重复 Agent 采样不是新增真人样本，置信区间按受访者等独立单位计算。WVS 态度留出只能验证态度回答拟合，不能改名为真实社会规范行为验证。

## 10. 冻结前的最小决定清单

- [ ] 确认本文件的架构/权限和路由默认保守原则。
- [ ] 确定首个情景、预测对象、行动空间、真人目标来源及是否具备同条件比较。
- [ ] 确定调查时间与模拟时间的对齐方式。
- [ ] 审阅拟启用的少量 ontology/mapping 条目；不要求再次审批全部来源审计。
- [ ] 冻结 ExperimentRolePlan 后实现 Schema/Projector，选择覆盖缺失和边界情况的 10–30 人；小样本不是代表性抽样。

以上未决项不妨碍先完成规范，但会阻止正式投影和行为结论。E1–E7 在用户新定义中仅被提及，本规范不自行补造其七项具体含义。
