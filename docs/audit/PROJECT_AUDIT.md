# 项目现状审计

审计日期：2026-10-16（Asia/Shanghai）。对象：本项目 `survey`，不是相邻 Concordia 项目。路径均相对项目根目录。当前代码、离线测试与历史运行产物分别核查；未修改业务代码、原始数据或既有运行产物，未调用外部模型、重新生成真实 Persona 或开展正式实验。

## 结论

项目已形成可复用的来源证据、确定性初始化和 Agent–World 工程原型。当前有 **3,036 人 / 880,440 条 WVS Evidence、12 个已初始化且可校验加载的 Persona、统一 Context Builder、分离的 ActionRequest/ActionResult、确定性 GM 和轨迹回读**。它尚未形成毕业论文需要的受控社会规范实验和独立真人行为评价闭环。

本次 182 项现有离线测试中 **180 项通过、2 项报错**。两项错误来自旧测试反馈夹具缺失 `agent_id`，不能写成全套验收通过，也不能据此断言合法 GM 结果无法运行。两份历史真实模型单步运行的回读各通过 23 项检查；现有两决策真实运行记录停在预检、调用数为 0。多轮稳定性与反馈影响下一次真实模型决策仍未完成验证。

## 1. 项目结构与技术栈

| 位置 | 当前职责 / 审计发现 |
| --- | --- |
| `step/00_persona/pipeline/src/` | TypeScript/Bun；WVS 读取、编码解释、来源标准化、Evidence 与校验。旧 `wvs.ts/run-wvs.ts` 为小样本投影路线，`wvs-audit.ts/run-wvs-full.ts` 为正式全量证据路线，不能混称全量 Persona。 |
| `step/00_persona/{raw,working,rules,derived,contracts}/` | 原始资料、人工审计、版本规则、冻结结果、JSON Schema；多数研究资产只在本地保存。 |
| `step/02_agent_world/runtime/` | TypeScript 确定性 Projector、初始化入口、早期 A/B 协调运行器、四条件工程输送和回放评价。 |
| `step/02_agent_world/python_runtime/` | 当前自有 Python Runner、Agent、Context Builder、Memory、LLM 客户端、SceneAdapter、GM、World、回读和离线测试。 |
| `step/03_experiment/` | 私有配置、12 人初始化包、TS 历史实验、Python 社区历史运行、单步/两步 readiness 记录；`analysis/validate_phase_ic.py` 为只读验证器。 |
| `step/specs/` | 当前设计入口；包含已实现接口与仍属未来研究的内容，状态文字部分滞后。 |
| `step/{01_population,04_calibration_validation,05_analysis_reporting}/` | 本次核查为空；不能凭目录存在认定有人口生成、校准或拟人性分析能力。 |
| `models/argos/`、`papers/`、根目录 PDF/HTML、`tmp/` | 翻译模型、阅读材料与临时产物，不是运行核心，也不进入公开备份。 |

实测环境：Windows / PowerShell；`community-agent` 中 Python **3.11.16**，Bun **1.3.14**，Node **22.14.0**，Git **2.51.2.windows.1**。Python 当前核心使用标准库（unittest、urllib、concurrent.futures 等），不是 PyTorch/Concordia 服务。可选本地 tokenizer 分支依赖 `tokenizers`，该环境未安装；默认 UTF-8 预算代理不需要它。未检测到本项目根级 Python 依赖说明；本次新增最小环境描述，不重建现有环境。

Bun 依赖来自 `step/00_persona/pipeline/package.json` 与 `bun.lock`：AJV 8.17.1、csv-parse 6.1.0、JSZip 3.10.1。Node 用于来源展示/审阅辅助脚本。模型请求为 OpenAI-compatible HTTPS；密钥从当前进程环境读取。

## 2. 统一状态矩阵

状态只描述表中限定能力；不自动推广到整个模块或科学有效性。

| 模块 / 能力 | 状态 | 当前文件 / 入口 | 核验依据与边界 |
| --- | --- | --- | --- |
| WVS 中国 2018 标准化与 Evidence | Implemented & Verified | `pipeline/src/wvs-audit.ts:interpretAuditedRow`、`run-wvs-full.ts`（位于 `step/00_persona/`） | 81 项来源管线测试通过；正式输出重新逐行检查 3,036 人、每人 290 字段、880,440 Evidence、3,036 flags；全部输出和原 ZIP 哈希一致。 |
| 缺失值 / 来源追踪 / 排除 | Implemented & Verified | 同上；`contracts/evidence_record_v0.1.json` | 有原码、原始位置/哈希、观察时间、缺失原因、规则/audit 版本及禁止推断；Q263 不进入标准化/Evidence。合法负码、top-code 与 unknown 有测试。 |
| 其他五来源的统一 Evidence Adapter | Partially Implemented | `raw/SRC_*/README.md`、`contracts/source_registry_fixture_v0.1.json` | 已有资料登记及不同深度的审阅；pipeline 实际仅发现 WVS 实现。没有证据表明 PRISM、Amazon、SO、JetBrains、Wikidata 均完成同等转换。 |
| 数据许可门槛 | Partially Implemented | `raw/README.md`、WVS 来源 README、registry | 有文档边界；缺少自动导出/发布许可检查器。字段输入授权不等于再分发许可。本次按来源资料的既有限制排除数据，未作新的法律授权判断。 |
| 12 人确定性初始化及许可视图 | Implemented & Verified | `runtime/projector.ts:project/personaView`、`initialize_personas.ts`、Python `population.py:load_population` | 已有 2026-09-20 的 completed 初始化包；本次只读加载 12 人，核验状态、角色计划、路由、视图与清单哈希，未重新投影。20 项所选 TS 测试通过（含合成初始化入口与既有包加载）。 |
| 全量真实来源到任意规模实验主体 | Implemented but Unverified | `runtime/initialize_personas.ts`、`persona_source.ts` | all/selection 入口存在、合成适配器测试通过；未找到 3,036 人全量 AgentState 运行证据，生产 loader 仍固定 12 人。 |
| P / L1–L8 表示与运行冻结 | Implemented & Verified | `projector.ts`、`agent.py` | 12 人均有八层容器；授权模型视图只有 12 个问卷字段加 unknown 占位。L4/L7/L8 仅有未知占位，不能写成技能、学习机制、策略已测得。 |
| M 长期更新 / Consolidator | Design Only | `specs/Architecture_Spec.md`；`agent.py` 的冻结存储 | M0 为空、`consolidated=false`，运行冻结 M；无经过验证的巩固、学习或内化机制。 |
| X 观察、判断、任务更新 | Implemented & Verified | `agent.py:deliver/accept_decision`、`cognition.py` | 单步真实轨迹及离线状态/权限测试有证据；判断与行动同调用生成，只是模型报告，不能据日志顺序推断认知因果机制。 |
| Context Builder、预算与去重 | Implemented & Verified | `context_builder.py:select_history/build_draft/ContextBuilder.prepare` | I-A 43/43；保留完整许可 P、当前观察、未暴露新交付，按 event/request ID 去重，只裁可选历史；必需内容超限停止整阶段发送。验证范围不含长时多人负载。 |
| 真实 tokenizer / 服务端预算等价 | Implemented but Unverified | `context_builder.py:TokenEstimator` | 本地路径/模型/哈希与失败关闭有替身测试；本机无 tokenizers，未验证真实 tokenizer 与服务端等价。默认计量是 UTF-8 byte reservation + 1024 allowance，不是真实 token 数。 |
| Episodic Memory 默认访问策略 | Implemented & Verified | `episodic_memory.py:BoundedRelevantPolicy` | 本 Agent 已交付历史、recency+词项相关度与预算裁剪有测试；不是向量记忆或规范学习。no-retrieval/其他比较策略只有接口，实验比较 Missing。 |
| Decision / LLM 调用与失败处理 | Implemented & Verified | `llm.py:LLMClient/CostLedger/preflight`、`runner.py:run_phase` | 冻结输入哈希、预算、HTTPS、重定向阻断、错误记录与无静默 WAIT 已实现；两份历史单调用证据。没有当前多人多轮完整真实调用验收。 |
| 确定性 GM、受限 OPEN 与 World | Implemented & Verified | `gm.py:GM.commit`、`scene_adapter.py`、`scene_community.py`、`living_world.py` | 请求/执行/拒绝/效果分开、事务拷贝、权限、幂等、可见性有测试；10/10 interaction 修订测试通过。OPEN 是受限解释器，不是任意生成式世界。 |
| 反馈进入下一次真实模型决策 | Implemented but Unverified | `scene_two_decision.py`、`run_two_decision_readiness.py` | 4/4 离线两决策测试通过；历史真实 readiness 预检失败、0 calls，不能继承单步成功为连续决策成功。 |
| 轨迹回读与工程 Evaluation | Implemented & Verified | `trace_readback.py`、`evaluation.py`、`evaluate_community.py`、`analysis/validate_phase_ic.py` | 两份历史单步轨迹各 23 checks / 26 events 重新通过；请求、结果、世界变化、反馈、X 可重建。两种 Python evaluation 入口及 TS 回放各有版本范围。 |
| 实验重复 / 条件配置 / 描述统计 | Partially Implemented | TS `experiment_config.ts/experiment_engine.ts/experiment_evaluation.ts`；Python `runner.py` | TS 有 2×2 输入开关、重复、条件汇总和保存响应回放；Python 有 repetitions/seed/experiment_id，但未见完成的规范信息条件随机调度器或正式效应统计。 |
| 正式规范信息受控比较 / 模型比较 | Design Only | `specs/Experiment_Spec.md` | 规范性/描述性信息与帮助行为设计已写；缺任务冻结配置、独立重复/随机化执行、效应量/区间分析与模型比较实验。旧四组工程输送不是这些实验。 |
| 独立真人参照 / 拟人性评价 | Missing | `04_calibration_validation/` 为空；TS evaluation 明列未实现 | 没有已接入、与当前情境/信息条件匹配的独立人类行为参照；WVS 自报及预留目标不自动成为模拟任务的真人行为答案。 |

12 人 P 每人字段槽位（包含 unknown）：L1=6、L2=19、L3=2、L4=1、L5=162、L6=14、L7=1、L8=1。此处仅作结构说明，不公开个体属性。历史回答按 2018 快照作反事实初始化，不代表其 2026 状态。12 人为开发覆盖样本，不代表总体。

## 3. 当前行为链及版本证据

`ObservationDispatcher.deliver → Agent.deliver（X 与 episodes）→ ContextBuilder.prepare → LLMClient.decide → Agent.accept_decision（X 判断/任务）→ ActionRequest → GM.commit（World transaction）→ ActionResult / WorldEvent → 定向 Feedback Observation → X 更新`。

ActionResult 与 World Effect 在一次 GM 事务中结算，并非先相信模型自报再更新世界。SAY/OPEN 中的“我已帮助”不等于资源已转移；GM 拒绝时 `executed_action=null`。阶段中部分决策失败会取消未提交请求；历史已完成阶段保留。没有持久崩溃恢复/自动续跑承诺。

本地证据索引：

| 证据 | 文件 / 目录 | 本次确认 |
| --- | --- | --- |
| E1 WVS 正式输出 | `step/00_persona/derived/WVS7_CHN_2018/WVS_full_2026-09-17T09-33-15-228Z/` | 输出三份 JSONL 数量、完整文件哈希与原 ZIP 哈希一致；来源标准化，不是 Persona 批次。 |
| E2 初始化包 | `step/03_experiment/persona_initializations/WVS7_CHN_2018/2026-09-20T11-32-22-103Z_e091968c/initialization_manifest.json` | 12 人可加载；不输出个体记录至公开仓库。 |
| E3 单步真实运行 | `step/03_experiment/runtime_validation/phase_ib_2026-10-01T15-07-43-156434+00-00_315da17a/` 及 `phase_ib_2026-10-01T15-09-17-859046+00-00_c36d284b/` | 各 1 次历史调用，均仅一次 MOVE；没有第二次模型决策。现有快照中的 Python 文件与对应当前文件哈希一致；这不覆盖后来新增文件。 |
| E4 两决策预检 | `step/03_experiment/runtime_validation/two_decision_2026-10-02T06-39-57-374223+00-00_873260d0/readiness_manifest.json` | Not Validated，`API_preflight_no_network`，0 calls。 |
| E5 旧 Python 社区运行 | `step/03_experiment/community_experiments/` | 清单有 3 份 completed 历史批次（96、192、192 calls）与失败批次；代码快照和当前版本有差异。本次只核清单与版本差异，未逐项重建全部社区日志，不能视为当前版通过。 |
| E6 旧 TS 工程运行 | `step/03_experiment/experiments/` | 两份 offline completed（各 672 次内部决策记录，非 API calls）和一份 4 calls 的 LLM completed 清单；不重新命名为正式科学实验。 |
| E7 本次离线验收 | `docs/audit/evidence/`（本地） | `phase_ia_20261016.json` 43/43；`python_regression_20261016.json` 36/38；两个 Bun 文本记录 81/81、20/20；`artifact_checks_20261016.json` 为只读复核。 |

历史 hashes 证明存储产物的内部一致性，不是独立服务商认证或科学效度。现有 `runtime.test.ts` 会在内存重新投影真实受访者，本次遵守“不重新生成 Persona”而未执行；没有把该套旧测试记入本次通过数。

## 4. 实际问题与优先级

| 编号 | 优先级 | 问题 / 影响 | 证据 / 建议 |
| --- | --- | --- | --- |
| A01 | P0（研究运行门槛） | 当前没有反馈进入下一次真实模型决策的成功证据；不能宣布基础设施完整 ready。 | E4；先完成离线修订与任务检查，再经用户单独授权最小两决策调用，不直接重跑社区。 |
| A02 | P1 | 2 项旧回归测试报错，测试资产已落后于反馈权限合同。 | `test_framework_refactor.py:171,182` 的 result 缺 `agent_id`；`agent.py:45` 正确拒绝。后续仅修夹具，保留生产校验。当前业务代码未修改。 |
| A03 | P1 | 现成规范条件、独立人类任务参照和科学统计缺失，直接阻碍论文任务 3/4。 | Evaluation 明示不提供拟人性指标；空验证目录；先按数据可得性定任务，隔离目标。 |
| A04 | P1 | 旧 Context 溢出不能因 I-A 通过就宣称整体解决。必需观察/任务仍可增长并触发停止；默认预算不是实测 token。 | 2026-09-22 `...a6b7bafb` 第一 action 阶段超限；新 Builder 离线通过，但新长时多人真实运行未验收。固定论文短任务，记录暴露量，不以裁掉处理信息解决。 |
| A05 | P1 | 实验设计文档存在不同阶段的优先级；旧帮助设计不自动等于本次论文任务。 | `Experiment_Spec.md` 的 prior-help 设计与后续规范信息设计分属不同研究问题。本次论文先按人类数据选任务，不修改历史冻结设计。 |
| A06 | P1 | 个体数据与许可资料不适合直接进公开 Git；配置甚至包含完整选样 ID 集。 | raw README、E1/E2、population config 和模型输入日志；本次白名单备份并排除完整数据/规则资料。公开仓库不等于完整私有研究备份。 |
| A07 | P1 | 换机复现依赖私有初始化包/规则/选样和若干绝对路径。 | `runtime/source.ts` 读取历史 manifest 中的绝对路径；Python config 锁定 12 人及哈希。不得为换机直接改历史 manifest；后续加独立位置解析或保持原布局。 |
| A08 | P2 | README、registry 和规范状态文字滞后；环境说明不完整。 | `step/README.md`、`00_persona/README.md` 仍称未初始化，部分 Spec 仍称未运行。新增根导航和带日期审计；未改冻结历史文件。 |
| A09 | P2 | 两套运行器及两个 Python Evaluation 入口可能引起误用，模型/条件比较尚无统一分析表。 | TS 与 Python hash/场景合同不同。按 artifact 类型选 reader；本科只补必要规范任务与分析，不合并重构全系统。 |

## 5. Git 与公开备份审计

开始时根目录及父目录不是 Git 仓库，项目内也未发现嵌套 `.git`；因此不存在可供扫描的本地提交历史、已跟踪敏感文件或未提交 diff。指定远端 `WDYXG/Intelligent-Behavioral-Cognition` 为 **Public**、用户具有 push 权限，`git ls-remote` 未返回已有引用。用户随后明确允许向该 Public 仓库仅推送经审查的代码和文档；不创建公共仓库，不改仓库可见性，不推 main/master。

748 个本地文本文件的凭据模式扫描未发现候选密钥（原始/派生数据、二进制与第三方依赖不属于该凭据扫描范围，独立排除发布）。模式扫描不证明所有文本无隐私；额外排除了个体记录、日志、选样 ID 配置、含真实样本断言的测试、来源全文与未经许可复核的规则表。公开暂存文件另作完整文件清单、大小、扩展名、内容模式及 diff 审查，详见 [PUBLICATION_SCOPE.md](PUBLICATION_SCOPE.md)。没有用 `.gitignore` 替代已跟踪内容检查，也未改写任何历史。
