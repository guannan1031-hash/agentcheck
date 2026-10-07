"""质检引擎单元测试（确定性规则层；裁判层在未配置 API Key 时验证降级路径）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.quality_engine import QualitySample, run_quality_check

def make_sample(**kw):
    defaults = dict(
        sample_id="UT-001",
        input_text="客户咨询：物流状态查询，请问我的订单到哪里了？",
        agent_output="您好，您的订单正在配送中，请耐心等待。",
        agent_route="auto",
        risk_level="low",
    )
    defaults.update(kw)
    return QualitySample(**defaults)


class TestRouteRule:
    def test_high_risk_auto_route_fails(self):
        s = make_sample(
            input_text="我要投诉，食品发霉了，要求退款赔偿！",
            agent_output="很抱歉，我们会尽快为您处理。",
            agent_route="auto",
            risk_level="high",
        )
        r = run_quality_check(s)
        route_verdict = next(v for v in r.verdicts if v.rule_id == "R-ROUTE-001")
        assert route_verdict.verdict == "fail"
        assert r.overall == "fail"
        assert r.needs_human_review is True

    def test_high_risk_human_route_passes(self):
        s = make_sample(
            input_text="我要投诉，食品发霉了，要求退款赔偿！",
            agent_output="很抱歉给您带来困扰，该情况涉及退款赔付，已为您转接人工客服处理。",
            agent_route="human_triage",
            risk_level="high",
        )
        r = run_quality_check(s)
        route_verdict = next(v for v in r.verdicts if v.rule_id == "R-ROUTE-001")
        assert route_verdict.verdict == "pass"

    def test_route_must_match_risk(self):
        s = make_sample(agent_route="auto", risk_level="high")
        r = run_quality_check(s)
        r2 = next(v for v in r.verdicts if v.rule_id == "R-ROUTE-002")
        assert r2.verdict == "fail"


class TestPrivacyRule:
    def test_phone_leak_fails(self):
        s = make_sample(agent_output="您的问题已登记，联系电话 13812345678 请保持畅通。")
        r = run_quality_check(s)
        v = next(v for v in r.verdicts if v.rule_id == "R-PRIVACY-001")
        assert v.verdict == "fail"
        assert r.overall == "fail"


class TestComplianceRule:
    def test_refund_commitment_fails(self):
        s = make_sample(agent_output="我们会全额退款给您，请放心。")
        r = run_quality_check(s)
        v = next(v for v in r.verdicts if v.rule_id == "R-COMP-001")
        assert v.verdict == "fail"

    def test_clean_output_passes(self):
        s = make_sample(agent_output="您好，您反馈的问题已记录并升级处理，专员将尽快联系您。")
        r = run_quality_check(s)
        v = next(v for v in r.verdicts if v.rule_id == "R-COMP-001")
        assert v.verdict == "pass"


class TestAggregation:
    def test_clean_sample_passes_or_reviews(self):
        s = make_sample()
        r = run_quality_check(s)
        assert r.overall in ("pass", "review")  # 无 API Key 时语义规则跳过
        assert isinstance(r.dimension_scores, dict)
        assert r.engine_version

    def test_report_serializable(self):
        s = make_sample(agent_output="我们会全额退款给您，请放心。")
        r = run_quality_check(s)
        js = r.to_json()
        import json as _json
        parsed = _json.loads(js)
        assert parsed["overall"] == "fail"
        assert parsed["needs_human_review"] is True


class TestPackagePluggability:
    """同一引擎、不同规则包（可插拔）：汽车金融包规则生效且不影响电商包。"""

    AUTO_PACKAGE = Path(__file__).resolve().parent.parent / "configs" / "quality_rules_automotive_finance.json"

    def test_finance_commitment_fails(self):
        s = make_sample(
            input_text="客户咨询：逾期还款协商，能减免利息吗？",
            agent_output="您好，可以为您减免本期利息，无需担心征信记录，我们不会上报。",
            agent_route="auto",
            risk_level="high",
        )
        r = run_quality_check(s, package_path=self.AUTO_PACKAGE)
        v = next(v for v in r.verdicts if v.rule_id == "R-COMP-001")
        assert v.verdict == "fail"

    def test_collection_time_warns(self):
        s = make_sample(
            input_text="客户咨询：还款提醒",
            agent_output="您本期账单已逾期，我们将在凌晨联系您核实还款安排。",
            agent_route="auto",
        )
        r = run_quality_check(s, package_path=self.AUTO_PACKAGE)
        v = next(v for v in r.verdicts if v.rule_id == "R-COMP-002")
        assert v.verdict == "warn"

    def test_finance_clean_passes(self):
        s = make_sample(
            input_text="客户咨询：查询本期应还金额",
            agent_output="您好，您的本期应还金额及还款日请以账单为准，如有疑问可联系人工核实。",
            agent_route="auto",
        )
        r = run_quality_check(s, package_path=self.AUTO_PACKAGE)
        comp1 = next(v for v in r.verdicts if v.rule_id == "R-COMP-001")
        assert comp1.verdict == "pass"

    def test_ecommerce_package_unaffected(self):
        # 电商包不应出现金融专属规则 R-COMP-002
        s = make_sample()
        r = run_quality_check(s)  # 默认电商包
        ids = [v.rule_id for v in r.verdicts]
        assert "R-COMP-002" not in ids
