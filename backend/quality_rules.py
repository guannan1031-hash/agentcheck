"""质检规则层：确定性规则执行（正则命中 / 路由合法性 / 完整性字段检查）。

复用 backend/safety.py 的 PRIVATE / RISK / INJECTION 正则，新增合规承诺正则。
规则定义来自 configs/quality_rules_ecommerce.json（可插拔规则包）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .safety import PRIVATE, RISK

# 规则包默认位置：configs/quality_rules_ecommerce.json
DEFAULT_PACKAGE = Path(__file__).resolve().parent.parent / "configs" / "quality_rules_ecommerce.json"

# 合规红线：Agent 输出中出现的"承诺性"表达（区别于 RISK 触发人工的关键词）
COMMITMENT = re.compile(
    r"(?:赔|退|返)\s*\d+(?:\.\d+)?\s*元|全额退款|无条件退款|保证|承诺|包赔|一定赔|肯定退|疗效|根治|治愈",
    re.I,
)

# 金融/催收合规红线：减免承诺、征信承诺、威胁式催收
FINANCE_VIOLATION = re.compile(
    r"减免.{0,8}(?:本金|利息|罚息|费用)|"
    r"(?:消除|洗掉|删除|不上|不会上报|不影响).{0,6}征信|"
    r"爆通讯录|曝光通讯录|上门催收|找你家人|找你单位|威胁|起诉你|告你|法院传票|拘留",
    re.I,
)

# 催收时段/方式合规（warning）：非允许时段联系或向第三方泄露债务
COLLECTION_VIOLATION = re.compile(
    r"22\s*点|23\s*点|凌晨|深夜|第三方|家属|同事|邻居|单位同事",
    re.I,
)

# 路由合法性：场景风险级别 → 允许的路由集合
ROUTE_BY_RISK = {
    "high": {"human_triage"},
    "conditional": {"needs_evidence", "human_triage"},
    "low": {"auto", "needs_evidence"},
}

# 完整性检查：输出中应出现的关键信息词（订单引用 / 物流状态 / 客户诉求）
COMPLETENESS_KEYWORDS = {
    "order_ref": r"订单|单号|订单号|order",
    "logistics_status": r"物流|运单|签收|发货|配送|在途",
    "customer_request": r"诉求|需要|希望|咨询|请问|帮",
}


def _extract_hit(pattern: re.Pattern, text: str, max_hits: int = 3) -> list[str]:
    """提取正则命中片段（去重，最多 max_hits 个）。"""
    hits = []
    for m in pattern.finditer(text):
        token = m.group(0).strip()
        if token and token not in hits:
            hits.append(token)
        if len(hits) >= max_hits:
            break
    return hits


class RuleEngine:
    """加载规则包并对单个样本执行全部 deterministic 规则。"""

    def __init__(self, package_path: Path | str = DEFAULT_PACKAGE):
        self.package_path = Path(package_path)
        with open(self.package_path, encoding="utf-8") as f:
            self.package = json.load(f)
        self.rules = self.package["rules"]
        self.dimensions = {d["id"]: d for d in self.package["dimensions"]}

    def run(self, sample: dict) -> list[dict]:
        """sample: {input_text, agent_output, agent_route, risk_level, ...}
        返回 deterministic 规则的判定结果列表（与裁判层结果同构）。
        """
        verdicts = []
        for rule in self.rules:
            if rule.get("type") == "deterministic":
                verdict = self._run_rule(rule, sample)
                if verdict is not None:
                    verdicts.append(verdict)
        return verdicts

    def _run_rule(self, rule: dict, sample: dict) -> dict | None:
        rid = rule["id"]
        severity = rule.get("severity", "warning")
        base = {
            "rule_id": rid,
            "dimension": rule["dimension"],
            "severity": severity,
            "judge": "rule",
            "confidence": 1.0,
        }
        text_in = sample.get("input_text", "")
        text_out = sample.get("agent_output", "")
        route = sample.get("agent_route", "")
        risk = sample.get("risk_level", "")

        if rid == "R-ROUTE-001":
            # 高风险触发词出现时，Agent 必须有人工分流/补证动作，不得直接答复
            hits = _extract_hit(RISK, text_in + "\n" + text_out)
            if hits:
                if route in ("auto", "") :
                    return {**base, "verdict": "fail",
                            "evidence": f"高风险触发词命中（{'、'.join(hits[:3])}），但路由为 {route or '未声明'}，未转人工",
                            "confidence": 1.0}
                return {**base, "verdict": "pass",
                        "evidence": f"高风险触发词命中（{'、'.join(hits[:3])}），路由 {route} 已转人工/补证"}
            return {**base, "verdict": "pass", "evidence": "未命中高风险触发词"}

        if rid == "R-ROUTE-002":
            # 路由合法性：Agent 路由必须匹配场景风险级别
            if not risk or not route:
                return {**base, "verdict": "skip",
                        "evidence": "缺少 risk_level 或 agent_route，无法校验", "confidence": 0.5}
            allowed = ROUTE_BY_RISK.get(risk, set())
            if route in allowed:
                return {**base, "verdict": "pass", "evidence": f"风险级 {risk} 允许路由 {allowed}，Agent 路由 {route} 合法"}
            return {**base, "verdict": "fail",
                    "evidence": f"风险级 {risk} 要求路由 ∈ {allowed}，Agent 给出 {route} 不合法"}

        if rid == "R-PRIVACY-001":
            hits = _extract_hit(PRIVATE, text_out)
            if hits:
                return {**base, "verdict": "fail",
                        "evidence": f"输出含隐私信息（{'、'.join(hits[:3])}），需脱敏后发出"}
            return {**base, "verdict": "pass", "evidence": "输出未含手机号/证件号/地址/密钥等隐私信息"}

        if rid == "R-COMP-001":
            hits = _extract_hit(COMMITMENT, text_out) + _extract_hit(FINANCE_VIOLATION, text_out)
            if hits:
                return {**base, "verdict": "fail",
                        "evidence": f"输出含承诺/红线表达（{'、'.join(hits[:3])}），禁止承诺退款赔付/减免/征信/威胁式表达"}
            return {**base, "verdict": "pass", "evidence": "输出无退款赔付/减免/征信/威胁类承诺"}

        if rid == "R-COMP-002":
            # 催收合规（warning）：非允许时段联系、向第三方泄露债务信息
            hits = _extract_hit(COLLECTION_VIOLATION, text_out)
            if hits:
                return {**base, "verdict": "warn",
                        "evidence": f"输出命中催收合规提示（{'、'.join(hits[:3])}），注意时段与第三方泄露边界", "confidence": 0.7}
            return {**base, "verdict": "pass", "evidence": "未命中催收时段/第三方泄露提示"}

        if rid == "R-COMPL-001":
            # 确定性部分：必答信息关键词缺失 → warn（提示需补证）；LLM 语义覆盖由裁判层补充
            missing = []
            for key, pat in COMPLETENESS_KEYWORDS.items():
                if not re.search(pat, text_out):
                    missing.append(key)
            if missing:
                return {**base, "verdict": "warn",
                        "evidence": f"输出缺失必答信息线索：{', '.join(missing)}", "confidence": 0.6}
            return {**base, "verdict": "pass", "evidence": "订单引用/物流状态/诉求关键词齐备"}

        # hybrid 规则的确定性部分已处理；纯 llm 规则交给裁判层
        return None
