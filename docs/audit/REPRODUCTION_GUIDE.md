# 复现与恢复开发指南

日期：2026-10-16。公开代码验收与私有研究产物核验必须区分。所有路径相对本项目根目录；不需要访问相邻 Concordia 仓库。

## 1. 当前环境

本次实际使用 `D:\conda\envs\community-agent\python.exe`（Python 3.11.16）。日常工作先：

```powershell
conda activate community-agent
Set-Location 'D:\ATongji\4\论文\survey'
python --version
bun --version
```

Python 核心只用标准库；unittest 已够用，不要求 pytest/PyTorch/pandas。可选 `local_tokenizer_json_v1` 路径依赖 tokenizers 和已有模型匹配 tokenizer 文件，本机未安装，本次只验证其接口替身和失败关闭。不要为了运行默认 UTF-8 预算代理下载 tokenizer。

Bun 1.3.14；依赖位于 `step/00_persona/pipeline/package.json` 与 `bun.lock`。Node 22.14.0 用于 `.mjs` 审阅工具，不是 Python Runner 的服务依赖。新增 `environment.audit.yml` 是核心环境说明，并非原机器完整环境导出。

## 2. 公开仓库即可执行：纯合成验收

```powershell
python -X utf8 step/02_agent_world/python_runtime/validate_phase_ia.py
```

可选 `--report <新的JSON文件路径>` 保存结果；父目录须存在、文件必须不存在。当前验收 43/43，通过权限、必需信息保留、ID 去重、预算边界、不可变 PreparedDecision、请求/执行区分和轨迹链接。内部安装网络阻断，发送检查用内存 transport double。

该检查没有真实 Persona 加载或投影，不是模型行为实验。公开 clone 不应直接运行所有测试：部分测试需要合法恢复的本地数据/配置；`runtime.test.ts` 会投影真实样本，本次明确未运行，且未公开含真实样本断言的测试文件。

## 3. 本机 / 合法恢复私有资料后可执行的离线测试

Python regression：

```powershell
Push-Location step/02_agent_world/python_runtime
python -X utf8 -m unittest -v test_framework_refactor test_interaction_repairs test_runtime_smoke test_two_decision_readiness
Pop-Location
```

本次使用额外 socket/HTTP 阻断包装执行相同四套现有测试，38 项中 36 项通过、2 项报错。报错为 `test_framework_refactor.py` 的两个反馈 fixture 没有 `agent_id`；生产接收者检查正确拒绝，未作代码修复。两决策测试包含本地 stub-run 和既有 Persona 只读加载，不是两次真实 API 验证。

Bun 来源测试：

```powershell
Push-Location step/00_persona/pipeline
bun test ./src/wvs.test.ts ./src/wvs-audit.test.ts
Pop-Location
```

本次 81/81。使用人工审计字典/规则和合成输入；这些本地依赖未公开。

Bun 初始化入口/旧实验支持：

```powershell
bun test ./step/02_agent_world/runtime/initialize_personas.test.ts ./step/02_agent_world/runtime/experiment_runner.test.ts
```

本次 20/20：合成临时文件、既有 12 人加载、2×2 配置、独立重复、结果提取、mock API/预算，无新增真实 Persona。前一个文件包含 cohort 身份引用，留在本地，未公开。不要把裸 `bun test` 作为当前根目录默认验收命令。

## 4. 既有产物只读核验

```powershell
python -X utf8 docs/audit/verify_local_artifacts.py --output docs/audit/evidence/artifact_checks_NEW.json
```

执行前创建输出父目录，并选择不存在的输出名。此工具只读既有数据，重新计算约 1.9 GB WVS 输出及原 ZIP 哈希、逐行检查计数与 Q263 排除、调用当前人口 loader 校验 12 人包，再回读两份单步轨迹。输出只包含汇总和哈希，不包含个人回答。不运行 `run-wvs-full.ts` 或 `initialize_personas.ts`。

本次输出见本地 `evidence/artifact_checks_20261016.json`；所有核验通过。两份单步真实运行各 23 项检查、26 个事件；没有第二次模型输入。历史 two-decision readiness 为预检失败、0 calls。历史单步快照中已有的 Python 文件与对应当前文件哈希一致，但不代表新增两决策入口完成真实验收。

不要在冻结 WVS 输出目录运行旧 `verify-wvs-full.ts`：它以 `wx` 写回 `readback_verification.json`，已有文件时会失败。本次工具把新验证结果写到 audit，保护原始产物。

## 5. 主要入口与副作用

| 入口 | 用途 / 运行边界 |
| --- | --- |
| `step/00_persona/pipeline/src/run-wvs-full.ts` | 原始 WVS → 全量标准化/Evidence，写新 derived；本次不运行。 |
| `step/02_agent_world/runtime/initialize_personas.ts` | Evidence → Persona 初始化，支持 all/selection；本次不运行。 |
| `step/02_agent_world/runtime/experiment_runner.ts` | 旧 TS 协调任务的 offline/LLM 工程实验；不能代替当前 Python 或论文实验。 |
| `step/02_agent_world/python_runtime/run_community.py --config ...` | 真实 API 入口；显式执行即可能调用模型，无离线 fallback。本次未执行。 |
| `run_runtime_smoke.py` / `run_two_decision_readiness.py`（同目录） | 最小真实模型工程验收；必须另行确认调用及远端 Persona 使用权限，先查配置哈希/价格/预算。 |
| `python_runtime/trace_readback.py:read_trace` | 只读一般 Python 事件链；TS hash 格式不同，不可混读。 |
| `python_runtime/evaluation.py:read_experiment` | 场景无关的结果/状态/交付一致性读取。 |
| `python_runtime/evaluate_community.py` | 既有社区批次的专用 CLI；先确认其版本与产物匹配。 |
| `step/03_experiment/analysis/validate_phase_ic.py` | 单步 I-B 专用回读，输出必须为源目录之外的新目录；不是任意任务评价器。 |

`VS_LLM_ENDPOINT`、`VS_LLM_MODEL`、`VS_LLM_API_KEY` 仅作为当前进程环境变量读取；不写入 `.env`、配置或 Git。缺环境/预算/授权应在预检停止，不能用 stub 假装真实运行。历史价格不是当前报价；获准运行前重新核对，不能直接复制旧成本承诺。

## 6. 换机恢复

1. 克隆审计分支，核对最终反馈中的 Commit SHA。新机器可按 `environment.audit.yml` 新建 community-agent；本机现有环境无需重建。安装与锁文件相符的 Bun，执行 pipeline 目录内 `bun install --frozen-lockfile` 仅安装依赖，不运行数据生成入口。

   仓库 `.gitattributes` 关闭自动文本换行转换，以保留原文件字节及历史 hash；不要让编辑器在恢复时批量重写换行或编码。此设置不把 UTF-8 文本改为其他编码。
2. 先运行第 2 节纯合成验收。公开备份包含源代码、schemas、设计文档、脱敏配置示例与本次审计，不包含完整实验数据。
3. 通过原有合法获得方式恢复原始来源及私有 `working/`、`rules/`、`derived/`、`vertical_slice/selection_v1/`、`03_experiment/configs/` 和 `persona_initializations/`。来源许可按资料分别核查，不从公共 Git 获取受访者数据。保留历史目录名和 hashes；模型输入/响应与研究日志另行受控保存。
4. Python loader 使用配置相对路径和严格 manifest hash，要求已有 12 人包。旧 TS `source.ts` 还依赖冻结选样 manifest 的绝对路径；无法保持原盘符/布局时，应另作位置解析修订并验收，不能直接改历史清单再宣称原始哈希不变。
5. 用第 4 节只读核验和适用的离线测试确认恢复；保留原始失败，不覆盖产物。没有私有资料时只能恢复代码开发与合成验收，不能宣称重现了真人来源集成运行。
6. 需要真实调用或正式实验时，重新核对明确授权、数据远端处理边界、模型/价格与费用上限。公开 example 的 remote flag 为 false、价格和预算为 null，故意不能直接发起调用。

## 7. 本次证据及局限

- `phase_ia_20261016.json`：43/43；`python_regression_20261016.json`：36/38；两个 Bun 文本：81/81、20/20。
- `artifact_checks_20261016.json`：WVS、12 人加载与两份单步轨迹复核；含当前 Python/TS 源文件 hashes。
- `local_inventory_security_20261016.json`：748 个文本的脱敏凭据模式扫描结果、目录体积统计，非人工隐私证明。
- `public_export_phase_ia_20261016.json`：从公开暂存文件集单独导出后，43/43 合成验收通过，不依赖本机私有数据。
- 以上详细证据留在本地 `docs/audit/evidence/`；公开报告只汇总已检查结果。全部本次模型 API 调用数为 0，正式实验运行数为 0。
