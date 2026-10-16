# Experiment Spec：智能行为认知研究的四阶段实验路线

版本：1.2.1｜优先级修订：2026-10-01｜实验优先级与首个实验设计：Designed / Frozen in Spec；非实验已实施／机制已验证声明。

本文件只维护研究与实验选择。状态/运行边界见 [Architecture Spec](Architecture_Spec.md)，证据/路由/投影边界见 [Data Contract](Data_Contract.md)。历史 [Selection Plan v1](../02_agent_world/vertical_slice/Selection_Plan_v1.md)因选择清单的路径与哈希依赖而原位冻结，不再作为当前维护入口。

本轮仅在 v1.2.1 总体架构下冻结实验优先级与首个实验设计，未修改代码、运行配置、Data Contract、研究日志、Persona 或历史产物，未运行 Python、测试、模拟或 API。工程状态沿用此前用户同步及静态接口梳理，不重新验证 Context Builder，也不开始 Phase I-A。能力／证据等级统一采用 Architecture Spec 第 1.3 节，遵守 Implementation ≠ Behavioral Effect ≠ Mechanistic Evidence。旧设计语义保留在下文历史定位中，不能把旧产物按新条件重新命名为正式机制实验。

## 1. 科学定位与旧 Research Questions 审计

原本科核心问题：RQ1 基础 LLM 与真人社会规范选择有多一致；RQ2 专业规范层是否改善；RQ3 数据支撑的 Persona 是否解释个体差异；RQ4 两层结合是否优于单层。RQ5 多 Agent 群体模式为探索性扩展。架构闭环不回答 RQ1–RQ5。模型塔是文献概念，层级接口与评估方案是本项目设计；改善行为拟真仍是假设。

以上为 v1.1 的原始问题含义，予以保留，不再直接等同于当前第一阶段目标。新科学定位是 **Intelligent Behavior and Cognition 的行为实验基础设施**：实际行为是主要观测对象，认知记录是待验证解释变量。研究主线为 **Simulation → Behavioral Science → Mechanism → Collective Science**，社会规范保留为核心研究方向；首个正式科学实验暂定为 Prior Social Behavior → Subsequent Behavior，不预设必须形成合作或规范。

| 原问题／旧工作 | 新路线中的归属 | 仍有效的内容与调整 |
| --- | --- | --- |
| RQ1：基础模型与真人选择一致性 | 推迟至独立 human validation 分支，不是 Phase I | 问题仍有效；无同情境独立真人行为标签时不可回答，不能用问卷 Persona 或群体趋同代替 |
| RQ2：专业规范层改善拟真 | 机制干预可在 Phase III 重构；“改善真人一致性”部分延后 | 先固定知识／信息操纵，研究实际行为效应；后续社会信息实验的 C1/C2 不自动等于原专业层，旧 public_rules_reference 也不等于专业规范机制 |
| RQ3：Persona 解释个体差异 | Phase II 发现异质性，Phase III 比较初始化条件；真人解释效度延后 | 保留真实来源个体差异价值；不能把模型之间差异归因于真人人格或称为复现个体 |
| RQ4：两层结合优于单层 | 待单因素操作化后，再做 Phase III 交互／消融；真人优越性另验 | 原 2×2 逻辑保留，先明确“优于”的行为测量；不与首个 C0–C2 三组实验混为同一设计 |
| RQ5：多人群体模式 | Phase IV；Phase II 可提供候选现象 | 先辨识微观效应，再研究互动与群体动力学；稳定、冲突、消退和无模式都合法 |
| S12 闭环、transport 四组、API smoke | Phase I architecture validation／历史工程证据 | 它们从未完成 RQ1–RQ4，不应追溯包装成正式机制实验 |
| 社区中 WAIT、讨论未落实、采购／领取变化 | 历史 behavioral discovery 候选及工程诊断 | 提供假设来源；代码与配置变动、资源机会改变均是竞争解释，不凭前后两次运行作因果结论 |

本次研究安排是用户明确的新设计选择，不自动表述为导师新增要求或论文结论。正式申报中的拟人性目标若仍是硬性要求，需要另行与导师确认是否保留后续 human validation 子任务；不能用新的工程或机制结果声称完成该目标。

## 2. Experimental Phases

四阶段共用既有 12 个 Persona、同一 Python Runner 和状态／授权边界。Discovery、Hypothesis-testing、Collective Dynamics 是未来研究模式，不是当前三个已完成的运行后端。下面的阶段门槛属于设计要求，未声称已通过。

**冻结顺序：Phase I-A Offline/Static Validation → Phase I-B Runtime Closed Loop → Phase I-C Behavioral Trace Reconstruction → Minimal Behavior Experiment: prior behavior → subsequent behavior → competing mechanism experiments → longitudinal interaction → structural/group experiments。** Phase II 优先做最小行为效应实验，Phase III 再区分机制；其后先纵向互动，再结构／群体研究。此为既有四阶段内的优先级调整，不推翻架构。Rich Community 的发现用途和原社会信息实验均保留，但不再优先于首个最小行为实验。

### 2.1 Phase I — Behavioral Infrastructure

目标是验证 `Observation → X → Decision → ActionRequest → GM → World → Feedback → next Observation` 能在明确的运行范围内稳定、可追踪运行，不证明社会规范或真人拟真。P 冻结；M 冻结，`learning_enabled=false`，不为研究长期学习提前启用 Consolidator。

当前优先工作分为：

- **已实现、待运行验证**：统一 Context Builder、可替换 MemoryAccessPolicy、current Observation／episodic history 的 event/request ID 去重、各来源预算审计、PreparedDecision 冻结输入。状态统一为 **`implemented / not runtime-validated`**，不是新一轮从头设计任务。
- **需通过验收证明**：必需新信息不丢失；授权 P 不裁剪；输入预算及超限失败可诊断；judgment/intention 与 ActionRequest、GM 结果清楚分开；OPEN／structured action 的实际执行、拒绝及定向反馈可追踪；跨阶段／轮次状态和事件可重建。
- **未来获准后的第一项代码任务仅对应 Phase I-A**：offline validation + read-only behavioral trace readback。补齐通用 Context／行为链的离线验收用例与只读回读检查，覆盖必需信息、去重、预算边界、授权不扩大、PreparedDecision 与发送载荷构造一致，以及反馈到下一决策的引用连接。此时不调用 API；离线载荷检查不能冒充真实服务端调用验证。依据之后获准执行的验收结果修复已有实现，不继续设计一套 Builder。

### 2.1.1 Phase I-A — Offline / Static Validation

在不调用 API 的前提下，验收 Context Builder、必需信息保护、Observation／episodic 去重、input budget、PreparedDecision、Persona 授权不扩大、状态与事件引用，以及日志和 readback 完整性。可在另行获准后采用静态检查、离线用例和只读回读；使用夹具／保存响应时标明来源，不能伪装为新增模型运行。产出限定于具体版本和覆盖范围的离线检查记录，不能据此宣布 Phase I 完成。本轮仅定义门槛，不执行上述检查。

### 2.1.2 Phase I-B — Runtime Closed Loop（运行工程验收）

在冻结版本、配置和明确轮次／负载范围内，实际跑通 `Observation → X → Decision → ActionRequest → GM → World → Feedback → next Observation`。覆盖正常成功、GM 拒绝、违反规范但可执行、预算失败、可见性隔离等必要分支，记录后端、调用与失败方式。预算失败分支本身应显式停止，不能强行继续闭环；其他成功路径须能完成后续反馈。各分支的受控覆盖不等于模型自主产生了某种行为；离线替身不替代拟声明的真实模型后端验收，网络／API 执行需另行授权。

### 2.1.3 Phase I-C — Behavioral Trace Reconstruction（轨迹重建验收）

验证研究者能从指定版本的运行日志可靠重建：**Agent legally received what → model actually saw what → X changed how → requested what → GM actually executed/rejected what → World changed how → who received what feedback → next behavior**。交付、实际输入、认知报告、请求、结算和下一决策必须可关联；失败、缺失和无后续行为明确标记，不补造连接。I-A 可以先实现／检查回读工具，但 I-C 还须在相应运行轨迹上证明重建成立；实际输入记录也不等于证明模型注意到或相信了该信息。

**总门槛：只有 I-A／I-B／I-C 在预先声明范围内均通过，才能称 Behavioral Infrastructure ready for Phase II within the validated scope。** 记录各层版本、配置、覆盖范围、证据位置和未覆盖事项；版本变更后不能自动继承旧结论。I-C 是轨迹完整性验收，不是 Behaviorally Validated；三层通过仍不证明行为效应、无限长期稳定、跨场景通用或开放世界能力。

Phase I-A／I-B／I-C 只承担 behavioral infrastructure validation；第 2.2.1 节最小科学实验不作为 Phase I 验收条件。首个科学实验只有在三层对声明范围全部通过后才能正式运行，不能把观察到帮助效应或合作作为基础设施验收要求。

### 2.2 Phase II — Behavioral Discovery

本阶段首先开展第 2.2.1 节 **Minimal Behavior Experiment：Prior Social Behavior → Subsequent Behavior**，检验行为效应而不直接识别内部机制。Minimal Experimental Scene 与 Rich Community 并存，目前仅有本轮设计冻结，不借旧 A/B 协调场景或工程夹具冒充正式科学实验实现。

Rich Community World 继续定位为 **Naturalistic / Discovery Environment**，用于生成 behavioral phenomenon catalogue，不直接作为唯一规范因果实验，也不作为首个最小实验的必经前置运行。既有发现可保留；新版本只有经过 Phase I 门槛后，其数据才适合做较稳定的现象收集。以下目录与发现规则保留，后续执行另行安排。

候选目录包括 imitation、social responsiveness、waiting/free-riding、helping、proposal/coordination、intention–behavior inconsistency、repeated failure、public commitment vs actual action、behavioral persistence/adaptation/heterogeneity 及 path dependence。这些名称是待操作化的现象标签，不是自动检测器：WAIT 可能来自不可行动，发言不自动是承诺，模仿外观也可能来自共同约束。

每个目录项记录：运行／代码／配置版本、具体行为序列与合法信息、行动可行性及资源条件、重复和反例、竞争解释、未知项，以及待检验假设。只使用独立重复运行来考察现象稳定性，不把同一 run 的 12 个互动 Agent 或 192 次调用当作独立实验样本。先冻结发现规则，再在新运行中确认可复现范围；不得筛掉“没有合作”的合法结果。

Phase II → III：优先围绕首个最小实验及其重复结果提出竞争解释，记录效应、不确定性、无效应／反例和工程排查依据；后续也可从发现目录选择适合操纵的现象。不凭单次引人注意的结果直接宣称机制成立，也不为获得正向效应而筛掉零效应或反向结果。

Discovery 的输出只能是 **behavioral phenomenon + competing explanations + candidate hypotheses**，不能直接产出机制结论。进入 Phase III 前重新冻结 **Phenomenon → Competing Explanations → Manipulation → Behavioral Outcome → Analysis**。机制实验的目标是区分多个合理解释，而非只寻找支持偏好解释的结果。例如“公开批评后行为减少”，至少需考虑 normative expectation、punishment/risk avoidance、imitation、salience/wording、resource/opportunity change 和 retrieval exposure；本例只说明竞争解释原则，不在本轮新增正式实验。

### 2.2.1 首个正式科学实验：Prior Social Behavior → Subsequent Behavior

**设计状态：Designed / Frozen in Spec。** 本节冻结实验优先级、主要对比与下列设计约束；不是 Implemented、Engineering Validated、Behaviorally Validated 或 Mechanistically Supported。核心问题为：**在决策时客观状态匹配的条件下，来自交互对象的前序帮助行为，是否会改变主体之后针对该对象的实际帮助行为？** A 为目标决策主体，B 为交互对象，不改变第 1 节旧 RQ 的含义。

**条件与主要因果比较。** 本节 C0–C2 仅指首个帮助实验，与第 2.3 节保留的社会信息实验同号但不同义，不得混用配置、产物或结论。

| 条件 | 前序操纵 | 比较角色 |
| --- | --- | --- |
| C0 Low-resource baseline | A 未获得 C1/C2 中的资源增量，保留低资源状态 | baseline / descriptive reference；不承担社会来源效应的主要因果识别 |
| C1 Partner-help | B 的前序帮助使 A 获得资源增量 | 主要处理条件；B 行为由实验控制器脚本安排 |
| C2 Matched non-social gain | 非社会性环境事件使 A 获得相同资源增量 | 主要匹配对照；目标决策时匹配 C1 的决策相关客观状态 |

**Primary causal contrast 固定为 C1 Partner-help vs C2 Matched non-social gain。** C0 的描述性比较不能代替 C1−C2 的社会来源识别，不得将三组描述为“资源完全匹配”。

**State matching。** 匹配时点为 A 作出目标帮助决策时，而不只是 trial 起点。C1/C2 必须匹配所有可能影响该决策、且对 A 可获得的 decision-relevant objective state，至少包括：

- A 当前资源；
- B 当前资源／need state（若 A 可观察或可推断）；
- 当前 action opportunity、help cost、possible benefit；
- interaction horizon、permissions、world rules、information visibility；
- 与目标行为有关的其他环境状态。

唯一计划中的关键差异应尽可能收敛为“A 的资源增量来自 B 的帮助行为”与“相同增量来自非社会性环境事件”。不要求完整世界历史逐字段相同，但所有额外差异须显式记录并判断是否可能构成竞争解释；不能仅核对 A 的余额就声称完成匹配。客观状态匹配不等于强制两条件的 X 判断、关系解释或主观报告相同。

**B resource confound 必须解决。** 若 C1 帮助消耗 B 的资源、C2 却不消耗，则 A 后续帮助 B 可能响应的是 B 的资源／需要差异，不能直接把 C1−C2 解释为 partner-action effect。实现前优先采用“在目标决策时匹配 B 对 A 可见的资源和需要状态”的方案，并核对 A 可推断的相关差异，使社会行为来源成为主要操纵变量。具体工程实现方式本轮不决定；未满足该条件不得按本设计宣称完成社会来源识别。

**Manipulation provenance。** B→A 的前序帮助由实验控制器安排，属于 randomized / scripted partner behavior manipulation，而不是 B 的自主选择。条件分配与脚本版本须可追溯；帮助及非社会收益均须有相应 World/GM 执行和合法 Observation 记录，不以一句未执行的帮助陈述替代行为操纵。未来日志与文档必须区分 `scripted experimental manipulation` 和 `autonomous Agent decision`；不得把前者写成“B 自主选择帮助 A”。自主 B 行为留到后续 interaction / mechanism experiments。

**Primary outcome。** 固定为 GM 实际执行后的 **ActionResult**，即 A 针对 B 的实际帮助行为结果；不能用 verbal intention、reasoning report 或 ActionRequest 替代。ActionRequest、X/cognition report、verbal rationale 仅作 secondary/process variables；拒绝、部分执行及工程失败分别记录，不能把未执行请求当已帮助，或把 API 失败默认为 WAIT。

**Persona 与重复单元。** 复用已完成的 12 个 Persona 及既有授权视图，不重转换、不扩权。Persona 先作 blocking / heterogeneity factor，而非主要解释变量；每个 `Persona × Condition × Repeat` 从独立重置状态开始。主要估计 `P(Behavior | Persona, Condition, Model)`，比较同一 Persona 在不同条件下的行为变化。独立 API repeats 是模型重复采样，不是独立人类 subjects；12 人覆盖样本不据此成为代表性人群。

**Memory / carry-over。** 首个实验保持 `P frozen`、`M disabled/frozen`、`learning_enabled=false`。每个 trial 重新初始化 World 与动态 Agent state（包括 X、经历、任务／意图及决策上下文），不能继承上一 trial 的反馈、判断或检索内容。trial 内的合法前序行为观察及其经历可以进入目标决策，trial 间不得 carry-over。memory policy 在条件间保持一致并记录实际输入；未来 memory-access 操纵不在本轮展开。

**Interpretation boundary。** 即使在匹配与操纵检查成立后观察到 C1 > C2，Phase II 也只能作“prior partner behavior has a measurable effect on subsequent behavior”或相同强度、限定模型／任务／条件的 behavioral finding。不得直接宣称 reciprocity mechanism、gratitude、norm internalization 等内部机制成立。Phase III 再以竞争解释实验区分 direct reciprocity、generalized social learning、future-interaction expectation、normative influence、relationship update 及其他可操作候选机制；同调用 cognition/action 报告不单独提供机制证据。

上述约束已冻结，但具体资源数值、B 状态匹配的工程方式、行为编码与效应量、条件随机分配执行方案、重复数及依据、失败处理与停止标准仍须在正式执行前明确并冻结；不能把这些待落实细节当作改写主要对比或降低匹配要求的授权。本轮不创建配置或执行入口。正式运行还须满足第 2.1 节 I-A／I-B／I-C 的全部范围门槛。

### 2.3 Phase III — Mechanism Experiment

在首个最小行为实验之后，使用／扩展 **Minimal Experimental Scene** 区分竞争机制，与 Rich Community 并存而非替代后者。机制实验优先区分第 2.2.1 节列出的行为效应候选解释；这些均是研究设计，不表示相关场景、操纵或机制已实现。

原首个推荐问题完整保留并后移为机制区分阶段的 **descriptive / normative social-information experiment**：**社会行为信息和社会评价信息是否对 Agent 的实际行为产生不同影响？** 以下 C0–C2 仅属于该后续社会信息实验，是待审核候选，不是首个帮助实验的条件或已冻结的执行条件：

| 条件 | 可操纵信息 | 必须控制的边界 |
| --- | --- | --- |
| C0 No Social Information | 不提供作为操纵的他人行为频率或赞同／反对信息 | 保留共同场景事实、操作规则、自身反馈及必要新交付；不是把 Agent 变成无观察状态 |
| C1 Descriptive Behavior Information | 他人实际／实验脚本规定的行为信息，来源明确 | 不夹带赞同／反对、义务语言或额外物质奖惩；脚本信息标为实验条件，不冒充真人证据 |
| C2 Normative / Approval–Disapproval Information | 他人的赞同／反对或规范性评价信息 | 不同时改变客观处罚、资源和行动权限；与“多数人实际做了什么”分离 |

保持世界初始状态、收益／资源、行动机会、调度、Persona 授权视图、模型／提示协议、预算和默认 memory policy 尽可能一致。信息来源、目标行为、时间、消息长度／表达风格与可见性须在具体 RolePlan 中固定；必要的中性填充对照先设计，不在看过结果后调整。C0 不得以删除已合法交付事件的方式实现：场景在信息产生和交付前定义操纵；自身行动后果仍照常交付。

行为目标为比较 `P(Action | C0)`、`P(Action | C1)`、`P(Action | C2)`，主要行动指标基于 GM 实际执行；同时独立报告请求分布、拒绝及 World 后果，避免把被拒绝的愿望算成实际行为。分别记录 O、X 的判断／预期记录、A_request、A_result、World effect、F 及后续 A'。当前没有独立 expectation 测量器；字段生成不是机制成立证据。

竞争解释至少包括信息内容效应、措辞／显著性效应、资源／机会差异和检索暴露差异。条件对行为的作用只支持该操作化干预的系统内效应；C2 与 C1 有差异不自动证明 N3 已成立，更不能直接证明内化或人类机制同一性。进一步区分解释需要独立操纵或消融，不仅比较 self-report。

**Phase III 的独立机制证据门槛：** cognition report 与 action 若由同一次模型调用共同生成，只能提供过程记录、相关关系和候选解释，不能单独支持 X → Action 的因果中介主张。O 后同时输出“我预期别人会反对”和行动 A，不证明 normative expectation caused Action A；写日志的先后、JSON 字段顺序或自述理由都不能代替证据。

若要检验 O → X → Action 中 X 的作用，至少预先选择一种独立机制证据设计：认知测量与行动生成时间分离、X 形成后对 X／可访问状态干预、expectation state available vs unavailable、controlled ablation、competing computational models，或其他能区分相关记录与实际中介作用的方法。时间分离本身不是充分证明；所选方法须说明如何区分竞争解释、处理测量／干预副作用及结论范围。**Implemented cognitive states are manipulable experimental representations, not validated cognitive mechanisms.** 本轮不决定采用哪种方案，不拆分模型调用、不增加 API 次数；获得适当证据后也只使用 Mechanistically Supported，而非绝对证明。

正式执行前另行冻结：具体场景与行为集合、条件分配单元、重复数及其依据、共同随机事件／顺序控制、主要对比 C1–C0/C2–C0/C2–C1、效应量与不确定性、失败处理和停止标准。互相影响的 Agent 不作独立随机化样本；优先使用彼此隔离的运行副本，若采用组内操纵必须显式处理干扰。发现 run 不兼作确认 run；超限／API 失败不能编码成 WAIT 或默默排除。

之后逐项考虑 no retrieval／recency／relevance／fuller history、social observation visibility、public vs private feedback、human-grounded vs reduced/common Persona、expectation state available vs unavailable。一次只加有明确假设的因素，不同时展开全因子设计。memory-access 接口已预留，仅默认 bounded-relevant 已实现；“no episodic retrieval”不等于没有当前 Observation、清空 M 或删除本地经历。

### 2.4 Phase IV — Collective Dynamics

在部分微观行为效应经受控重复和竞争解释检验后，先开展 **longitudinal interaction**，再推进 **structural/group experiments**，研究 **Individual Cognition → Interaction → Mutual Adaptation → Collective Dynamics**。重新冻结网络、人口、环境、信息传播、重复数和预算，检查受控场景效应是否迁移或失效；不把纵向互动与结构／群体比较合并成一个无需分步设计的实验。

群体结果可以是 shared convention、descriptive norm、normative expectation、norm conflict、local norms、polarization、norm decay 或 no stable pattern。行为趋同、他人行为预期、赞同预期和内化各需独立操作化，不能只凭一个趋同率命名规范。相互适应可以先通过 X／合法记忆发生，不以启用 M 学习为前提；任何 Consolidator 研究须独立设计与批准。

独立 human validation 是保留的外部效度分支，不因完成四阶段自动获得；需另有同条件真人行为标签、目标隔离和评估协议。既有 12 人覆盖样本不代表总体，也不足以支持政策预测。

## 3. Cohort Selection

本节保留冻结选样包的历史语义。当前复用已经转换完成的全部 12 个 Persona，不重抽样、不重新转换；选样包的 `selected_pending_confirmation` 是历史字段，不等于当前还没有初始化产物。

首个 vertical slice 选择 12 名不同的 WVS7_CHN_2018 真实受访者作为 architecture-test/development cohort，不随机、不代表总体、不是独立真人行为 holdout。正式证据运行固定为 `WVS_full_2026-09-17T09-33-15-228Z`；筛选不读取 31 项 VALIDATION_TARGET 候选的答案或缺失画像，不拼接来源、不修改回答、不根据守规矩程度挑人。

候选统计集合是 F_select=215、F_P=13、F_M=137、F_X=11 项；这些只是未启用目录上的覆盖计数，不是允许输入清单。合法特殊码计为可解释值，来源缺失保留原原因；44 项共同未测量不参与个人缺失排名。高/低阈值用全池 nearest-rank 分位数 `sorted[ceil(p*N)-1]` 的 q80/q20；稀疏槽位还须有实际缺失，不能因阈值相同伪造差异。

12 个主槽位依次覆盖：S01 高可用、S02 整体缺失、S03 P 稀疏、S04 M0 稀疏、S05 X0 稀疏、S06 Q284 合法 -5、S07 Q274 top-code 7、S08 当前职业条件、S09 过去职业条件、S10 就业条件未知但有职业码、S11 既有跨题一致性标记、S12 Q286 的 2017/2018 时间边界。附加 9 标签为有效 0 子女、有效 unsure 类别、no_answer/dont_know/not_applicable 三种来源缺失、年龄-出生年差异标记，以及 P/M/X 各自 rich。共同守卫包括来源 hold、Q290 限制和 Q263 排除。

选择须全局匹配 12 个不重复来源 ID：先验证完整匹配，再逐级最大化 9 标签覆盖、S01 最低 P/M/X 有值比例及总可用数，再依序最小化 S02–S05 的对应有值数；最终按 S01→S12 来源 ID 的 UTF-8 字典序打破并列。不可行或未证实最优时报告 gap，不暗中放宽；选后回读 Evidence 校验值、缺失、来源与时间。完整谓词、算法版本与实际阈值在冻结的[选样包](../02_agent_world/vertical_slice/selection_v1/coverage_report.md)及[manifest](../02_agent_world/vertical_slice/selection_v1/selection_manifest.json)中，不在本 Spec 复制 12 个 ID。

已执行结果为 12/12 主槽位、9/9 标签、12 个不同 ID，状态 `selected_pending_confirmation`。这只是已定义的选样覆盖；实际获准输入字段的 unknown/特殊码覆盖尚未检查。若后续最小输入集合无所需边界，报告 runtime coverage gap，再讨论是否换人；不能用字段级候选覆盖代替运行输入覆盖。

上述“尚未检查”属于选样阶段记录，本轮不重新验收其运行覆盖。当前 Persona 的实验角色为 **Human-grounded individual-difference initialization**。12 人是 development/architecture cohort，不是 12 个真人行为标签，也不是统计代表样本。未来 reduced/common 条件仅在单独批准后形成明确的实验决策视图，保存完整原 P 与授权差异，不把删减后的未知解释为来源未测量，不因比较需要扩充外发字段。

## 4. 历史 Scenario Kernel：首个协调 Vertical Slice

第 4–7 节保留 2026-09-20 首个 S12 协调实验的设定和调用入口，归入 Phase I 历史工程证据，不是当前 Rich Community 或未来 Phase III 的正式配置。

首个场景为 6 轮 A/B 重复协调，单个 VS12 与确定性脚本角色互动。公开规范期望选择 A；B 违反该期望但允许执行，并收到不赞同反馈。匹配得 1 分，否则 0 分；SET_PAYOFF 因无修改收益权限而拒绝，拒绝不推进 World。脚本未来选择私有，已执行选择在反馈中公开。该规范由实验设置，不声称自然涌现。精确参数见唯一运行配置 [coordination_v1.json](../03_experiment/configs/coordination_v1.json)。

## 5. 历史 ExperimentRolePlan 与继续有效的隔离要求

ExperimentRolePlan 与场景合并冻结在上述配置，不新增平行说明文档。本轮使用已选 S12，测试真实缺失、合法 unsure 与年龄/回溯时间边界；选择历史包仍保持原字节及 selected_pending_confirmation，不据此宣称 12 人已全部验收。simulation time 为从 2018 调查快照初始化的反事实抽象轮次，不外推成 2026 的真人属性；Q286 仍指 2017。无真人 A/B 留出目标；原 31 项目标候选及语义派生保留隔离，不计分、不作为输入。

## 6. 既有 Enabled Ontology / Inputs

完整候选资料保留该人 290 个 Evidence 字段位置，目标内容独立封存。P 保留 L1–L8，依据 [版本化映射](../00_persona/rules/coordination_persona_mapping_v1.json)填入合法、获准的初始自报属性，保留来源题项语义而不推断稳定心理特质；缺失、未测技能/机制/策略为 unknown。未获准或无充分语义依据的字段保持 Evidence-only。旧 proposed 目录不自动生效，逐 Evidence 正式决定由本配置和映射生成。

最小化的是运行机制与决策许可视图，不是完整 Persona。决策仅允许 Q1–Q6、Q57、Q77、Q91、Q262、Q272、Q275；它们的缺失仍为 unknown，不补值。候选资料、完整 P 与决策视图分别保存/构造。M0 空且未巩固；X0 不导入旧调查即时状态，X 只随本轮真实交付的 Observation 更新。

## 7. 历史 Runtime Configuration（TypeScript）

`learning_enabled=false`，P/M 深冻结；独立运行器实现 Observation → X → Decision → ActionRequest → GM → World → 可见反馈闭环。离线后端人为请求一次 SET_PAYOFF、执行一次 B，并依据可见反馈作后续选择，用于工程分支覆盖，不测 Persona 的行为效应。日志记录实际提示词/响应、请求/结果、交付/阻断、X 转移和世界快照；RunManifest 固定数据、配置、代码、Spec 与产物哈希。幂等限单进程，尚无持久化事务恢复，也尚未接入 Concordia。

运行命令：`bun step/02_agent_world/runtime/run.ts --backend=offline`。测试：`bun test step/02_agent_world/runtime/runtime.test.ts`。真实模型用 `--backend=llm`，从 VS_LLM_ENDPOINT、VS_LLM_MODEL、VS_LLM_API_KEY 环境变量读取 OpenAI-compatible HTTPS 配置；缺失即显式失败，不自动降级，不读取 .env。

## 8. Ablation Design

以下为保留的模型塔 2×2 研究设想，尚未完成正式操作化；不取代第 2.2.1 节首个帮助实验或第 2.3 节后续社会信息候选条件，也不将 transport 条件升级为正式机制条件。

未来同情景、同信息与行动空间的 2×2：A 基础 LLM，B 加专业层，C 加个体化层，D 两层皆加。World 的真实规则保持一致；关闭专业层不能同时改实际奖惩。个体化层究竟启用哪些 P/M0/X0 条目需事先列明，公共情境 X 不应随之消失。学习开关若后来启用，应独立比较，避免与分层消融混杂。四组差异只能支持这些系统配置的比较，不能直接证明人类心理因果机制。

## 9. Validation Criteria 与 Behavioral Measurement

### 9.1 工程验收与独立真人验证

当前只准备 Phase I-A，Phase I 整体按第 2.1 节的 I-A／I-B／I-C 分层验收：确定性初始化、证据追溯、缺失完整、P 不变、目标隔离、Observation 可见性、ActionRequest/ActionResult 分离、World/log 一致、失败显式记录、完整 RunManifest。验收范围至少覆盖正常执行、GM 拒绝、违规可执行、预算失败、可见性隔离、X 更新，以及 unknown/特殊编码/矛盾 Evidence 的 Agent 正常运行；后者需相应运行证据，不能由离线检查一并推定通过。

Human validation 是独立外部效度分支，须有同条件独立真人留出行为数据、适配任务的基线、预先冻结的 behavioral outcome type／指标与分析方案、不确定性、适用的真人复测参考及模型／提示词／随机性稳健性评估。多数类基线、balanced accuracy／MCC 仅适合相应分类任务；无 Persona 和简单统计模型也须与任务匹配，不能机械套用。

| 预先冻结的 behavioral outcome type | 可选指标示例（非完整指标库） |
| --- | --- |
| categorical choice | balanced accuracy、MCC；有概率预测及对应观测时可评估 calibration |
| continuous action | MAE、适当的 distributional distance／effect size；区分配对误差与分布比较 |
| behavioral distribution | distributional agreement，明确分布对象、尺度和不确定性 |
| sequential behavior | transition／trajectory metrics，固定时间对齐与序列比较单位 |
| treatment-effect prediction | effect-size agreement／uncertainty，需有可比较的人类效应估计及其识别依据 |
| longitudinal behavior | trajectory／adaptation metrics，固定时间跨度、机会条件及缺失处理 |

**Balanced accuracy / MCC are task-specific examples, not universal human-fidelity metrics.** Persona 问卷不是独立真人行为标签；repeated Agent samples 不等于新增真人样本；simulation pattern 不自动等于 human behavior。历史重演仍须防模型记忆和事后信息泄漏。此分支与 Phase III 的系统内行为实验分开，不要求后者必须先有真人标签。

### 9.2 候选 Behavioral Measurement 层（设计，尚未实现）

测量对象以实际执行和客观后果为主，请求、拒绝、内部判断及发言独立报告。以下为候选概念，不在本轮实现公式、阈值或评分器。每项使用前须固定行为编码、观察窗口、机会／可行性分母、干预时点、缺失与拒绝处理、重复单元及不确定性方案；不按首次结果临时定义成功。

| 概念 | 测量什么行为 | 所需日志／条件 | 不能推出什么 |
| --- | --- | --- | --- |
| behavioral persistence | 在仍有可行机会时，已执行选择或行为序列跨时保持的程度 | 请求与结果、资源／前置条件、时序、情境变化、实际上下文 | 不能由重复选择直接推出稳定人格、内化；不可行导致的不变须分开 |
| behavioral adaptation | 反馈／环境改变后实际行为的变化及延迟 | 改变事件、定向交付、前后机会与执行、对照条件 | 变化可能来自行动集合改变，不自动证明学习或 M 巩固 |
| social sensitivity | 社会行为／互动信息操纵对实际选择分布的效应 | 实验专属条件、实际暴露、请求／结果、世界控制、独立重复；首个帮助实验主要比较第 2.2.1 节 C1/C2 | 无控制的相关变化不证明他人信息造成变化，也不等于亲社会性 |
| normative sensitivity | 赞同／反对信息在区别于行为频率和物质奖惩条件下的行为效应 | 第 2.3 节后续社会信息实验 C0/C1/C2、消息来源／内容、可见性、收益、执行记录 | 不等于道德水平、规范内化或 N3 机制已被证实 |
| intention–behavior consistency | 显式任务／意图与后续可执行步骤、调整或放弃之间的对应 | task_id、意图更新／取消、请求关联、GM 结果、机会、时间窗 | SAY 不自动是承诺；计划改变或 GM 拒绝不能一概算不诚信；不能读取模型隐含意图 |
| behavioral heterogeneity | 相同可比条件下不同 Agent 的实际行为分布差异 | 初始化视图／授权哈希、模型／seed、网络位置、资源机会、重复 | 不自动是真人人格差异；12 个覆盖样本不支持总体外推 |
| path dependence | 当前条件相同而早期历史不同的行为差异 | 预设不同历史、实际交付／检索、匹配当前 World 与输入、独立运行 | 单次先后相关不证明路径依赖；当前状态未匹配可能是混杂 |
| reversal / recovery | 干预撤除／反转后，行为是否回转、恢复及所需机会数 | 干预切换、O/F 时点、实际行为、资源约束与基线 | 不必然是遗忘、去内化或现实韧性；可能是机械资源恢复 |
| interaction responsiveness | 合法收到请求／发言后是否、何时以可关联的行为回应 | source_request/event/observation ID、接收者、回应及执行、时间顺序 | 发言次数不等于协作质量；时间邻近不等于响应因果，沉默不等于拒绝帮助 |

公共表态与 task 意图分开编码；要把某条话语判为承诺，须先定义编码规则和可靠性，不能由文本中出现“愿意”自动归类。行为编码可区分结构化类型与实际效果，OPEN 是否产生事件以 GM 结果为准。工程格式错误、预算失败、资源拒绝与自主未行动分别保留。

不创建 sociality score、morality score、norm score 等没有构念边界的单一总分。Evaluation 只读；研究者事后完整视图不回流为运行中 Agent 可知信息。

## 10. Current Experiment Status

### 10.1 当前版本与研究阶段（工程记录沿用 2026-09-27；设计优先级更新于 2026-10-01）

读取的当前配置为 [community_python_v1.json](../03_experiment/configs/community_python_v1.json)：`experiment_id=community_scene_cognition_v1`、`kernel=community_daily_life_v1`，SceneAdapter 为 `scene_community:LivingCommunityScene`，复用现有 12 人；8 轮、1 次重复、seed=17，每轮一次交流加一次行动。认知协议为 `judgments_intentions_v1`，OPEN 解释器为 `bounded_social_v1`；Consolidator 与 norm inference disabled。未来运行若仍采用该配置，首次尝试计划为 12×8×2=192 次决策调用，失败／重试另计；这不是本轮运行结果或实验功效设计。

`community_daily_life_v1` 是丰富生活场景 kernel 名称，`community_scene_cognition_v1` 是使用它的当前实验标识，不是两个独立机制条件。两者的研究定位如下：

| 对象 | 当前阶段 | 证据边界 |
| --- | --- | --- |
| 旧 Python 社区 completed 运行 | Phase I 的历史工程运行证据；Phase II 的探索候选来源 | 全 WAIT、讨论多于实际集体行动、采购／领取与资源变化等来自历史记录／用户审计；本轮未重审计产物，不推出机制或真人效度 |
| community_daily_life_v1 Rich Community | 保留为 Phase II 发现环境，同时承载 Phase I 工程问题暴露；不再优先于首个最小科学实验 | 不是规范唯一因果实验，设施／市场／资金等实验假设可改变可行动空间 |
| community_scene_cognition_v1 当前版本 | 仍在 Phase I；通过后再继续 Phase II | 已知一次运行完成首轮交流的 12 次调用后，在行动阶段本地预留检查失败；没有完整认知版本运行通过证据 |
| Context Builder 修正版 | Phase I，**implemented / not runtime-validated** | 已写代码，未运行 Python／测试／模拟／API；不能称已解决长上下文问题 |
| 首个 Minimal Behavior Experiment：Prior Social Behavior → Subsequent Behavior | Phase II 优先；Designed / Frozen in Spec | C1 Partner-help vs C2 Matched non-social gain 为主要比较，C0 仅描述性参考；未实现、未运行或验证 |
| descriptive / normative social-information experiment；独立 memory-access 条件 | 后移／保留于 Phase III 机制区分候选设计 | 与首个帮助实验的 C0–C2 分开；尚未实现或运行，不借旧 A/B／transport 产物冒充 |
| 多人长期机制重嵌入 | Phase IV 未来研究 | 不是“当前已有 12 人”即可视作完成 |

此前失败的直接原因已经定位：旧 reservation 将转义后的 messages UTF-8 字节数再加余量；resident_01 从 27,054 增至 39,068，超过配置 32,768，伴随 Observation、X 增长及当前观察／历史检索重复。这些是旧本地预留值，不是服务端报告的 Token 数，行动阶段未发 API。修正版分离本地完整状态与决策视图，提供去重、分项预算、冻结输入和可替换策略；新默认代理估算和可选本地 tokenizer 均不能未经验证当作供应商精确计数。

配置中的 `notes` 是重构当时的说明，不是动态验收状态；本轮保留配置原字节。当前无显式 `context` 配置段，代码默认 bounded-relevant policy；只有此默认策略已实现，no retrieval／recency／relevance／fuller-history 四个独立条件尚未实现。其接口供未来机制比较使用，不作当前实验条件声明。

### 10.2 历史初始化与首个闭环记录（2026-09-20）

以下保留当时的状态、入口及产物语义，后续状态以第 10.1 节为准；“尚无多自主 Agent”“计划运行 12 人”等是历史描述，不是当前结论。

截至 2026-09-20，新增配置、正式路由生成器、确定性 Projector、单 Agent 运行器和自动化测试；真实运行存入 `step/03_experiment/runs/` 的独立目录，成功与否以各目录 RunManifest 和架构验收结果为准。原始/derived 数据、WVS 历史验证链和 12 人选样包不修改。尚无 Concordia 集成、运行期 M 更新、多自主 Agent、合成人口或真人拟人性验证；合成边界测试夹具不能充当真实 S12 的观测。

初始化入口已与 cohort 解耦：[initialize_personas.ts](../02_agent_world/runtime/initialize_personas.ts) 使用 `--selection <manifest>` 按来源 ID 名单处理，或 `--all` 处理来源配置中全部受访者；两者使用相同映射/RolePlan，不含专门针对 12 个槽位的投影分支。来源默认配置为 [wvs_persona_source_v1.json](../03_experiment/configs/wvs_persona_source_v1.json)，目前适配 WVS 已审计快照，其他来源需自己的 adapter/映射。身份使用来源内 ID，选样槽位只作可选元数据。源哈希每批校验一次，Evidence 逐人流式读取，不逐人重新扫描全量文件。

输出为 `step/03_experiment/persona_initializations/<dataset>/<timestamp_id>/agents/<source_record_id>/`，总清单为 `initialization_manifest.json`。只做初始化，不执行 World/Decision 或 LLM；与 simulation run 分开记录。每次创建新目录，不覆盖历史 S12；新稳定身份命名会改变完整状态哈希，不表示问卷值变化。批次失败停止并记录已完成/失败项，尚不支持断点续跑。用户本次计划自行运行 12 人，全量功能的存在不表示已转换或验证全部 3036 人。

## 11. 历史 Experiment Runner 实现状态补记（2026-09-20，TypeScript）

本节是旧 TypeScript 工程线路记录；包括 API 参数、预算及重放方式的描述只适用于该线路，不代表当前 Python 接口。保留其入口和原设计含义，不在本轮运行。下文旧指标清单中的 MCC／平衡准确率仅作为分类行为任务的历史候选；当前 human validation 的适用规则以第 9.1 节为准。

本节只补记用户新授权的工程实现，不修改第 1–9 节的研究问题、正式条件含义或评价要求。当前已存在 12 人 completed 初始化批次 `2026-09-20T11-32-22-103Z_e091968c`；Runner 验证其清单、状态、RolePlan、许可视图与路由哈希后直接加载，没有重新转换 Persona。第 10 节“尚无多自主 Agent”为本次扩展之前的状态。

入口为 [experiment_runner.ts](../02_agent_world/runtime/experiment_runner.ts)，参数为实验配置路径。[离线示例](../03_experiment/configs/experiment_offline_v1.json)使用已有 12 人、6 轮、4 个工程输入条件、2 次重复；[API smoke 示例](../03_experiment/configs/experiment_api_smoke_v1.json)使用同批次 2 人、2 轮、1 条件、1 次重复。两种后端走同一个调度/GM/World/反馈流程。

配置包括：人口初始化清单及可选成员/组别、唯一已实现 kernel `simultaneous_pair_coordination_v1`、ring/complete/explicit 网络及可选组内互动、interaction_only/public_completed_round 可见范围、经历窗口、模型后端、X 更新/专业参考模块、轮次、seed、重复次数、尝试上限及整个实验共用的调用/token/金额上限。不支持的 kernel 或机制显式拒绝，不静默忽略；相同 seed 的匹配在各条件间保持一致。每个 Agent 每轮至多参与一次匹配；无法匹配者收到 idle 观察。

多自主 Agent 场景是根据本轮实现请求对既有协调 kernel 的工程扩展，不取代未来正式社会规范任务设计。它先收齐本轮所有合法请求，再统一提交 World，之后才发布各自反馈。B 可执行；SET_PAYOFF 因权限拒绝。本轮公开规范提醒由 World 规则产生，不伪装为另一个 Agent 的真实赞同/反对。私有 Persona/M/X/经历隔离，完整 World、其他人的未公开选择和受限证据不进入决策。

A_transport/B_transport/C_transport/D_transport 对应 Persona 和专业参考输入开关的 2×2 工程验收，World 奖惩不随开关变化。`public_rules_reference_v1` 只是对已公开规则的版本化参考视图，用于检验能力输入线路，**不是正式专业规范层**，不能据此声称已经完成 RQ2/RQ4 的消融操作化。M0 当前为空；关闭 Persona 的条件不删除公共情境 X。N1–N6、长期内化和 Consolidator 未启用。所有条件仍在 `learning_enabled=false` 下运行。

运行命令：`bun step/02_agent_world/runtime/experiment_runner.ts step/03_experiment/configs/experiment_offline_v1.json`。测试：`bun test step/02_agent_world/runtime`。产物位于 `step/03_experiment/experiments/<timestamp_backend_id>/`，包括冻结配置、已加载人口快照、需求实现核对、总清单，以及逐 run 的 events.jsonl、World/各 Agent 终态、私有经历、基础统计、验收与 RunManifest。初始化源文件不改写。P/M 深冻结、X 写入者固定；轮次提交在进程内整体执行，尚无跨进程事务/断点恢复。

离线 Evaluation 入口为 [experiment_evaluation.ts](../02_agent_world/runtime/experiment_evaluation.ts)，参数为实验目录。它检查磁盘产物哈希并用已保存响应重放，核对实际上下文、事件链、World、P/M/X 与经历。`readExperiment` 返回规范化的请求/结果/观察/互动行；`filterRows` 支持 agent、interaction、round、run、condition，`basicStatistics` 只计算基础计数。它没有反向写入 World/Agent 的接口。重放不是再次调用 LLM；统计不包含事后拟定的规范形成阈值、真人效度或机制因果结论。

真实 API 配置：在运行终端设置 `VS_LLM_ENDPOINT`（完整 HTTPS chat/completions 地址）、`VS_LLM_MODEL`、`VS_LLM_API_KEY`；不要把 key 写进 JSON、日志或提交代码。API 示例中的 `backend.api.input_usd_per_million` / `output_usd_per_million` 必须按所选模型真实计费填写，确认资料允许外发后设置 `backend.allow_remote_persona=true`。`budget.max_calls/max_tokens/max_usd` 是全实验共享上限，每次调用保守预留、不因失败或 usage 缺失退款；无隐式网络重试或离线降级。输入 token 采用 UTF-8 字节加余量的保守估计，不是供应商精确 tokenizer；金额保护依赖正确费率和供应商计费方式，仍建议配合平台余额/配额限制。API 地址/模型/费用未配置时显式失败，本轮没有真实 API 调用。

正式研究仍未落实：独立真人行为 holdout、正式专业层及其知识/操作化依据、完整个人层消融说明、社会惯例/规范形成/机制效应测量、真人复测、多数类/统计模型 baseline、MCC/平衡准确率/置信区间，以及正式模型/提示词稳健性实验。Concordia adapter 也仍未接入。工程运行或 API 连通性 smoke 均不能补足这些要求。

## 12. 最小行为链的日志与实验接口审计

目标链为 `O → X → A_request → A_result → World effect → F → X' → A'`。这首先是可重建的时间／执行链，记录齐全也不自动构成因果识别；同调用生成的 X 报告与 action 适用第 2.3 节的独立机制证据门槛。以下保留 v1.2.0 的接口静态梳理与用户提供的 Context Builder 状态，本轮不执行代码或检查器；“有记录路径”不等于“稳定记录已运行验证”。I-A 准备离线回读能力，I-C 才在相应运行轨迹上验收整条链的重建。

| 最小链要素 | 当前记录／接口依据 | 缺口与 Phase I 验收要求 |
| --- | --- | --- |
| 当前合法 O | `observation_delivered`，recipient、visibility、observation_id、round/world_version；Dispatcher 对已提交 WorldEvent 做交付过滤 | 验证合法新事件不漏交、不跨 Agent 泄露；世界发生时间和收到时间不能混同 |
| 模型实际读取的 O 与经历 | `decision_input`、`context_budget_check`、PreparedDecision、`api_request` messages/hash；`decision_context_access` 记录有效决策暴露 | Builder 为 implemented / not runtime-validated；必须区分“交付到本地”“计划发给模型”“有 API 尝试”“有效决策”，不得都叫模型已知／相信 |
| P/M/X 版本 | 初始化／终态快照与哈希、`state_transition` 前后 X、请求中的 basis_state/cognition/observation；Builder 的状态与视图哈希 | P/M 目前以冻结内容／哈希识别，不宣称已有全部独立版本字段；验证映射到具体决策的版本和授权视图，不能仅核终态 |
| condition / manipulation | 冻结配置、experiment_id、seed、repetition；Python `evaluation.py` 当前把 experiment_id 映射为 experiment_condition | 这是标签，不是完整操纵接口。未来须补 condition_id、分配单元、操纵版本、计划暴露与实际交付／读取及偏离；首个帮助实验须区分 scripted experimental manipulation / autonomous Agent decision 并记录目标决策时状态匹配，不自动认定现有配置已支持首个帮助或后续社会信息条件 |
| judgment / expectation / intention | `cognition_update`、CognitiveUpdater 的 `state_transition`、task_id／history／attempts；记录含不确定性与来源 | judgment/intention 是报告；expectation 尚无独立专门测量。校验引用和状态轨迹，不把文本当机制成立证据 |
| A_request | `action_request`，request_id、actor、round/phase、basis 引用及 task_id | 关联实际输入／输出；重复请求、失败和多步骤 PLAN 子行动须保留，不能用请求频数替代执行 |
| GM interpretation | 当前 SceneAdapter／有限 OPEN 规则；成功结果 effects 的 grounded action／解释信息及拒绝原因 | 并非所有成功／失败路径均有独立统一解释记录；需核对 OPEN 原意→解释→前置条件→结果，缺失路径另补审计而不是默认动作成功 |
| A_result | `action_result` accepted/rejected、原因、effects、possible_next_steps；PLAN 的逐步结果 | 区分格式错误、资源／权限失败、自主 WAIT、部分完成及阶段取消；不能把 GM 拒绝当居民规范制裁 |
| objective World effect | `world_transaction` before/after、results、system_effects，`world_event`／world_sequence，环境边界事件 | 验证请求、结算顺序、环境效果与客观变化一致；现有 Python 回读核哈希／终态不等于完整重新执行所有 World 规则 |
| recipient-specific F | 下一条 `observation_delivered.content.action_result` 和获准事件；接收者 ID | 验证反馈确已交付和何时进入决策，不能只凭完整研究者日志推断所有居民都看过 |
| X' → A' | 下次 state_transition、decision_input／context access、action_request 的 actor／round/phase／basis 引用 | 可按时序和 ID 关联，但尚无经过运行验收的完整 next-decision link 表；停止／无下一次决策须明确为截尾，不能补造 A' |

现有 Python Evaluation 提供 requests/results/observations/world_events/cognition/state_transitions 等只读抽取及一致性检查路径，但没有把新增 Context 审计、独立操纵分配、全部 GM 解释和 next-decision links 统一成已验证的行为链数据集。此缺口是未来验收／回读任务，不在本轮修改代码。原始日志继续保留，不为适配新指标重写历史记录。

实验控制中的 memory policy 必须记录 adapter/options、代码版本、检索候选和选择、预算移除项、最终 messages 与实际 usage。比较 fuller history 等条件时，若预算使实际视图退化为短历史，应报告操纵偏离或按预先规则停止，不能继续把该 run 标成已成功接受“完整历史”操纵。未交付或未授权的内容不因换策略成为可检索记忆；Consolidator 仍关闭。

## 13. 实施清单、历史定位与下一步

| 分类 | 当前清单 | 后续边界 |
| --- | --- | --- |
| 现有功能／资产 | 已转换的 12 人、授权视图、来源审计；单一 Python Runner、社区生活与结算规则、API 预算／日志；已有社区历史运行 | 不重做 Persona、不扩展来源或外发授权；旧运行结论只适用于各自版本 |
| 已实现但未运行验证 | Context Builder、MemoryAccessPolicy 默认 bounded-relevant、事件／反馈去重、分项预算与 PreparedDecision；当前认知框架完整生命周期 | 放入 Phase I 验收，不能称已解决长上下文或新认知机制已通过；不要把它们重新列为从头开发 |
| 需要修复／补齐 | 经授权后的离线验收与只读行为链回读、GM 解释覆盖／next-decision 关联、独立条件／实际暴露记录；由验收发现的问题 | 先补证据再定位修复，不为得到合作而强制行动；不由“尚未验证”推断一定有 bug |
| 已冻结设计、尚未实现 | 实验优先级及第 2.2.1 节首个 Prior Social Behavior → Subsequent Behavior 设计 | Designed / Frozen in Spec；C1/C2 状态匹配与 B resource confound 必须解决；Phase I 全部范围门槛通过后才可正式运行 |
| 未来新增 | behavioral phenomenon catalogue 与测量实现、后续社会信息实验、逐项 Persona／memory／信息干预、纵向互动及结构／群体研究 | 后续候选另行冻结操作化和统计设计；no/recency/relevance/fuller-history 均非已完成条件 |
| 暂不做 | M consolidation、RL、合成人口／全量扩容、大规模社会／政策预测、完整生成式 GM、异步小镇地图及日历 | 另行提出问题和授权；不作为本轮研究设计重构的顺带开发 |

旧描述的保留规则：

1. Evidence / P / M / X / World、L1–L8、目标隔离、权限、缺失、时间与来源合同继续有效；未发现新定位要求改 Data Contract 的实质冲突，因此该文件保持原字节。
2. S12 A/B、transport 2×2、API smoke 继续作为历史 architecture validation／工程线路，不是正式规范机制实验。历史产物、原目录、配置快照、清单及哈希不修改。
3. 既有社区运行保留为历史探索和工程诊断。公开表达合作而未实际参与是候选现象，不能归结为唯一原因，也不能把修补前后独立 run 当受控干预。
4. 原 RQ1–RQ4 和 2×2 研究逻辑保留，但真人拟真部分延后；Phase II 首个帮助实验与 Phase III 后续社会信息实验的 C0–C2 各有独立语义，不混用彼此或历史条件名与结论。
5. N1–N6 是六类机制假设，非已具备模块。三个研究模式、完整异步调度、生成式 GM、跨场景无需改核心的承诺和长期稳定性均尚无本轮运行验证。

实验优先级与首个实验设计现为 **Designed / Frozen in Spec**；本轮不开始 Phase I-A。下一步另行获准后的首项代码工作仅为 **Phase I-A：offline validation + read-only behavioral trace readback**，入口及验收范围为第 2.1.1 节与第 12 节：先对现有 Context Builder／PreparedDecision、授权与必需交付、去重／预算和行为链引用补齐离线检查及只读回读证据。不是完成整个 Phase I，也不是实现首个科学实验、扩展社区、要求合作、重写 Context Builder 或开启学习；I-B／I-C 的执行与证据须另行安排。何时运行测试、模拟和 API 仍由用户决定。本轮无任何新增运行结果，不增加模块。
