# AgentCheck · Agent 输出质检工具

> 跨平台 Agent 输出的统一质量门禁 —— 毫秒级全量质检 · 可插拔规则包 · 证据可解释

AgentCheck 是一个**引擎通用、规则可插拔、裁判可替换**的 Agent 输出质检工具。对任何 Agent 的每条输出：

1. **规则层**（确定性正则）：路由合法性 / 隐私泄露 / 合规红线 / 信息完整性 —— 毫秒级拦截、零成本、可审计；
2. **Jev 毫秒级裁判**（TypeSafe System One）：语义级快判（答非所问 / 幻觉 / 情绪），返回概率与置信度；
3. **LLM 深析兜底**（DeepSeek / 智谱）：Jev 不可用时自动降级，未配置裁判时规则层兜底，永不静默放过；
4. **置信度分级升级**：critical 违规或低置信判定 → 自动进入人工复核队列，输出证据链可整改可审计。

从"人工抽检（覆盖率 <5%）"升级为"毫秒级全量质检"：批量质检实测单条平均 **0.4ms**。

## 功能特性

| 特性 | 说明 |
| --- | --- |
| 毫秒级全量质检 | 规则层 + Jev 快判，批量单条平均 0.4ms（实测 40 条 0.4ms/条） |
| 可插拔规则包 | 引擎与规则分离：仅替换 `configs/quality_rules_*.json` 即可跨行业复用 |
| 双裁判降级链 | Jev → LLM → 规则层兜底，任何环境可运行、都有产出 |
| 置信度分级升级 | 低置信不硬判，升级人工复核，机器初筛 + 人工终审闭环 |
| 证据链可解释 | 每条判定带命中词/依据/置信度，支持整改闭环与审计 |
| 质检反馈闭环 | 检出 → 整改建议（规则增强/知识补全/提示词优化）→ 应用整改 → 重检验证 → 回归用例防复发 |
| 数据反哺 | 质检产出反哺业务：人工培训课件 / 客户画像 / 复购节奏分析（数据生产入口） |
| 统一质检 API | `/api/quality/*`：单条 / 批量 / 规则包 / 引擎状态 / 反馈闭环 / 数据反哺 |

## 技术架构

```
Agent 输出（回复 + 路由决策）
   │
   ├─ 通道① 规则层      ── 路由/隐私/合规/完整性正则（0.4ms，零成本）
   ├─ 通道② Jev 裁判    ── 语义判定，概率+置信度（System One 决策模型）
   ├─ 通道③ LLM 深析    ── 低置信升级、深挖证据（DeepSeek/智谱）
   │
   ▼
统一质检报告：维度加权得分 · 规则明细 · 证据链 · 人工复核标记
```

核心模块：

- `backend/quality_rules.py` — 规则层：加载规则包 JSON，执行确定性规则（复用 `safety.py` 正则体系）
- `backend/quality_judges.py` — 裁判层：`JevJudge`（System One）/ `LLMJudge`（DeepSeek/智谱）接口化，自动降级
- `backend/quality_engine.py` — 主流程：三通道聚合 → 维度得分 → 总体结论 → 人工复核升级
- `backend/quality_api.py` — FastAPI：`/check` `/batch` `/packages` `/health` `/feedback/*`
- `backend/quality_feedback.py` — 反馈闭环：整改建议生成 / 真实写库应用 / 重检验证 / 回归用例 / 留痕
- `backend/quality_insights.py` — 数据反哺：培训课件 / 客户画像 / 复购节奏
- `frontend/dist/quality-panel.html` — 质检工作台（报告渲染 / 批量仪表 / 规则包切换 / 闭环整改 / 数据反哺）

## 规则包（可插拔）

| 规则包 | 文件 | 覆盖 |
| --- | --- | --- |
| 电商客服 | `configs/quality_rules_ecommerce.json` | 路由/隐私/合规/语义/完整性，基于 218 业务场景基准 |
| 汽车金融 | `configs/quality_rules_automotive_finance.json` | 承诺边界、虚构事实、路由合规（还款/结清/解抵押场景） |

扩展新行业：新建 `quality_rules_<industry>.json`，按 `dimensions + rules` 结构定义即可，引擎零改动。

## 快速开始

环境要求：Python 3.12（Windows / macOS / Linux）。

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.lock.txt

# 2. 启动服务（内置已构建前端）
python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8878 --workers 1

# 3. 打开质检工作台
#    浏览器访问 http://127.0.0.1:8878/quality-panel.html
```

### 配置裁判后端（可选）

| 环境变量 | 作用 |
| --- | --- |
| `QS_JEV_API_KEY` | TypeSafe System One（Jev）毫秒级裁判；未配置自动降级 LLM |
| `QS_JEV_ENDPOINT` / `QS_JEV_MODEL` | Jev 端点（默认 `https://api.typesafe.ai/v1/systemone`）与模型（默认 `jev-latest`） |
| `CS_MODEL_API_KEY` / `CS_MODEL_PROVIDER` / `CS_MODEL_NAME` | LLM 裁判（deepseek / zhipu） |
| `QS_JUDGE_FORCE=llm` | 强制走 LLM 裁判 |

未配置任何裁判时，规则层仍完整执行（语义规则跳过并在报告中标注 degradation），引擎不静默放过。

## 质检 API

```bash
# 单条质检
curl -X POST http://127.0.0.1:8878/api/quality/check \
  -H "Content-Type: application/json" \
  -d '{"input_text":"我要投诉，食品发霉了，要求退款赔偿！","agent_output":"我们保证全额退款，电话13812345678。","agent_route":"auto","risk_level":"high","package":"ecommerce"}'

# 批量质检（benchmark 抽样，返回统计）
curl -X POST http://127.0.0.1:8878/api/quality/batch -d '{"limit":20}'

# 规则包列表 / 引擎状态
curl http://127.0.0.1:8878/api/quality/packages
curl http://127.0.0.1:8878/api/quality/health
```

## 测试

```bash
pytest tests/ -q          # 全部测试（含质检引擎 12 项 + 基线 98 项）
python scripts/run_quality_demo.py            # 端到端质检演示（合规/违规对比报告）
python scripts/verify_quality_api.py          # API 真实字节级验证
```

## 数据与合规

- 演示与基准数据全部为**合成/脱敏**（218 业务场景 + 93KB 基准对话集），不含真实客户信息；
- 密钥一律运行时读取环境变量，绝不落盘、绝不提交；
- 隐私与安全规则内建于引擎（`backend/safety.py`），检出即拦截。

## 开源治理

- 许可证：GPL-3.0（允许商用但衍生必须开源，防闭源商用；SaaS 防护可升级 AGPL-3.0，详见 `docs/LICENSE_DECISION.md`，第三方依赖许可齐备）
- 工程规范：CONTRIBUTING.md / SECURITY.md / AGENTS.md
- 部署：Docker Compose 一键启动（`compose.yaml`）

## 路线图

- 行业规则包市场（医疗 / 政务 / 出海）
- 接入 Dify / N8N / LangGraph 工作流节点，成为 Agent 链路标准质检环节
- 本地化 / 私有化部署（金融、政务数据不出域）
- 质检报告反哺提示词与规则优化，形成"检出 → 整改 → 再检"闭环

## License

GPL-3.0 License. See [LICENSE](LICENSE) for details.
