# Project 014-Agent质检工具
- 创建日期：2026-10-07
- 状态：进行中（代码资产已就绪，D1 待跑通演示）
- 目标：基于 Project 006 客服 Agent 的 218 场景库，跑通本地演示 Agent，提取质检数据集并定义规则包 schema。
- 验收标准：
  1. scripts\setup_windows.ps1 → start_windows.ps1 本地可跑通演示 Agent。
  2. 验证 218 场景客服 Agent 可运行。
  3. 提取质检数据集、确定规则包 schema。
- 范围与边界：
  - 从 Project 006 复制基线资产（backend/frontend/configs 218 场景库/prompts/demo-data/tests 基线/scripts/规范文档/licenses）。
  - 排除 .git/.venv/node_modules/__pycache__/数据库/内部产出。
  - 独立 Git 参赛仓库，不带 006 私有历史。
- 启动 / 构建 / 验证命令：
  - 环境准备：scripts\setup_windows.ps1
  - 本地演示：scripts\start_windows.ps1
  - 测试基线：scripts\test_windows.ps1

## 目录
backend：后端源码（app/engine/safety/model/scenario_center 等）；frontend：前端；configs：218 场景库；prompts：提示词；demo-data：演示数据；tests：测试基线；scripts：运行/构建脚本；docs：文档；assets：素材；deliverables：交付物。

## 里程碑
| 日期 | 里程碑 | 结果摘要 | 验证证据 |
| --- | --- | --- | --- |
| 2026-10-07 | 建立骨架并复制 006 基线资产 | 218 场景库、backend/frontend/tests、独立 git 就绪 | 目录与文件核验通过 |
| 2026-10-07 | D1：跑通本地演示 Agent | 待完成 | 待完成 |

## 关键链接（本地知识库）
- 知识库项目卡：knowledge/01-项目/Project 014-Agent质检工具.md
- 相关决策：待沉淀
- 相关踩坑：待沉淀
- 相关方法：待沉淀

## Notion 联动
- Notion 页面链接：待确认是否同步
- 最后同步时间：待同步
- 同步方式：待确认