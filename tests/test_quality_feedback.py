"""质检反馈闭环单元测试：suggest / apply(knowledge) / recheck / 留痕。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.quality_engine import QualitySample, run_quality_check
from backend.quality_feedback import suggest_fixes, apply_fix, recheck, get_feedback_log

BAD = dict(
    sample_id="FB-BAD-001",
    input_text="我要投诉，食品发霉了，要求退款赔偿！",
    agent_output="亲，您放心，我们保证全额退款给您，联系电话 13812345678。",
    agent_route="auto",
    risk_level="high",
)

GOOD = dict(
    sample_id="FB-GOOD-001",
    input_text="我要投诉，食品发霉了，要求退款赔偿！",
    agent_output="您好，您反馈的情况涉及退款赔付，已为您转接人工客服专员，请提供订单号以便核实。",
    agent_route="human_triage",
    risk_level="high",
)


class TestFeedbackSuggest:
    def test_suggest_generates_fixes_for_fail(self):
        r = run_quality_check(QualitySample(**BAD))
        fixes = suggest_fixes(r.to_dict())
        dims = {f["dimension"] for f in fixes}
        assert {"route", "privacy", "compliance"} <= dims
        assert all(f["fix_type"] in ("rule", "knowledge", "prompt") for f in fixes)

    def test_suggest_empty_for_pass(self):
        r = run_quality_check(QualitySample(**GOOD))
        fixes = suggest_fixes(r.to_dict())
        assert fixes == []


class TestFeedbackApply:
    def test_apply_knowledge_persists(self):
        path = Path(__file__).resolve().parent.parent / "demo-data/knowledge_additions.json"
        before = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
        result = apply_fix("knowledge", {
            "question": "食品发霉如何申请退款？",
            "answer": "需核实订单与凭证后由人工处理，禁止自动承诺退款。",
            "tags": ["退换退款", "食品安全"],
            "sample_id": "FB-BAD-001",
            "input_text": BAD["input_text"],
            "agent_output": BAD["agent_output"],
        })
        after = json.loads(path.read_text(encoding="utf-8"))
        assert result["total"] == len(before.get("items", [])) + 1
        assert after["items"][-1]["status"] == "pending_publish"

    def test_feedback_log_records(self):
        log = get_feedback_log()
        assert log["total"] >= 1
        assert log["records"][0]["fix_type"] == "knowledge"


class TestFeedbackRecheck:
    def test_recheck_after_fix_passes(self):
        # 整改后（正确路由 + 安全回复）重检：fail → pass
        r = recheck(QualitySample(**GOOD))
        assert r["overall"] == "pass"
        assert r["needs_human_review"] is False
