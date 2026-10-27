# 智能主体的行为认知：社会规范行为模拟研究

## 2026-10-27 公共品实验

版本定位：**结果精简与诊断基线**。本版整理文件并保留可回放证据，用作后续极端选择诊断的对照起点。没有新增模型调用或行为机制改进，不能据此声称拟人性提升。

当前毕设代码位于 [实验/public_goods](实验/public_goods)，每日进展见 [研究日志](MD/毕设开发日志.md)。A 直接决策；B 先形成规范等判断再决策；C 移除显式规范判断。三组共用四人同步公共品环境。旧 `step/` 目录保留此前架构。

最新 Qwen 修复预实验位于 [结果目录](实验/public_goods/runs/live/ollama_qwen35_full_repair_20261025_001)：`report.json` 是执行摘要，`development_comparison.json` 是评分，`contribution_curves.png` 是贡献曲线，`records.zip` 保存配置、请求响应、World 和状态记录。历史真实运行收在 `runs/live/history.zip`，离线核验摘要收在 `runs/offline/checks.zip`。

压缩后，评分、审查和恢复代码仍可按原路径读取，例如 `A/trajectory.jsonl`。从 `run.py` 或实验模块命令行运行时，输出目录在执行结束后自动精简；执行中的记录仍逐条落盘。

本次每种实现只有一个四人小组，且 B 在恢复时改变引用约束。A 的整体 W1 最小，B 的 P 条件均值较接近真人但贡献只取 0/20。结果尚不能证明 B 更拟人，详见评分中的限制。

```sh
python3 -m venv 实验/.venv
实验/.venv/bin/python -m pip install -r 实验/public_goods/requirements.txt
PYTHONPATH=实验 实验/.venv/bin/python -m unittest public_goods.tests.test_world public_goods.tests.test_agent_bc public_goods.tests.test_artifacts public_goods.tests.test_ollama_recovery
PYTHONPATH=实验 实验/.venv/bin/python -m public_goods.experiments.review_trial --A 实验/public_goods/runs/live/ollama_qwen35_full_repair_20261025_001/A --B 实验/public_goods/runs/live/ollama_qwen35_full_repair_20261025_001/B --C 实验/public_goods/runs/live/ollama_qwen35_full_repair_20261025_001/C --output /tmp/qwen_comparison.json
```

公开结果只包含合成智能体行为和真人开发集汇总统计。原始真人记录、个体 Persona、划分身份清单、密钥、费用账本、虚拟环境和论文全文不上传。需要原始真人数据的测试，须从 [Dryad 来源](https://doi.org/10.5061/dryad.87301) 自行恢复数据。

## 2026-10-16 旧实现与审计

本仓库保存 `survey` 项目的代码、设计文档与 2026-10-16 审计报告。当前主实现为自有 Python Agent–World Runner，数据与初始化工具采用 TypeScript/Bun；不等同于另一个 Concordia 小镇项目。

当前已核验 WVS 来源证据层、12 个既有 Persona、上下文构建与单步运行轨迹。尚未完成受控社会规范效应实验或独立真人拟人性验证；旧运行只保留其原有工程证据含义。

## 从这里开始

- [项目审计](docs/audit/PROJECT_AUDIT.md)：逐模块状态、证据与问题。
- [本科论文适配](docs/audit/THESIS_ADAPTATION.md)：最小研究路径与范围。
- [后续计划](docs/audit/NEXT_STEPS.md)：依赖、修改点、验收标准。
- [复现指南](docs/audit/REPRODUCTION_GUIDE.md)：公开代码检查与私有研究环境恢复。
- [公开备份边界](docs/audit/PUBLICATION_SCOPE.md)：排除项及安全检查。

## 2026-10-16 旧备份的边界

公开备份不包含原始数据、受访者记录、个体 Persona、选样包、模型请求/响应、历史运行日志、来源问卷/完整字典及未完成许可审查的规则表。相关本地文件保持原位，未删除或改写。代码中的历史路径引用不代表这些数据可公开下载。

无私有资料也可执行纯合成夹具验收：

```powershell
python -X utf8 step/02_agent_world/python_runtime/validate_phase_ia.py
```

此命令不调用 API、不生成真实 Persona、不运行正式实验。其余集成测试与数据入口多数需要合法恢复的私有资料，不能把整个目录直接作为默认测试集合运行。

旧 `step/README.md` 和部分规范保留了当时状态；当前完成状态请以带日期的审计与实际证据为准。设计规范描述的长期机制不等于已实现功能。

