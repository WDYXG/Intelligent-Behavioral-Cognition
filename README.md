# 智能主体的行为认知：社会规范行为模拟研究

## 2026-10-25 公共品实验代码备份

本分支新增独立重写的本科毕设实验。入口位于 [实验/public_goods](实验/public_goods)，每日进展见 [研究日志](MD/毕设开发日志.md)。旧 `step/` 目录和下方审计说明保留 2026-10-16 状态。

- A 直接决策；B 先形成规范等判断再决策；C 移除显式规范判断。三组共用四人同步公共品环境。
- 当前候选 v5 增加支付频率、完整贡献历史核对和旧 X 对照。完整预实验及小规模诊断尚未证明 B 更拟人。
- 本次只备份代码、测试、自写笔记和数据来源元信息。原始数据、数据划分文件、密钥、模型请求/响应、费用账本、运行记录及论文全文保留在本地。日志中的部分本地报告链接在公开仓库不可用。

安装依赖并运行无需真人数据或 API 的合成测试（macOS/Linux）：

```sh
python3 -m venv 实验/.venv
实验/.venv/bin/python -m pip install -r 实验/public_goods/requirements.txt
PYTHONPATH=实验 实验/.venv/bin/python -m unittest public_goods.tests.test_world public_goods.tests.test_agent_state public_goods.tests.test_agent_a public_goods.tests.test_agent_bc public_goods.tests.test_capabilities public_goods.tests.test_memory_diagnostic
```

真人参照和完整测试另需从 [Dryad 原始来源](https://doi.org/10.5061/dryad.87301) 合法恢复数据，并按 [来源记录](数据核验/Herrmann2008/来源记录.json) 中的相对位置放置文件。历史诊断还需要本地原始运行记录。模型密钥由使用者在本地配置；运行真实实验前先查看调用量和预算计划。

## 2026-10-16 旧实现与审计

本仓库保存 `survey` 项目的代码、设计文档与 2026-10-16 审计报告。当前主实现为自有 Python Agent–World Runner，数据与初始化工具采用 TypeScript/Bun；不等同于另一个 Concordia 小镇项目。

当前已核验 WVS 来源证据层、12 个既有 Persona、上下文构建与单步运行轨迹。尚未完成受控社会规范效应实验或独立真人拟人性验证；旧运行只保留其原有工程证据含义。

## 从这里开始

- [项目审计](docs/audit/PROJECT_AUDIT.md)：逐模块状态、证据与问题。
- [本科论文适配](docs/audit/THESIS_ADAPTATION.md)：最小研究路径与范围。
- [后续计划](docs/audit/NEXT_STEPS.md)：依赖、修改点、验收标准。
- [复现指南](docs/audit/REPRODUCTION_GUIDE.md)：公开代码检查与私有研究环境恢复。
- [公开备份边界](docs/audit/PUBLICATION_SCOPE.md)：排除项及安全检查。

## 公开仓库的边界

公开备份不包含原始数据、受访者记录、个体 Persona、选样包、模型请求/响应、历史运行日志、来源问卷/完整字典及未完成许可审查的规则表。相关本地文件保持原位，未删除或改写。代码中的历史路径引用不代表这些数据可公开下载。

无私有资料也可执行纯合成夹具验收：

```powershell
python -X utf8 step/02_agent_world/python_runtime/validate_phase_ia.py
```

此命令不调用 API、不生成真实 Persona、不运行正式实验。其余集成测试与数据入口多数需要合法恢复的私有资料，不能把整个目录直接作为默认测试集合运行。

旧 `step/README.md` 和部分规范保留了当时状态；当前完成状态请以带日期的审计与实际证据为准。设计规范描述的长期机制不等于已实现功能。

