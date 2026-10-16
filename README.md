# 智能主体的行为认知：社会规范行为模拟研究

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
