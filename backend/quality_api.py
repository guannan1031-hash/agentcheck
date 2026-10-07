"""质检 API：接入 FastAPI。

- POST /api/quality/check       单样本质检 → 统一报告（含毫秒级耗时）
- POST /api/quality/batch       批量质检（benchmark 全量）→ 统计仪表数据
- GET  /api/quality/packages    可用规则包列表（可插拔规则包展示）
- GET  /api/quality/health      引擎与裁判后端状态

规则包：configs/quality_rules_*.json（package 参数选择，引擎零改动）。
裁判后端：Jev（毫秒级）→ LLM（降级）→ 规则层 only（未配置时，语义规则跳过并记录 degradation）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .quality_engine import QualitySample, run_quality_check
from .quality_judges import JudgeRouter, JevJudge, LLMJudge
from .quality_feedback import suggest_fixes, apply_fix, recheck, get_feedback_log

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "configs"
PACKAGES = {
    "ecommerce": PACKAGE_DIR / "quality_rules_ecommerce.json",
    "automotive": PACKAGE_DIR / "quality_rules_automotive_finance.json",
}


class CheckRequest(BaseModel):
    sample_id: str = "demo-001"
    input_text: str = ""
    agent_output: str = ""
    agent_route: str = "auto"
    scenario_id: str | None = None
    expected_route: str | None = None
    risk_level: str | None = None
    confirmed_facts: list = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    package: str = "ecommerce"


class BatchRequest(BaseModel):
    package: str = "ecommerce"
    limit: int = 20  # benchmark 抽样条数
    include_good: bool = True  # 同时构造合规样本对比


class SuggestRequest(CheckRequest):
    pass


class ApplyRequest(BaseModel):
    fix_type: str  # rule / knowledge / prompt
    payload: dict = Field(default_factory=dict)


class RecheckRequest(CheckRequest):
    pass


def _report_for(sample: QualitySample, package: str, router: JudgeRouter, mock: bool = False) -> dict:
    t0 = time.perf_counter()
    report = run_quality_check(sample, package_path=PACKAGES.get(package, PACKAGES["ecommerce"]), router=router)
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    data = report.to_dict()
    data["latency_ms"] = latency_ms
    data["judge_backend"] = "jev" if router.jev.available() else ("llm" if router.llm.available() else "rule-only")
    data["package"] = package
    return data


def _make_sample(req: CheckRequest) -> QualitySample:
    return QualitySample(
        sample_id=req.sample_id,
        input_text=req.input_text,
        agent_output=req.agent_output,
        agent_route=req.agent_route,
        scenario_id=req.scenario_id,
        expected_route=req.expected_route,
        risk_level=req.risk_level,
        confirmed_facts=req.confirmed_facts,
        meta=req.meta,
    )


def register_quality(app):
    router = APIRouter(prefix="/api/quality")

    @router.post("/check")
    def check(req: CheckRequest):
        router_ = JudgeRouter()
        sample = _make_sample(req)
        return _report_for(sample, req.package, router_)

    @router.post("/batch")
    def batch(req: BatchRequest):
        """从 benchmark 抽样构造违规/合规对比样本，批量质检并统计。"""
        bench = json.loads((ROOT / "tests/fixtures/full-scenario-benchmark-v1.4.json").read_text(encoding="utf-8"))
        scen = json.loads((ROOT / "configs/scenarios-v1.4.json").read_text(encoding="utf-8"))
        sc_map = {s["id"]: s for s in scen["scenarios"]}
        cases = bench["cases"][: req.limit]

        router_ = JudgeRouter()
        reports = []
        for case in cases:
            sc = sc_map.get(case["expected_scene_id"], {})
            risk = sc.get("risk_level", "conditional")
            text = case["synthetic_text"].replace("合成测试：", "")
            bad = QualitySample(
                sample_id=f"{case['case_id']}-BAD",
                scenario_id=case["scene_id"], input_text=text,
                agent_output="亲，您放心，我们保证全额退款给您，联系电话 13812345678。",
                agent_route="auto", expected_route=case["expected_route"], risk_level=risk,
                meta={"variant": "违规样本"},
            )
            reports.append(_report_for(bad, req.package, router_))
            if req.include_good:
                good_route = "human_triage" if risk == "high" else "needs_evidence"
                good = QualitySample(
                    sample_id=f"{case['case_id']}-GOOD",
                    scenario_id=case["scene_id"], input_text=text,
                    agent_output="您好，您反馈的情况需核实后处理，已为您转接人工客服，请提供订单号以便核实。",
                    agent_route=good_route, expected_route=case["expected_route"], risk_level=risk,
                    meta={"variant": "合规样本"},
                )
                reports.append(_report_for(good, req.package, router_))

        total = len(reports)
        failed = sum(1 for r in reports if r["overall"] == "fail")
        reviewed = sum(1 for r in reports if r["needs_human_review"])
        passed = sum(1 for r in reports if r["overall"] == "pass")
        latencies = [r["latency_ms"] for r in reports]
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "needs_human_review": reviewed,
            "fail_rate": round(failed / total, 4) if total else 0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "max_latency_ms": max(latencies) if latencies else 0,
            "judge_backend": reports[0]["judge_backend"] if reports else "rule-only",
            "package": req.package,
            "reports": reports,
        }

    @router.get("/packages")
    def packages():
        out = []
        for key, path in PACKAGES.items():
            if path.exists():
                meta = json.loads(path.read_text(encoding="utf-8")).get("meta", {})
                out.append({"id": key, "name": meta.get("name", key), "description": meta.get("description", ""),
                            "version": meta.get("version", "")})
        return {"packages": out}

    # ---- 质检反馈闭环：检出 → 建议 → 整改 → 重检 ----

    @router.post("/feedback/suggest")
    def feedback_suggest(req: SuggestRequest):
        """质检并生成整改建议（规则增强 / 知识补全 / 提示词优化）。"""
        router_ = JudgeRouter()
        sample = _make_sample(req)
        report = _report_for(sample, req.package, router_)
        fixes = suggest_fixes(report)
        return {"report": report, "fixes": fixes}

    @router.post("/feedback/apply")
    def feedback_apply(req: ApplyRequest):
        """应用整改动作（真实写入规则包/知识文件），返回更新摘要。"""
        return apply_fix(req.fix_type, req.payload)

    @router.post("/feedback/recheck")
    def feedback_recheck(req: RecheckRequest):
        """整改后重检，验证 fail → pass 闭环。"""
        router_ = JudgeRouter()
        sample = _make_sample(req)
        return recheck(sample, req.package, router_)

    @router.get("/feedback/log")
    def feedback_log(limit: int = 20):
        return get_feedback_log(limit)

    @router.get("/health")
    def health():
        jev = JevJudge()
        llm = LLMJudge()
        return {
            "engine_version": "0.1.0",
            "jev": {"available": jev.available(), "endpoint": jev.endpoint if jev.available() else None},
            "llm": {"available": llm.available(), "provider": "deepseek/zhipu"},
            "packages": [p.name for p in PACKAGES.values() if p.exists()],
            "status": "ok",
        }

    app.include_router(router)
