"""质检引擎主流程：样本 → 规则层 + 裁判层 → 统一报告。

三通道：
  1. 规则层（deterministic）：backend/quality_rules.py，复用 safety.py 正则，毫秒级、零成本。
  2. 裁判层（Jev / LLM）：backend/quality_judges.py，语义级判定（答非所问/幻觉/情绪/完整性语义）。
  3. 兜底与升级：critical fail 或低置信判定 → needs_human_review=True（人工复核队列）。

报告：每规则 verdict(pass/fail/warn/skip) + evidence + confidence + judge；
维度加权得分（权重来自规则包）；总体结论 + 是否升级人工。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .quality_rules import RuleEngine, DEFAULT_PACKAGE
from .quality_judges import JudgeRouter, JudgeUnavailable

ENGINE_VERSION = "0.1.0"

VERDICT_SCORE = {"pass": 1.0, "warn": 0.5, "fail": 0.0, "skip": None}


@dataclass
class QualitySample:
    sample_id: str
    input_text: str
    agent_output: str
    agent_route: str = "auto"
    scenario_id: Optional[str] = None
    expected_route: Optional[str] = None
    risk_level: Optional[str] = None
    confirmed_facts: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["meta"] = dict(self.meta or {})
        return d


@dataclass
class RuleVerdict:
    rule_id: str
    dimension: str
    severity: str
    verdict: str
    evidence: str
    confidence: float
    judge: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QualityReport:
    sample_id: str
    verdicts: list = field(default_factory=list)
    dimension_scores: dict = field(default_factory=dict)
    overall: str = "pass"
    needs_human_review: bool = False
    degradation: list = field(default_factory=list)
    engine_version: str = ENGINE_VERSION
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "verdicts": [v.to_dict() if isinstance(v, RuleVerdict) else v for v in self.verdicts],
            "dimension_scores": self.dimension_scores,
            "overall": self.overall,
            "needs_human_review": self.needs_human_review,
            "degradation": self.degradation,
            "engine_version": self.engine_version,
            "summary": self.summary,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def run_quality_check(
    sample: QualitySample | dict,
    package_path: Path | str = DEFAULT_PACKAGE,
    router: JudgeRouter | None = None,
) -> QualityReport:
    """对单个样本执行完整质检，返回统一报告。"""
    s = sample if isinstance(sample, QualitySample) else QualitySample(**sample)
    sdict = s.to_dict()

    engine = RuleEngine(package_path)
    router = router or JudgeRouter()

    verdicts: list[RuleVerdict] = []
    degradation: list[str] = []

    # 通道 1：确定性规则层
    for v in engine.run(sdict):
        verdicts.append(RuleVerdict(**v))

    # 通道 2/3：裁判层（llm / hybrid 规则的语义部分）
    llm_rules = [r for r in engine.rules if r.get("type") in ("llm", "hybrid")]
    for rule in llm_rules:
        try:
            jv = router.judge(rule, sdict)
            if jv is None:
                continue
            verdicts.append(RuleVerdict(
                rule_id=rule["id"],
                dimension=rule["dimension"],
                severity=rule.get("severity", "warning"),
                verdict=jv["verdict"],
                evidence=jv["evidence"],
                confidence=jv["confidence"],
                judge=jv["judge"],
            ))
        except JudgeUnavailable:
            degradation.append(f"{rule['id']}: 裁判不可用（未配置 API Key 或调用失败），已跳过语义判定")
        except Exception as exc:  # 裁判层异常不阻断整体质检
            degradation.append(f"{rule['id']}: 裁判异常 {type(exc).__name__}")

    if router.degraded and "jev" not in degradation:
        degradation.append("Jev 不可用，已降级 LLM 裁判")

    report = _aggregate(s, verdicts, engine, degradation)
    return report


def _aggregate(
    sample: QualitySample,
    verdicts: list[RuleVerdict],
    engine: RuleEngine,
    degradation: list[str],
) -> QualityReport:
    # 维度加权得分
    dim_scores: dict[str, dict] = {}
    for dim_id, dim in engine.dimensions.items():
        dim_scores[dim_id] = {
            "name": dim["name"],
            "weight": dim.get("weight", 0),
            "rules_total": 0,
            "rules_passed": 0,
            "score": 0.0,
        }
    for v in verdicts:
        if v.dimension not in dim_scores:
            continue
        entry = dim_scores[v.dimension]
        entry["rules_total"] += 1
        sc = VERDICT_SCORE.get(v.verdict)
        if sc is not None:
            entry["score"] += sc
        if v.verdict == "pass":
            entry["rules_passed"] += 1
    weighted_total = 0.0
    weight_sum = 0.0
    for entry in dim_scores.values():
        if entry["rules_total"]:
            entry["score"] = round(entry["score"] / entry["rules_total"], 4)
            weighted_total += entry["score"] * entry["weight"]
            weight_sum += entry["weight"]
    overall_score = round(weighted_total / weight_sum, 4) if weight_sum else 0.0

    # 总体结论：任一 critical fail → fail；有 fail → fail；有 warn 或低置信 → review；否则 pass
    has_fail = any(v.verdict == "fail" for v in verdicts)
    has_warn = any(v.verdict == "warn" for v in verdicts)
    low_confidence = any(v.verdict in ("pass", "warn", "fail") and v.confidence < 0.6 for v in verdicts)
    if has_fail:
        overall = "fail"
    elif has_warn or low_confidence:
        overall = "review"
    else:
        overall = "pass"
    if not verdicts:
        overall = "review"
        degradation.append("无任何规则判定产出（检查规则包与样本字段）")

    needs_human_review = overall != "pass" or has_fail or low_confidence

    summary = _make_summary(sample, overall, overall_score, verdicts)
    return QualityReport(
        sample_id=sample.sample_id,
        verdicts=verdicts,
        dimension_scores=dim_scores,
        overall=overall,
        needs_human_review=needs_human_review,
        degradation=degradation,
        summary=summary,
    )


def _make_summary(
    sample: QualitySample,
    overall: str,
    score: float,
    verdicts: list[RuleVerdict],
) -> str:
    fails = [v for v in verdicts if v.verdict == "fail"]
    warns = [v for v in verdicts if v.verdict == "warn"]
    if fails:
        return (
            f"质检未通过（得分 {score:.2f}）：{len(fails)} 项违规，"
            f"包括 {'、'.join(v.evidence[:40] for v in fails[:3])}。建议进入人工复核。"
        )
    if warns:
        return (
            f"质检需复核（得分 {score:.2f}）：{len(warns)} 项提示，"
            f"包括 {'、'.join(v.evidence[:40] for v in warns[:2])}。建议人工确认后发出。"
        )
    return f"质检通过（得分 {score:.2f}），{len(verdicts)} 项规则全部合规，可自动发出。"
