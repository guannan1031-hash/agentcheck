# 项目进度 — Project 014
## 最近更新
- 更新时间：2026-10-07
- 当前状态：D1 完成，进入 D2（质检引擎）
## 已完成
- [2026-10-07] 建立项目骨架（README/STATUS/AGENTS/.gitignore + src/docs/assets/deliverables）。
- [2026-10-07] 从 Project 006 复制基线资产：backend、frontend/src+dist、configs（218 场景）、prompts、demo-data、tests（基线）、scripts、规范文档、licenses。
- [2026-10-07] 初始化独立 Git（参赛仓库，不带 006 私有历史）。
- [2026-10-07] **D1 完成**：
  - 演示 Agent 跑通：uvicorn backend.app:create_app @ 127.0.0.1:8878，首页 200，API 路由确认（/api/messages、/api/conversations、/api/tickets 等 51 个路由）。
  - 质检数据集分析：benchmark 218 cases（expected_route：needs_evidence 159 / human_triage 59），scenarios 218 场景（10 域、risk_level：conditional 159 / high 59）。
  - 规则包 schema 定义：`configs/quality_rules_ecommerce.json`（5 维度 × 8 规则，确定性正则 + LLM 裁判混合）。
  - 辅助脚本：`scripts/inspect_data.py`、`scripts/analyze_distribution.py`。
## 验证
- 环境：Python 3.12.10（venv 手动创建，绕过 setup_windows.ps1 编码问题）、依赖安装完成。
- 演示 Agent：HTTP 200 响应正常，服务保持后台运行（task d15753eb）。
- 数据文件：benchmark/scenarios 均可解析，218+218 条完整。
## 已知问题
- `setup_windows.ps1`/`start_windows.ps1` 为无 BOM UTF-8，PowerShell 5.1 按 ANSI 解析会失败；已用手动 venv + uvicorn 直跑绕过，后续启动沿用此方式或转 UTF-8 BOM。
- Jev API 可用性未验证（D2 探测，降级豆包/DeepSeek）。
- 演示 Agent 依赖后端数据为合成/脱敏，公开仓库时需保持。
## 下一步
- D2：质检引擎三通道（规则层扩展 safety.py、裁判接口 + Jev 探测、LLM 深析复用 model.py），输出结构化判定。
- D2：质检样本 schema（输入对话 + Agent 输出 + 路由决策 → 报告 JSON）。
## 知识库沉淀
- 决策：暂无
- 踩坑：PowerShell 5.1 读取无 BOM UTF-8 .ps1 中文乱码致解析失败（2026-10-07，014）
- 方法：暂无
## Notion 同步记录
- 待确认是否同步 014 到 Notion 项目库。
