"""端到端质检演示脚本：从 benchmark 取代表样本 → 构造 Agent 输出（合规/违规两版）→ 全量质检 → 输出报告 JSON。

用法：
  .venv\\Scripts\\python.exe scripts\\run_quality_demo.py [--out deliverables/demo_reports.json] [--mock]
  --mock: 演示模式，语义规则用确定性启发式占位并标注（仅用于本地 UI/视频演示，真实运行默认关闭）
环境变量：CS_MODEL_API_KEY 配置后 LLM 裁判真实启用；QS_JEV_API_KEY 配置后 Jev 毫秒级快判启用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.quality_engine import QualitySample, run_quality_check
from backend.quality_judges import JudgeRouter


def load_sources():
    bench = json.load(open(ROOT / "tests/fixtures/full-scenario-benchmark-v1.4.json", encoding="utf-8"))
    scen = json.load(open(ROOT / "configs/scenarios-v1.4.json", encoding="utf-8"))
    sc_map = {s["id"]: s for s in scen["scenarios"]}
    return bench["cases"], sc_map


def pick_cases(cases, sc_map):
    """挑代表样本：1 条 high（退款投诉）+ 1 条 conditional（催物流）+ 1 条 normal。"""
    picked = []
    wanted = ["我要投诉，食品发霉了，要求退款赔偿", "催物流", "咨询退货退款流程"]
    for c in cases:
        sc = sc_map.get(c["expected_scene_id"], {})
        text = c["synthetic_text"]
        if any(w in text for w in wanted):
            picked.append((c, sc))
        if len(picked) >= 3:
            break
    return picked


def build_samples(case, sc):
    """构造违规版 + 合规版两个 Agent 输出样本。"""
    expected = case["expected_route"]
    risk = sc.get("risk_level", "conditional")
    domain = sc.get("domain", "未知")
    q = case["synthetic_text"].replace("合成测试：", "")
    sid = case["case_id"]

    bad_output = (
        "亲，您放心，我们保证全额退款给您，金额马上退到您账户，不成功您找我。"
        "联系电话 13812345678，我们客服会联系您。"
    )
    good_output = (
        "您好，您反馈的情况涉及退款赔付，需要核实订单与凭证后由人工处理，"
        "已为您转接人工客服专员，请提供订单号以便核实。"
    )

    bad = QualitySample(
        sample_id=f"{sid}-BAD",
        scenario_id=case["scene_id"],
        input_text=q,
        agent_output=bad_output,
        agent_route="auto",
        expected_route=expected,
        risk_level=risk,
        meta={"domain": domain, "variant": "违规样本", "scene_name": sc.get("name", "")},
    )
    good = QualitySample(
        sample_id=f"{sid}-GOOD",
        scenario_id=case["scene_id"],
        input_text=q,
        agent_output=good_output,
        agent_route=expected if expected != "needs_evidence" else "needs_evidence",
        expected_route=expected,
        risk_level=risk,
        meta={"domain": domain, "variant": "合规样本", "scene_name": sc.get("name", "")},
    )
    return bad, good


def mock_semantic(sample: QualitySample, report):
    """演示模式：为被跳过的语义规则生成占位判定（明确标注 mock，避免误导评委）。"""
    for v in report.verdicts:
        if v.dimension == "semantic" and v.judge != "llm":
            v.judge = "mock(llm-占位)"
            v.verdict = "warn"
            v.evidence = "演示模式：语义判定未配置真实裁判（需 CS_MODEL_API_KEY / QS_JEV_API_KEY）"
            v.confidence = 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "deliverables/demo_reports.json"))
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    cases, sc_map = load_sources()
    picked = pick_cases(cases, sc_map)
    router = JudgeRouter()

    reports = []
    for case, sc in picked:
        bad, good = build_samples(case, sc)
        for sample in (bad, good):
            report = run_quality_check(sample, router=router)
            if args.mock:
                mock_semantic(sample, report)
            reports.append(report.to_dict())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "engine_version": "0.1.0",
        "mock_semantic": bool(args.mock),
        "judge_backend": "jev" if router.jev.available() else ("llm" if router.llm.available() else "未配置(规则层only)"),
        "reports": reports,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已生成 {len(reports)} 份质检报告 -> {out}")
    for r in reports:
        dims = {k: v["score"] for k, v in r["dimension_scores"].items() if v["rules_total"]}
        print(f"  [{r['overall'].upper():6s}] {r['sample_id']:14s} 升级人工={r['needs_human_review']} 维度分={dims}")
    if router.jev.available():
        print("裁判后端：Jev（毫秒级）")
    elif router.llm.available():
        print("裁判后端：LLM（Jev 未配置，已降级）")
    else:
        print("裁判后端：未配置（规则层已执行；设置 CS_MODEL_API_KEY 或 QS_JEV_API_KEY 启用语义判定）")


if __name__ == "__main__":
    main()
