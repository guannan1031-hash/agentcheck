"""数据反哺模块测试：培训课件 / 客户画像 / 复购节奏。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.quality_insights import training_courseware, customer_profile, repurchase_rhythm


class TestTraining:
    def test_courseware_compliance(self):
        r = training_courseware("ecommerce", "compliance")
        assert r["rules_total"] >= 1
        assert r["case_example"]["bad"] and r["case_example"]["good"]
        assert len(r["top_violations"]) >= 1

    def test_courseware_unknown_dimension(self):
        r = training_courseware("ecommerce", "nonsense")
        assert r.get("empty") is True


class TestProfile:
    def test_profile_from_benchmark(self):
        r = customer_profile()
        assert r["total_samples"] == 218
        assert len(r["domain_distribution"]) >= 4
        assert len(r["risk_distribution"]) >= 2
        assert len(r["top_terms"]) >= 5


class TestRepurchase:
    def test_repurchase_rhythm(self):
        r = repurchase_rhythm()
        assert r["orders_total"] >= 20
        assert r["repurchase_rate"] > 0
        assert r["avg_repeat_cycle_days"] > 0
        assert len(r["interval_distribution"]) >= 3
