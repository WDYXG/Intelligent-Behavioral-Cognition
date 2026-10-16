# Vertical Slice 第 1 步：12 人覆盖式选择方案

版本：1.0.0-draft｜日期：2026-09-18｜状态：待用户确认选择规则，尚未执行选人。

沿用已确定的 [Architecture Spec v1](../Architecture_Spec_v1.md)、[Evidence Routing v1](../../00_persona/rules/Evidence_Routing_v1.md) 和 [WVS 字段目录](../../00_persona/rules/WVS_Evidence_Routing_v1.md)，不改状态边界、不启用新路由。

## 1. 选择目标与本轮交付

选择 12 名真实、不同的受访者，组成“架构测试样本”，不是人口代表样本，也不是正式拟人性验证样本。每人一个主要测试目的，可以同时覆盖多个边界。

本轮仅给出选择规则和 [selection manifest JSON Schema](selection_manifest_v1.schema.json)。确认之后才扫描候选池、计算覆盖画像、选出真实 ID 并生成 manifest；再交用户确认，不自动进入 ExperimentRolePlan。

不随机抽样、不拼接人物、不修改回答、不用 LLM 总结或挑选，不依据“看起来守规矩/容易违规”的态度方向挑人。12 人后续作为 development/architecture_test cohort，不声称独立 holdout。

## 2. 固定数据入口

只使用 `WVS7_CHN_2018 / 6-0-0 (2024-04-30)`，正式运行目录为：

`step/00_persona/derived/WVS7_CHN_2018/WVS_full_2026-09-17T09-33-15-228Z/`

| 文件 | 本阶段用途 |
| --- | --- |
| standardized_records.jsonl | 按来源内 ID 建立候选池；读取已审计状态、原码、证据引用 |
| consistency_flags.jsonl | 继承现有描述性一致性标记，不重新判定回答对错 |
| manifest.json / quality_report.json | 锁定正式版本、已有计数和限制 |
| final_field_status.json | 继承排除、未测量、hold 和编码解释；不重新审计 |
| WVS_Evidence_Routing_v1.json | 计算候选路由有值数，不视为启用授权 |
| evidence.jsonl | 选人后回读对应证据，核实来源/时间/缺失/限制，生成核验引用 |

执行时校验实际文件 SHA256 与声明清单；最终 manifest 记录输入文件和规则文件的实际哈希。候选应具有唯一来源 ID、290 个非排除字段及可解析引用。发现重复 ID、缺记录、未知状态或版本不一致时显式阻断/报告，不悄悄换人。

**不作为排除依据：** 缺失较多、暂缓字段、有描述性矛盾标记、值不符合直觉。所有这些恰恰可能是测试目的。Q263 不读取、不用来选择人。

## 3. 先定义覆盖指标，避免把“字段数”当成人格丰富度

### 3.1 固定字段集合

- `F_P`：目录建议 P_INIT 的 13 项。
- `F_M`：目录建议 M0_CANDIDATE 的 137 项。
- `F_X`：目录建议 X0_INIT 的 11 项。
- `F_select`：290 个保留字段，减去 44 个本来源未测量字段，再减去 31 个 VALIDATION_TARGET 候选，当前共 215 项。

以上只是选样统计集合，不是本实验允许输入集合。来源 hold 或 Evidence-only 字段即使有值，也不能因此投影。

对集合 F 定义：

```text
valid_count(F)   = status == source_category 且与来源 code_audit 一致的字段数
missing_count(F)= status == source_missing 的字段数
valid_fraction = valid_count / |F|
missing_by_reason = 按既有 missing_reason 分组计数
```

对正式数据，预期 valid_count + missing_count = |F|；出现 empty/unrecognized_code/field_absent 等状态须报告异常，不能混进正常缺失。

“有值”包括合法的“不知道/难说”等回答类别，只表示编码可解释，不表示有确定信念。`Q284=-5` 可有值，`Q274=0` 可有值；不得用正负数或真假值判断缺失。44 个未测量项不计入个人缺失排名，避免所有人被共同的问卷设计缺失淹没。

**保留目标隔离：** 选择器不读取 31 个 VALIDATION_TARGET 候选的答案或其缺失画像来排名，也不以其派生指标选人。后续目标尚未定义，故不能声称完整 target closure 已完成；若新目标与选样依据重合，必须披露选择依赖。这批人仍是开发样本，不能升级为“从未接触过的测试样本”。

### 3.2 相对高低的精确定义

对全部通过结构校验的候选计算每项 valid_count，按数值升序排列。使用 nearest-rank 分位数：`q(p)=sorted[ceil(p*N)-1]`。

- 低值候选：count ≤ q(0.20)。P/M/X 稀疏槽位还要求 count < 对应分母，确保确实包含缺失。
- 高值候选：count ≥ q(0.80)。不称“全部完整”，除非 count 等于分母。
- 分位点相等或维度无差异时保留实值并报告；不强行分组，不人为制造缺失。

## 4. 12 个主要测试槽位

以下是角色名，不是 12 个已选受访者。每个槽位由不同真人承担；同一个人命中其他边界时记录 secondary_tags，但不能重复凑足人数。

| 槽位 | 主要目的 | 明确候选规则 | 后续应检查什么 |
| --- | --- | --- | --- |
| S01 | 高可用度参照 | valid_count(F_select) ≥ q80；优先 P/M/X 最低有值比例较高者 | 正常路径；“有值很多”不触发全量输入 |
| S02 | 整体缺失较多 | valid_count(F_select) ≤ q20，且 missing_count(F_select)>0 | unknown 不被补齐；数据少也能完成运行 |
| S03 | P 候选稀疏 | valid_count(F_P) ≤ q20，且 <13 | 缺背景信息不由常识补出 |
| S04 | M0 候选稀疏 | valid_count(F_M) ≤ q20，且 <137 | 不伪造信念或长期经验；可空的 M0 不造成崩溃 |
| S05 | X0 候选稀疏 | valid_count(F_X) ≤ q20，且 <11 | 初始 unknown 与后续环境观察分开 |
| S06 | 合法负编码 | Q284.status=source_category 且 Q284.value=-5 | 保留有效“都不是”，不误判缺失；不自动启用 Q284 |
| S07 | top-code | Q274.status=source_category 且 Q274.value=7 | 至少 7 而非精确 7；是否输入仍由后续最小路由决定 |
| S08 | 当前职业条件分支 | Q279 为有效 1/2/3，Q281 为有效 1–10 | 当前工作上下文可判定；不与过去职业混淆 |
| S09 | 过去职业条件分支 | Q279 为有效 4/5/6/7，Q281 为有效 1–10 | 保存上一份工作语义，不改写成当前职业 |
| S10 | 条件未知分支 | Q279.status=source_missing，Q281 为有效 1–10 | 不能仅凭有职业码补出当前/过去工作状态 |
| S11 | 跨题一致性标记 | 既有 flags 含 partner_employed_occupation_zero | 原回答与标记共存，不自动修正或创建配偶 Agent |
| S12 | 时间语义边界 | Q286.status=source_category | observation_year=2018 与 reference_period=2017 分离；不把去年收支变成当前余额 |

已核对的边际计数：Q274=7 有 2 人；Q284=-5 有 1,015 人；Q279 缺失有 175 人；现有报告含配偶就业/职业零码标记 66 人、出生年加年龄与 2018 不同标记 32 人。**这些是来源审计/质量报告的计数，不是本轮联合筛选结果。** S08–S10 等联合条件及槽位之间是否有不同人可分配，须执行时确认，不能据边际数保证可行。

### 4.1 尽量由上述 12 人兼任的附加覆盖

在满足主要槽位的前提下，优先覆盖以下标签；每个标签只算一次，不能用同类标记数量冲高覆盖分：

1. `valid_zero_children`：Q274 有效 0，与 top-code 构成边界对照。
2. `valid_unsure_category`：Q33 有效 3 或 Q119 有效 0；“不知道/难说”仍是有效回答，不当成同名缺失。
3. `missing_no_answer`：F_select 中出现来源缺失 no_answer。
4. `missing_dont_know`：F_select 中出现来源缺失 dont_know，与有效“不知道”区别。
5. `missing_not_applicable`：F_select 中出现来源缺失 not_applicable。
6. `age_year_discrepancy`：继承 birth_year_plus_age_differs_from_2018_not_necessarily_error；不得称为确定错误。
7. `P_rich` / `M_rich` / `X_rich`：对应 count ≥ q80。与 S03–S05 对照，检查三层候选有值数的跨度。

用于计分的唯一标签清单为：

`valid_zero_children, valid_unsure_category, missing_no_answer, missing_dont_know, missing_not_applicable, age_year_discrepancy, P_rich, M_rich, X_rich`，**共 9 个**。

所有受访者共同涉及的“44 项未测量、14 项 hold、Q290 粒度限制、Q263 已排除”作为全样本守卫检查，不浪费一个专门名额，也不参与区分个人的覆盖分。

### 4.2 证据覆盖不等于运行时输入覆盖

S06、S08–S12 的部分字段当前是 Evidence-only。测试可以证明它们被正确保留、阻止误投影，同时该受访者用其他获准输入完成生命周期；不要求为了覆盖而强行激活这些字段。

后续 ExperimentRolePlan 确定后，应另算实际启用字段的有效/缺失覆盖。若 12 人都在那个极小输入集合上无缺失，应报告 runtime unknown 覆盖缺口，提请定点换人或调整测试方案；不能用本阶段“大集合上有缺失”冒充运行输入也有 unknown，更不能人为把真人值改成缺失。

## 5. 可复现的选择算法

采用确定性的“12 槽位—受访者匹配”，而不是贪心先占一个稀有个体后导致其他槽位无解。

1. 校验输入，构建只包含允许选择字段/既有非目标标记的候选画像；固定分位点和候选资格。
2. 建立二分关系：若某受访者满足某槽位规则，则连边。先检查是否存在覆盖全部 12 槽位、且受访者不重复的完整匹配。
3. 在所有可行匹配中，按以下目标逐级优化；前一级最优后固定其值，再优化下一级，不混成任意权重总分：
   - 最大化 9 个附加标签的不同命中数。
   - 最大化 S01 的 min(P_fraction,M_fraction,X_fraction)，再最大化 S01 的 F_select 有值数。
   - 最小化 S02 的 F_select 有值数；依次最小化 S03 的 P、S04 的 M、S05 的 X 有值数。
   - 仍并列时，以 S01→S12 的来源 ID 向量按 UTF-8 字符串字典序取最小；ID 保持字符串，不能数值化丢失前导零。
4. 可用约束求解/精确匹配实现，记录算法和工具版本；不使用随机启发式返回未说明的任意解。超时或未证实上述确定性选择条件时标记 selection_incomplete，不假称完整完成。
5. 回读选中 12 人的对应 Evidence：校验证据引用、值/缺失与快照一致，并核查时间边界。回读失败不静默换人，应暂停并报告。
6. 输出 manifest 和覆盖报告。报告包含每个槽位候选人数、联合匹配可行性、附加覆盖命中/未命中、重复 ID 检查和来源核验。

**如果不可行：** 输出空候选槽位或冲突槽位组合及人数，不自行放宽。建议替代只能提交用户确认，例如 S10 原条件无候选时，可提议改为“Q279=8 且有职业记录”的上下文未明确分支；两者语义不同，不能暗中互换。12 是目标人数，不为凑数复制人或合成人。

## 6. selection manifest 的逻辑结构

正式 JSON Schema 约束的是**已经完成选样、但待确认或已确认的清单**，不是本计划文件。不创建带虚构 ID 的“样本清单示例”。

| 部分 | 关键字段与用途 |
| --- | --- |
| 顶层 | schema_version、selection_id、status、purpose、created_at、requested_n=12 |
| source | 数据集、版本、正式运行 ID、candidate_count、各输入 artifact 路径/哈希 |
| protocol | 本计划版本/哈希、selector 版本/代码哈希、分位数方法、最终阈值、9 个标签清单、求解状态/目标值/tie-break |
| boundary | architecture_test_only、不是代表样本、没有输入授权、没有未来 holdout 保证、learning_enabled=false；目标候选载荷未用于选择 |
| coverage | 各槽位候选数、最终覆盖人 ID、附加覆盖及缺口、通用守卫检查、运行输入覆盖尚待 RolePlan 检查 |
| samples[12] | slot_id、source_record_id、sample_alias、primary_test_purpose、secondary_tags、profile、selection_evidence、expected_checks、limitations |
| profile | F_select/P/M/X 的分母、有值数、缺失数、缺失原因；既有一致性标记 |
| selection_evidence | 实际 evidence_id、source_field、观测/参考时期、触发的原码/标准码/缺失或 flag、证据文件定位；仅存审查所需的最少值 |
| integrity | 不重复、不跨源、来源引用/哈希核验、选样可重现、未自动修正/插补；选择结果语义校验摘要 |

`sample_alias` 为 VS01–VS12，和 S01–S12 对应，不是真名。包含来源 ID、缺失或矛盾标记的 manifest 是受限审计文件，不能整体送给 Decision、GM 或网页公开回放。运行输入必须通过后续 RouteDecision 和最小投影单独构建。

Schema 能检查结构、枚举和 12 人数量；唯一 source ID、槽位各出现一次、计数与证据一致、9 个标签确实命中、没有目标内容、哈希匹配及确定性需要专门语义检查，不能只靠 JSON Schema 宣称通过。

## 7. 本步确认点

建议确认：**12 个主槽位 + 9 个附加标签、目标候选不参与选择、确定性全局匹配、不可行不自动放宽、选样不授予投影权限。**

确认后仅执行第 1 步选样并交付真实 manifest/覆盖报告。下一步的情景、时间对齐、输入许可、目标隔离和 action space 仍等待单独确认。
