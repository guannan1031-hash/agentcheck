"""质检反馈闭环：检出 → 整改建议 → 应用整改 → 重检验证 → 回归用例。

闭环链路：
  1. suggest_fixes(report)：按失败规则维度生成整改建议（规则增强 / 知识补全 / 提示词优化）；
  2. apply_fix(...)：真实写入整改资源（规则包 JSON / 知识补全文件 / 提示词建议日志）；
  3. recheck(sample)：整改后重新质检，对比整改前后结论（fail → pass 验证）；
  4. 整改样本自动登记为回归用例，防止复发。

所有整改动作留痕（feedback_log.json），可审计、可回滚。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .quality_engine import QualitySample, run_quality_check
from .quality_judges import JudgeRouter

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "state/local/feedback_log.json"
KNOWLEDGE_ADDITIONS = ROOT / "demo-data/knowledge_additions.json"
REGRESSION_CASES = ROOT / "configs/regression_cases.json"

# 失败规则维度 → 整改动作类型与建议模板
FIX_MAP = {
    "route": {
        "type": "rule",
        "template": "路由策略增强：将命中场景纳入高风险路由映射（high → human_triage），或更新规则包 R-ROUTE-* 触发词。",
    },
    "privacy": {
        "type": "rule",
        "template": "规则包增强：将新出现的隐私表达（手机号/证件/密钥变体）补充到 R-PRIVACY-001 正则。",
    },
    "compliance": {
        "type": "rule",
        "template": "规则包增强：将新出现的承诺/红线表达（赔付、减免、征信、威胁）补充到 R-COMP-* 正则。",
    },
    "semantic": {
        "type": "knowledge",
        "template": "知识库补全：当前知识缺失导致答非所问/幻觉，建议补充对应 FAQ 条目与事实约束，并同步优化回复提示词。",
    },
    "completeness": {
        "type": "knowledge",
        "template": "知识/提示词补全：输出缺少必答信息（订单引用/物流状态/诉求），建议补充知识字段并引导 Agent 追问缺失信息。",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def suggest_fixes(report: dict) -> list[dict]:
    """根据质检报告生成整改建议（按失败规则维度去重）。"""
    fixes: dict[str, dict] = {}
    for v in report.get("verdicts", []):
        if v.get("verdict") != "fail":
            continue
        dim = v.get("dimension")
        if dim not in FIX_MAP or dim in fixes:
            continue
        fix = FIX_MAP[dim]
        fixes[dim] = {
            "dimension": dim,
            "fix_type": fix["type"],
            "rule_ids": [x["rule_id"] for x in report["verdicts"]
                         if x.get("dimension") == dim and x.get("verdict") == "fail"],
            "suggestion": fix["template"],
            "evidence": v.get("evidence", ""),
        }
    return list(fixes.values())


def apply_fix(fix_type: str, payload: dict) -> dict:
    """执行整改动作并留痕。支持：
      - rule: 向规则包追加一条确定性规则（真实写入 configs/quality_rules_*.json）
      - knowledge: 向 demo-data/knowledge_additions.json 追加知识条目（标注待发布）
      - prompt: 记录提示词优化建议（feedback log）
    返回更新后的资源摘要。
    """
    record = {
        "id": uuid4().hex[:8],
        "time": _now(),
        "fix_type": fix_type,
        "payload": payload,
    }
    result = {}

    if fix_type == "rule":
        pkg = payload.get("package", "ecommerce")
        path = ROOT / "configs" / f"quality_rules_{pkg}.json"
        data = _load_json(path, {})
        rules = data.setdefault("rules", [])
        dimension = payload.get("dimension", "compliance")
        desc = payload.get("description", "")
        hint = payload.get("hint", "")
        new_id = f"R-{dimension[:4].upper()}-{len(rules) + 1:03d}"
        new_rule = {
            "id": new_id,
            "dimension": dimension,
            "type": "deterministic",
            "severity": payload.get("severity", "critical"),
            "description": desc or "闭环整改新增规则",
            "hint": hint,
            "source": "quality-feedback-loop",
            "added_at": _now(),
        }
        rules.append(new_rule)
        meta = data.setdefault("meta", {})
        meta["version"] = f"{float(meta.get('version', '0.1')) + 0.1:.1f}"
        _save_json(path, data)
        result = {"resource": str(path.relative_to(ROOT)), "rule_id": new_id,
                  "rules_total": len(rules), "version": meta["version"]}

    elif fix_type == "knowledge":
        path = KNOWLEDGE_ADDITIONS
        data = _load_json(path, {"items": []})
        item = {
            "id": uuid4().hex[:8],
            "time": _now(),
            "status": "pending_publish",
            "question": payload.get("question", ""),
            "answer": payload.get("answer", ""),
            "tags": payload.get("tags", []),
            "source": "quality-feedback-loop",
        }
        data["items"].append(item)
        _save_json(path, data)
        result = {"resource": str(path.relative_to(ROOT)), "knowledge_id": item["id"],
                  "total": len(data["items"]), "status": "pending_publish"}

    elif fix_type == "prompt":
        path = LOG_PATH
        data = _load_json(path, {"records": []})
        result = {"resource": str(path.relative_to(ROOT)), "record_id": record["id"]}

    else:
        raise ValueError(f"未知整改类型: {fix_type}")

    log = _load_json(LOG_PATH, {"records": []})
    record["result"] = result
    log["records"].append(record)
    _save_json(LOG_PATH, log)

    # 整改样本自动登记为回归用例
    if payload.get("sample_id"):
        add_regression_case(payload["sample_id"], fix_type, payload.get("input_text", ""),
                            payload.get("agent_output", ""), result.get("rule_id") or result.get("knowledge_id"))
    return result


def add_regression_case(sample_id: str, fix_type: str, input_text: str,
                        agent_output: str, ref_id: Optional[str] = None) -> None:
    """登记回归用例：整改后样本进入基准集，防止复发。"""
    path = REGRESSION_CASES
    data = _load_json(path, {"cases": []})
    data["cases"].append({
        "sample_id": sample_id,
        "time": _now(),
        "fix_type": fix_type,
        "input_text": input_text,
        "agent_output": agent_output,
        "reference": ref_id,
    })
    _save_json(path, data)


def recheck(sample: QualitySample | dict, package: str = "ecommerce",
            router: JudgeRouter | None = None) -> dict:
    """整改后重检：返回最新质检结论（用于与整改前对比验证 fail → pass）。"""
    s = sample if isinstance(sample, QualitySample) else QualitySample(**sample)
    report = run_quality_check(s, package_path=ROOT / "configs" / f"quality_rules_{package}.json",
                               router=router or JudgeRouter())
    data = report.to_dict()
    data["package"] = package
    data["judge_backend"] = ("jev" if (router or JudgeRouter()).jev.available()
                             else "llm" if (router or JudgeRouter()).llm.available() else "rule-only")
    return data


def get_feedback_log(limit: int = 20) -> dict:
    data = _load_json(LOG_PATH, {"records": []})
    return {"total": len(data["records"]), "records": data["records"][-limit:][::-1]}
