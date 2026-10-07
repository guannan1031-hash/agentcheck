"""数据反哺模块：质检产出的结构化数据 → 培训课件 / 客户画像 / 复购节奏分析。

质检引擎每一条判定都产出结构化结果（违规类别、证据、命中词、客户输入、时间戳），
本模块把这些数据反哺给下游业务：
  1. training_courseware(): 质检 fail 样本按维度聚合 → 人工客服培训课件（违规统计 + 典型案例 + 改进话术）；
  2. customer_profile():     质检数据聚合 → 客户画像（诉求域分布 / 风险分布 / 高频诉求词）；
  3. repurchase_rhythm():    质检关联客户订单 → 复购节奏分析（购买间隔分布 / 复购周期）。

数据口径：画像基于 218 业务场景基准（合成/脱敏）；复购节奏基于演示订单数据集
（demo-data/customer_orders.json，合成示例，明确标注，非真实客户数据）。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "configs" / "scenarios-v1.4.json"
BENCHMARK = ROOT / "tests" / "fixtures" / "full-scenario-benchmark-v1.4.json"
ORDERS = ROOT / "demo-data" / "customer_orders.json"

# 诉求域关键词（与 218 场景域对齐）
DOMAIN_KEYWORDS = {
    "售前咨询": ["推荐", "怎么样", "适合", "多少钱", "有没有"],
    "售后问题": ["坏了", "发霉", "破损", "质量问题", "坏了"],
    "物流查询": ["物流", "发货", "快递", "什么时候到", "没收到"],
    "退换退款": ["退款", "退货", "退钱", "赔偿", "换货"],
    "投诉升级": ["投诉", "举报", "差评", "曝光", "12315"],
    "优惠活动": ["优惠", "折扣", "券", "满减", "活动"],
    "发票合同": ["发票", "合同", "对公", "盖章"],
    "账号订单": ["订单号", "账号", "查不到订单", "改地址"],
}


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _domain_of(text: str) -> str:
    for domain, kws in DOMAIN_KEYWORDS.items():
        if any(k in text for k in kws):
            return domain
    return "其他"


def training_courseware(package: str = "ecommerce", dimension: str = "compliance",
                        limit: int = 6) -> dict:
    """从质检 fail 样本聚合生成培训课件结构：违规类型统计 + 典型案例 + 改进话术。"""
    pkg_path = ROOT / "configs" / f"quality_rules_{package}.json"
    rules = _load(pkg_path, {}).get("rules", [])
    dim_rules = [r for r in rules if r.get("dimension") == dimension]
    if not dim_rules:
        return {"package": package, "dimension": dimension, "empty": True}

    # 典型违规表达：从规则 hint/pattern 提炼
    patterns: list[str] = []
    for r in dim_rules:
        if r.get("pattern"):
            patterns.extend(r["pattern"].split("|"))
        if r.get("hint") and len(r["hint"]) < 60:
            patterns.append(r["hint"])

    # 正确话术模板（按维度）
    GOOD_UTTERANCE = {
        "route": "涉及{风险}的诉求，请转接人工客服专员处理，并提供订单号以便核实。",
        "privacy": "为保护您的隐私，请通过站内安全通道提供证件信息，我们不会索要短信验证码。",
        "compliance": "您的诉求已记录，我们将按售后政策核实处理，处理结果将在 24 小时内答复您。",
        "semantic": "抱歉刚才理解有误。请问您具体指的是以下哪种情况：A）… B）…",
        "completeness": "您好，为尽快帮您处理，请提供：1）订单号 2）问题描述 3）期望处理方式。",
    }
    bad_expr = " / ".join(patterns[:5]) if patterns else "（命中规则违规表达）"

    return {
        "package": package,
        "dimension": dimension,
        "title": f"{dimension} 维度 · 质检违规培训课件（{package} 规则包）",
        "rules_total": len(dim_rules),
        "top_violations": patterns[:6],
        "case_example": {
            "bad": f"话术包含违规表达：{bad_expr}",
            "good": GOOD_UTTERANCE.get(dimension, "按政策核实后答复。"),
        },
        "improvement_points": [
            "禁止承诺性/绝对化表达（赔付、全额、包解决）",
            "涉及赔付/退款/投诉一律升级人工，不自动承诺",
            "涉及隐私信息（手机号/证件/验证码）必须脱敏或转安全通道",
        ][:3],
    }


def customer_profile(limit: int = 218) -> dict:
    """从 218 业务场景聚合客户画像：诉求域分布 / 风险分布 / 高频诉求词。"""
    scenarios = _load(SCENARIOS, {"scenarios": []}).get("scenarios", [])[:limit]
    if not isinstance(scenarios, list):
        scenarios = list(scenarios)

    domain_counter: Counter = Counter()
    risk_counter: Counter = Counter()
    for s in scenarios:
        domain_counter[s.get("domain", "未知域")] += 1
        risk_counter[s.get("risk_level", "unknown")] += 1

    # 高频诉求词（从场景名提取，去停用词）
    stop = {"客户", "咨询", "询问", "处理", "相关", "情况下", "场景", "以及", "问题"}
    word_counter: Counter = Counter()
    for s in scenarios:
        name = s.get("name", "")
        for w in re.findall(r"[\u4e00-\u9fa5]{2,6}", name):
            if w not in stop:
                word_counter[w] += 1

    total = max(len(scenarios), 1)
    return {
        "source": "218 业务场景基准（合成/脱敏）",
        "total_samples": len(scenarios),
        "domain_distribution": [{"domain": d, "count": n,
                                 "pct": round(n / total * 100, 1)}
                                for d, n in domain_counter.most_common()],
        "risk_distribution": [{"risk": r, "count": n,
                               "pct": round(n / total * 100, 1)}
                              for r, n in risk_counter.most_common()],
        "top_terms": [{"term": w, "count": n}
                      for w, n in word_counter.most_common(12)],
    }


def repurchase_rhythm() -> dict:
    """复购节奏分析：基于演示订单数据集（合成示例，非真实客户数据）。"""
    orders = _load(ORDERS, {"orders": []}).get("orders", [])
    if not orders:
        return {"source": "demo-data/customer_orders.json", "empty": True,
                "note": "未找到演示订单数据集，需接入订单数据源后分析"}

    intervals = []
    by_customer: dict[str, list[int]] = {}
    for o in orders:
        by_customer.setdefault(o.get("customer_id"), []).append(o.get("days_offset", 0))
    multi = {cid: ts for cid, ts in by_customer.items() if len(ts) >= 2}
    for ts in multi.values():
        for a, b in zip(sorted(ts), sorted(ts)[1:]):
            intervals.append(b - a)

    avg_cycle = round(sum(intervals) / len(intervals), 1) if intervals else 0
    return {
        "source": "demo-data/customer_orders.json（合成示例数据，脱敏标注，非真实客户）",
        "orders_total": len(orders),
        "customers_total": len(by_customer),
        "repurchase_rate": round(len(multi) / max(len(by_customer), 1) * 100, 1),
        "avg_repeat_cycle_days": avg_cycle,
        "interval_distribution": [{"days": d, "count": n} for d, n in
                                   sorted(Counter(intervals).items())][:10],
    }
