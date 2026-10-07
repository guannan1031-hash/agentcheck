"""P0 full-scenario registry and conservative, synthetic-only case evaluation."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import actor_ref, require_role
from .safety import contains_private, risky

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "configs/scenarios-v1.4.json"
Role = Literal["客服", "主管", "运营", "供应链", "仓储物流", "财务", "质量"]
Fact = Literal["order_ref", "logistics_status", "product_ref", "quantity", "evidence", "batch", "opened_status", "customer_request", "business_status"]
Department = Literal["运营", "供应链", "仓储物流", "财务", "质量"]
ALL_ROLES = ("客服", "主管", "运营", "供应链", "仓储物流", "财务", "质量")
SPLIT = re.compile(r"[（）()\/、，,：:\s]+")
GENERIC = {"问题", "咨询", "商品", "客户", "处理", "其他", "场景", "售后"}


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, status, message):
    if not condition:
        raise HTTPException(status, message)


def load_registry(path=REGISTRY_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("scenarios", [])
    if payload.get("meta", {}).get("scenario_count") != 218 or len(rows) != 218 or len({row["id"] for row in rows}) != 218:
        raise RuntimeError("全场景配置必须包含218个唯一场景。")
    return payload


REGISTRY = load_registry()
SCENARIOS = REGISTRY["scenarios"]
BY_ID = {row["id"]: row for row in SCENARIOS}

TASK_PACKS = {
    "logistics_stalled": {
        "scene_ids": {"S006", "S007"},
        "basis": "物流轨迹停滞需要先核查承运轨迹、异常节点和可催办状态。",
        "tasks": [
            {"key": "warehouse", "department": "仓储物流", "summary": "核查承运轨迹、停滞节点与可催办状态", "depends_on": []},
        ],
    },
    "signed_not_received": {
        "scene_ids": {"S009", "S010"},
        "basis": "签收未收到需要先核查签收记录、承运证明和包裹交付状态。",
        "tasks": [
            {"key": "warehouse", "department": "仓储物流", "summary": "核查签收记录、承运证明与包裹交付状态", "depends_on": []},
        ],
    },
    "missing_item": {
        "scene_ids": {"S086"},
        "basis": "少件先由仓储核实出库与分包裹事实，再由财务只读核查相关售后进度。",
        "tasks": [
            {"key": "warehouse", "department": "仓储物流", "summary": "核查出库明细、包裹拆分与少件事实", "depends_on": []},
            {"key": "finance", "department": "财务", "summary": "只读核查相关售后或退款进度，不执行资金动作", "depends_on": ["warehouse"]},
        ],
    },
    "stockout_delay": {
        "scene_ids": {"S174"},
        "basis": "缺货延迟先确认页面与仓库库存，再核查在途和补货计划。",
        "tasks": [
            {"key": "operations", "department": "运营", "summary": "核查页面库存、可售状态与商品展示", "depends_on": []},
            {"key": "supply", "department": "供应链", "summary": "核查在途数量、预计到货与补货计划", "depends_on": ["operations"]},
        ],
    },
    "refund_delayed": {
        "scene_ids": {"S125"},
        "basis": "退款迟迟未到账只允许财务核查系统状态和到账依据，不能再次发起退款。",
        "tasks": [
            {"key": "finance", "department": "财务", "summary": "只读核查退款状态、到账依据与异常原因，不执行退款", "depends_on": []},
        ],
    },
}


def task_pack_for(scene_id):
    return next(((pack_id, pack) for pack_id, pack in TASK_PACKS.items() if scene_id in pack["scene_ids"]), (None, None))


def _aliases(name):
    values = [name] + [value for value in SPLIT.split(name) if len(value) >= 2 and value not in GENERIC]
    return sorted(set(values), key=len, reverse=True)


def classify_scene(text, hint=None):
    """Return one conservative classification; ties and weak signals stay unknown."""
    if hint:
        return BY_ID.get(hint), "user_selected", 1.0
    exact = [row for row in SCENARIOS if row["name"] in text]
    if exact:
        row = max(exact, key=lambda item: len(item["name"]))
        return row, "exact_title", 1.0
    scored = []
    for row in SCENARIOS:
        hits = [alias for alias in _aliases(row["name"])[1:] if alias in text]
        if hits:
            scored.append((max(map(len, hits)), len(hits), row))
    if not scored:
        return None, "unknown", 0.0
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    top = scored[0]
    tied = [item for item in scored if item[:2] == top[:2]]
    if len(tied) != 1 or top[0] < 2:
        return None, "ambiguous", 0.0
    return top[2], "alias", min(0.85, 0.55 + top[0] / 20)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: Role | None = None


class CaseInput(Input):
    text: str = Field(min_length=1, max_length=500)
    synthetic_or_redacted: Literal[True]
    request_id: UUID
    case_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    scenario_hint_id: str | None = Field(default=None, pattern=r"^S\d{3}$")
    confirmed_facts: list[Fact] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def valid_update(self):
        if (self.case_id is None) != (self.expected_revision is None):
            raise ValueError("case_id and expected_revision must be supplied together")
        if len(set(self.confirmed_facts)) != len(self.confirmed_facts):
            raise ValueError("Duplicate facts")
        return self


class ReviewInput(Input):
    scenario_id: str = Field(pattern=r"^S\d{3}$")
    occurrence: Literal["unknown", "yes", "no"]
    frequency_30d: int | None = Field(default=None, ge=0, le=100000)
    current_steps: str = Field(default="", max_length=500)
    current_systems: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=500)
    synthetic_or_redacted: Literal[True]
    request_id: UUID
    expected_version: int | None = Field(default=None, ge=1)


class ReviewDecisionInput(Input):
    expected_version: int = Field(ge=1)
    decision: Literal["confirmed", "needs_revision", "not_applicable"]
    note: str = Field(default="", max_length=500)
    request_id: UUID


class TestReviewInput(Input):
    case_id: UUID
    expected_case_revision: int = Field(ge=1)
    verdict: Literal["correct", "wrong", "unsure"]
    expected_scene_id: str | None = Field(default=None, pattern=r"^S\d{3}$")
    note: str = Field(default="", max_length=500)
    request_id: UUID

    @model_validator(mode="after")
    def expected_for_wrong(self):
        if self.verdict == "wrong" and not self.expected_scene_id:
            raise ValueError("Wrong classifications need an expected scene")
        return self


class TaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    department: Department
    summary: str = Field(min_length=1, max_length=200)
    depends_on: list[str] = Field(default_factory=list, max_length=5)


class TaskPlanInput(Input):
    case_id: UUID
    expected_case_revision: int = Field(ge=1)
    tasks: list[TaskDraft] = Field(min_length=1, max_length=5)
    request_id: UUID
    synthetic_or_redacted: Literal[True]

    @model_validator(mode="after")
    def valid_graph(self):
        keys = [task.key for task in self.tasks]
        if len(set(keys)) != len(keys) or len({task.department for task in self.tasks}) != len(self.tasks):
            raise ValueError("Task keys and departments must be unique")
        known = set(keys)
        graph = {task.key: set(task.depends_on) for task in self.tasks}
        if any(task.key in task.depends_on or not set(task.depends_on) <= known for task in self.tasks):
            raise ValueError("Task dependency is invalid")
        visiting, visited = set(), set()
        def walk(key):
            if key in visiting:
                raise ValueError("Task dependency cycle")
            if key in visited:
                return
            visiting.add(key)
            for parent in graph[key]:
                walk(parent)
            visiting.remove(key); visited.add(key)
        for key in keys:
            walk(key)
        return self


class TaskPlanActionInput(Input):
    expected_version: int = Field(ge=1)
    action: Literal["approve", "reject", "accept_task", "report_task", "return_task", "close"]
    task_id: str | None = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=500)
    request_id: UUID
    synthetic_or_redacted: Literal[True]

    @model_validator(mode="after")
    def required_fields(self):
        if self.action in ("accept_task", "report_task", "return_task") and not self.task_id:
            raise ValueError("Task action requires task_id")
        if self.action in ("reject", "report_task", "return_task", "close") and not self.note.strip():
            raise ValueError("Action requires note")
        return self


def evaluate(text, scenario, confirmed_facts, source, confidence):
    safety_signal = risky(text)
    if not scenario:
        reason = "检测到风险线索但无法可靠识别业务场景，必须人工分诊。" if safety_signal else "没有足够证据唯一识别场景，请人工选择场景并核对上下文。"
        return {"scene_id": None, "scene_name": "待人工选择", "domain": "待分诊", "risk_level": "high" if safety_signal else "unknown",
                "classification_source": source, "confidence": confidence, "route": "human_triage", "state": "待人工分诊",
                "reason": reason, "missing_facts": [], "reply_draft": "已为您记录，客服核对完整情况后继续处理。", "owner_suggestion": "主管分诊"}
    high = safety_signal or scenario["risk_level"] == "high"
    missing_keys = [key for key in scenario["required_facts"] if key not in confirmed_facts]
    labels = REGISTRY["fact_labels"]
    base = {"scene_id": scenario["id"], "scene_name": scenario["name"], "domain": scenario["domain"],
            "risk_level": "high" if high else scenario["risk_level"], "classification_source": source,
            "confidence": confidence, "missing_facts": [{"key": key, "label": labels[key]} for key in missing_keys],
            "owner_suggestion": scenario["owner_suggestion"]}
    if high:
        return {**base, "route": "human_triage", "state": "待人工分诊",
                "reason": "该场景或当前上下文含食品安全、赔付、投诉等风险线索；AI只做摘要、补证提示和路由建议。",
                "reply_draft": "已记录您的情况，客服将优先核对并由人工继续处理。"}
    if missing_keys:
        return {**base, "route": "needs_evidence", "state": "待补证",
                "reason": "尚缺：" + "、".join(item["label"] for item in base["missing_facts"]) + "。",
                "reply_draft": "为便于核查，请通过商家原有渠道补充必要信息；客服确认后继续处理。"}
    return {**base, "route": "needs_business_rule", "state": "待业务规则确认",
            "reason": "事实项已齐，但该场景仍是业务规则草稿；需确认店铺适用范围、审批权限、责任部门和时效。",
            "reply_draft": "信息已登记，客服将在核对店铺规则后回复处理进度。"}


def register_scenario_center(app, db):
    router = APIRouter(prefix="/api/scenarios")

    def writer(request, claimed):
        principal = require_role(request, "客服", "主管")
        if app.state.auth.enabled:
            return actor_ref(request, claimed)
        require(claimed in ("客服", "主管"), 403, "当前本地演示角色无此动作权限。")
        return actor_ref(request, claimed)

    def supervisor(request, claimed):
        principal = require_role(request, "主管")
        if app.state.auth.enabled:
            return actor_ref(request, claimed)
        require(claimed == "主管", 403, "当前本地演示角色无此动作权限。")
        return actor_ref(request, claimed)

    def service(request, claimed):
        require_role(request, "客服")
        if app.state.auth.enabled:
            return actor_ref(request, claimed)
        require(claimed == "客服", 403, "当前本地演示角色无此动作权限。")
        return actor_ref(request, claimed)

    def department_actor(request, claimed, department):
        require_role(request, department)
        if app.state.auth.enabled:
            return actor_ref(request, claimed)
        require(claimed == department, 403, "当前本地演示角色无此任务权限。")
        return actor_ref(request, claimed)

    def clean_optional(value):
        value = value.strip()
        require(not value or not contains_private(value), 422, "仅填写脱敏流程信息，不含客户或员工隐私、订单原文和凭据。")
        return value

    @router.get("/state")
    def state(request: Request):
        principal = require_role(request, *ALL_ROLES)
        with db.transaction() as tx:
            cases = sorted(tx.all("scenario_cases"), key=lambda row: row["updated_at"], reverse=True)[:100]
            reviews = {row["id"]: row for row in tx.all("scenario_reviews")}
            test_reviews = sorted(tx.all("scenario_test_reviews"), key=lambda row: row["created_at"], reverse=True)[:200]
            task_plans = sorted(tx.all("scenario_task_plans"), key=lambda row: row["updated_at"], reverse=True)[:100]
        if app.state.auth.enabled and principal.role not in ("客服", "主管"):
            cases = []
            visible = []
            for plan in task_plans:
                if not any(task["department"] == principal.role for task in plan["tasks"]):
                    continue
                item = dict(plan)
                item["tasks"] = [{**task, "summary": task["summary"] if task["department"] == principal.role else "其他部门任务",
                                  "resolution": task["resolution"] if task["department"] == principal.role else ""} for task in plan["tasks"]]
                visible.append(item)
            task_plans = visible
        return {"meta": REGISTRY["meta"], "fact_labels": REGISTRY["fact_labels"], "scenarios": SCENARIOS, "cases": cases,
                "reviews": reviews, "review_summary": {status: sum(row["status"] == status for row in reviews.values())
                for status in ("submitted", "confirmed", "needs_revision", "not_applicable")}, "test_reviews": test_reviews,
                "test_summary": {verdict: sum(row["verdict"] == verdict for row in test_reviews) for verdict in ("correct", "wrong", "unsure")},
                "task_plans": task_plans}

    @router.post("/cases")
    def create_or_update(body: CaseInput, request: Request):
        actor = writer(request, body.actor)
        text = body.text.strip()
        require(bool(text) and not contains_private(text), 422, "请仅提交虚构或已脱敏内容，勿输入联系方式、订单原文或凭据。")
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(exclude={"request_id"}, mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        receipt_id = "scenario-case:" + str(body.request_id)
        with db.transaction() as tx:
            receipt = tx.get("received_events", receipt_id)
            if receipt:
                require(receipt["fingerprint"] == fingerprint, 409, "同一请求标识已用于不同内容。")
                return tx.get("scenario_cases", receipt["case_id"])
            prior = (tx.get("scenario_cases", str(body.case_id)) or tx.get("scenario_cases", body.case_id.hex)) if body.case_id else None
            if body.case_id:
                require(prior is not None, 404, "场景案件不存在。")
                require(prior["revision"] == body.expected_revision, 409, "案件上下文已更新，请刷新后重试。")
            scenario, source, confidence = classify_scene(text, body.scenario_hint_id)
            if prior and prior.get("scene_id"):
                if scenario and scenario["id"] != prior["scene_id"]:
                    scenario, source, confidence = None, "multiple_scenes", 0.0
                elif not scenario:
                    scenario, source, confidence = BY_ID[prior["scene_id"]], "context_carried", prior["confidence"]
            combined = (("\n".join(message["text"] for message in prior["messages"]) + "\n") if prior else "") + text
            result = evaluate(combined, scenario, body.confirmed_facts, source, confidence)
            timestamp = now()
            if prior:
                history = prior["history"] + [{"revision": prior["revision"], "route": prior["route"], "state": prior["state"], "at": prior["updated_at"]}]
                case_id, revision, created_at, messages = prior["id"], prior["revision"] + 1, prior["created_at"], prior["messages"] + [{"text": text, "at": timestamp}]
            else:
                case_id, revision, created_at, messages, history = str(uuid4()), 1, timestamp, [{"text": text, "at": timestamp}], []
            row = {"id": case_id, "revision": revision, "messages": messages, "confirmed_facts": sorted(body.confirmed_facts),
                   "created_at": created_at, "updated_at": timestamp, "history": history, "actor": actor, **result}
            tx.put("scenario_cases", case_id, row)
            tx.put("received_events", receipt_id, {"id": receipt_id, "fingerprint": fingerprint, "case_id": case_id})
            audit_id = uuid4().hex
            tx.put("audit_events", audit_id, {"id": audit_id, "action": "scenario_case_evaluated:" + row["route"], "object_id": case_id, "actor": actor, "at": timestamp})
            return row

    @router.post("/reviews")
    def submit_review(body: ReviewInput, request: Request):
        actor = writer(request, body.actor)
        require(body.scenario_id in BY_ID, 404, "场景不存在。")
        values = {"current_steps": clean_optional(body.current_steps), "current_systems": clean_optional(body.current_systems),
                  "notes": clean_optional(body.notes)}
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(exclude={"request_id"}, mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        receipt_id = "scenario-review:" + str(body.request_id)
        with db.transaction() as tx:
            receipt = tx.get("received_events", receipt_id)
            if receipt:
                require(receipt["fingerprint"] == fingerprint, 409, "同一反馈请求标识已用于不同内容。")
                return tx.get("scenario_reviews", receipt["scenario_id"])
            prior = tx.get("scenario_reviews", body.scenario_id)
            if prior:
                require(body.expected_version == prior["version"], 409, "业务反馈已更新，请刷新后再提交。")
            else:
                require(body.expected_version is None, 409, "当前场景尚无旧版反馈，请刷新后再提交。")
            timestamp = now()
            row = {"id": body.scenario_id, "scenario_name": BY_ID[body.scenario_id]["name"], "domain": BY_ID[body.scenario_id]["domain"],
                   "version": (prior["version"] + 1) if prior else 1, "status": "submitted", "occurrence": body.occurrence,
                   "frequency_30d": body.frequency_30d, **values, "submitted_by": actor, "reviewed_by": None,
                   "review_note": "", "created_at": prior["created_at"] if prior else timestamp, "updated_at": timestamp}
            tx.put("scenario_reviews", row["id"], row)
            tx.put("received_events", receipt_id, {"id": receipt_id, "fingerprint": fingerprint, "scenario_id": row["id"]})
            audit_id = uuid4().hex
            tx.put("audit_events", audit_id, {"id": audit_id, "action": "scenario_review_submitted", "object_id": row["id"], "actor": actor, "at": timestamp})
            return row

    @router.post("/reviews/{scenario_id}/decision")
    def decide_review(scenario_id: str, body: ReviewDecisionInput, request: Request):
        actor = supervisor(request, body.actor)
        note = clean_optional(body.note)
        require(body.decision != "needs_revision" or bool(note), 422, "退回修改时请填写原因。")
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(exclude={"request_id"}, mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        receipt_id = "scenario-review-decision:" + str(body.request_id)
        with db.transaction() as tx:
            receipt = tx.get("received_events", receipt_id)
            if receipt:
                require(receipt["fingerprint"] == fingerprint and receipt["scenario_id"] == scenario_id, 409, "同一确认请求标识已用于不同内容。")
                return tx.get("scenario_reviews", scenario_id)
            row = tx.get("scenario_reviews", scenario_id)
            require(row is not None, 404, "尚无客服业务反馈。")
            require(row["version"] == body.expected_version, 409, "业务反馈已更新，请刷新后确认。")
            require(row["status"] == "submitted", 409, "当前反馈不在待确认状态。")
            row.update(status=body.decision, version=row["version"] + 1, reviewed_by=actor, review_note=note, updated_at=now())
            tx.put("scenario_reviews", row["id"], row)
            tx.put("received_events", receipt_id, {"id": receipt_id, "fingerprint": fingerprint, "scenario_id": scenario_id})
            audit_id = uuid4().hex
            tx.put("audit_events", audit_id, {"id": audit_id, "action": "scenario_review_decision:" + body.decision,
                   "object_id": scenario_id, "actor": actor, "at": row["updated_at"]})
            return row

    @router.post("/case-reviews")
    def review_case(body: TestReviewInput, request: Request):
        actor = writer(request, body.actor)
        note = clean_optional(body.note)
        require(not body.expected_scene_id or body.expected_scene_id in BY_ID, 404, "期望场景不存在。")
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(exclude={"request_id"}, mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        receipt_id = "scenario-test-review:" + str(body.request_id)
        with db.transaction() as tx:
            receipt = tx.get("received_events", receipt_id)
            if receipt:
                require(receipt["fingerprint"] == fingerprint, 409, "同一测试反馈请求标识已用于不同内容。")
                return tx.get("scenario_test_reviews", receipt["review_id"])
            case = tx.get("scenario_cases", str(body.case_id)) or tx.get("scenario_cases", body.case_id.hex)
            require(case is not None, 404, "测试案件不存在。")
            require(case["revision"] == body.expected_case_revision, 409, "案件已追加新上下文，请针对最新版本重新评价。")
            key = case["id"] + ":" + str(case["revision"])
            require(tx.get("scenario_test_reviews", key) is None, 409, "当前案件版本已有评价。")
            expected = BY_ID.get(body.expected_scene_id) if body.expected_scene_id else None
            row = {"id": key, "case_id": case["id"], "case_revision": case["revision"], "predicted_scene_id": case["scene_id"],
                   "verdict": body.verdict, "expected_scene_id": body.expected_scene_id,
                   "expected_scene_name": expected["name"] if expected else None, "note": note, "actor": actor, "created_at": now()}
            tx.put("scenario_test_reviews", key, row)
            tx.put("received_events", receipt_id, {"id": receipt_id, "fingerprint": fingerprint, "review_id": key})
            audit_id = uuid4().hex
            tx.put("audit_events", audit_id, {"id": audit_id, "action": "scenario_test_review:" + body.verdict,
                   "object_id": key, "actor": actor, "at": row["created_at"]})
            return row

    @router.post("/task-plans")
    def create_task_plan(body: TaskPlanInput, request: Request):
        actor = service(request, body.actor)
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(exclude={"request_id"}, mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        receipt_id = "scenario-task-plan:" + str(body.request_id)
        with db.transaction() as tx:
            receipt = tx.get("received_events", receipt_id)
            if receipt:
                require(receipt["fingerprint"] == fingerprint, 409, "同一任务计划请求标识已用于不同内容。")
                return tx.get("scenario_task_plans", receipt["plan_id"])
            case = tx.get("scenario_cases", str(body.case_id)) or tx.get("scenario_cases", body.case_id.hex)
            require(case is not None, 404, "场景案件不存在。")
            require(case["revision"] == body.expected_case_revision, 409, "案件上下文已更新，请按最新版本重新拆分任务。")
            require(not any(plan["case_id"] == case["id"] and plan["case_revision"] == case["revision"] for plan in tx.all("scenario_task_plans")), 409, "当前案件版本已有任务计划。")
            timestamp = now()
            for old in tx.all("scenario_task_plans"):
                if old["case_id"] == case["id"] and old["state"] not in ("closed", "rejected", "superseded"):
                    old.update(state="superseded", version=old["version"] + 1, updated_at=timestamp)
                    old["history"].append({"action": "superseded", "actor": actor, "at": timestamp, "note": "案件已有新版本任务计划。"})
                    tx.put("scenario_task_plans", old["id"], old)
            tasks = []
            for draft in body.tasks:
                summary = clean_optional(draft.summary)
                require(bool(summary), 422, "任务摘要不能为空。")
                tasks.append({"id": str(uuid4()), "key": draft.key, "department": draft.department, "summary": summary,
                              "depends_on": draft.depends_on, "state": "pending_approval", "resolution": "", "history": []})
            plan_id = str(uuid4())
            row = {"id": plan_id, "case_id": case["id"], "case_revision": case["revision"], "scene_id": case["scene_id"],
                   "scene_name": case["scene_name"], "version": 1, "state": "pending_approval", "tasks": tasks,
                   "history": [{"action": "submitted", "actor": actor, "at": timestamp, "note": "等待主管审批核查任务。"}],
                   "created_at": timestamp, "updated_at": timestamp, "close_note": ""}
            tx.put("scenario_task_plans", plan_id, row)
            tx.put("received_events", receipt_id, {"id": receipt_id, "fingerprint": fingerprint, "plan_id": plan_id})
            audit_id = uuid4().hex
            tx.put("audit_events", audit_id, {"id": audit_id, "action": "scenario_task_plan_submitted", "object_id": plan_id, "actor": actor, "at": timestamp})
            return row

    @router.get("/cases/{case_id}/task-plan-suggestion")
    def task_plan_suggestion(case_id: str, request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            case = tx.get("scenario_cases", case_id)
        require(case is not None, 404, "场景案件不存在。")
        pack_id, pack = task_pack_for(case.get("scene_id"))
        if not pack:
            return {"available": False, "case_id": case["id"], "case_revision": case["revision"],
                    "reason": "当前场景尚无已审阅的低风险核查流程包，请由客服手工拆分任务并交主管审批。", "tasks": []}
        if case["missing_facts"]:
            return {"available": False, "case_id": case["id"], "case_revision": case["revision"],
                    "pack_id": pack_id, "reason": "必要事实尚未补齐，不能生成部门核查计划草稿。", "tasks": []}
        return {"available": True, "case_id": case["id"], "case_revision": case["revision"], "pack_id": pack_id,
                "basis": pack["basis"], "requires_supervisor_approval": True,
                "risk_notice": "该案件仍由人工主导；计划只用于内部核查，不授权退款、赔付、补发、采购、调拨、付款或客户回复。",
                "tasks": pack["tasks"]}

    @router.post("/task-plans/{plan_id}/actions")
    def task_plan_action(plan_id: str, body: TaskPlanActionInput, request: Request):
        note = clean_optional(body.note)
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(exclude={"request_id"}, mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        receipt_id = "scenario-task-plan-action:" + str(body.request_id)
        with db.transaction() as tx:
            plan = tx.get("scenario_task_plans", plan_id)
            require(plan is not None, 404, "多部门任务计划不存在。")
            task = next((item for item in plan["tasks"] if item["id"] == body.task_id), None) if body.task_id else None
            if body.action in ("approve", "reject"):
                actor = supervisor(request, body.actor)
            elif body.action in ("return_task", "close"):
                actor = service(request, body.actor)
            else:
                require(task is not None, 404, "部门任务不存在。")
                actor = department_actor(request, body.actor, task["department"])
            receipt = tx.get("received_events", receipt_id)
            if receipt:
                require(receipt["fingerprint"] == fingerprint and receipt["plan_id"] == plan_id, 409, "同一任务动作请求标识已用于不同内容。")
                return tx.get("scenario_task_plans", plan_id)
            require(plan["version"] == body.expected_version, 409, "任务计划已变化，请刷新后重试。")
            case = tx.get("scenario_cases", plan["case_id"])
            require(case is not None and case["revision"] == plan["case_revision"], 409, "案件上下文已更新，旧任务计划已失效。")
            timestamp = now()
            if body.action == "approve":
                require(plan["state"] == "pending_approval", 409, "当前计划不在待审批状态。")
                for item in plan["tasks"]:
                    item["state"] = "blocked" if item["depends_on"] else "waiting"
                plan["state"] = "dispatched"
            elif body.action == "reject":
                require(plan["state"] == "pending_approval", 409, "当前计划不在待审批状态。")
                plan["state"] = "rejected"
            elif body.action == "accept_task":
                require(plan["state"] in ("dispatched", "in_progress"), 409, "任务计划尚未派发或已结束。")
                require(task["state"] == "waiting", 409, "该任务当前不能接单。")
                task["state"] = "working"; plan["state"] = "in_progress"
            elif body.action == "report_task":
                require(plan["state"] in ("dispatched", "in_progress"), 409, "任务计划尚未派发或已结束。")
                require(task["state"] == "working", 409, "该任务尚未接单或已经回传。")
                task.update(state="reported", resolution=note)
                states = {item["key"]: item["state"] for item in plan["tasks"]}
                for item in plan["tasks"]:
                    if item["state"] == "blocked" and all(states[parent] == "reported" for parent in item["depends_on"]):
                        item["state"] = "waiting"
                plan["state"] = "waiting_customer" if all(item["state"] == "reported" for item in plan["tasks"]) else "in_progress"
            elif body.action == "return_task":
                require(plan["state"] == "waiting_customer" and task and task["state"] == "reported", 409, "只能退回已回传且正在等待客服确认的任务。")
                reset = {task["key"]}
                changed = True
                while changed:
                    changed = False
                    for item in plan["tasks"]:
                        if item["key"] not in reset and any(parent in reset for parent in item["depends_on"]):
                            reset.add(item["key"]); changed = True
                for item in plan["tasks"]:
                    if item["key"] in reset:
                        item.update(state="working" if item["id"] == task["id"] else "blocked", resolution="")
                plan["state"] = "in_progress"
            else:
                require(plan["state"] == "waiting_customer" and all(item["state"] == "reported" for item in plan["tasks"]), 409, "仍有部门任务未回传，不能结案。")
                plan.update(state="closed", close_note=note)
            if task and body.action in ("accept_task", "report_task", "return_task"):
                task["history"].append({"action": body.action, "actor": actor, "at": timestamp, "note": note})
            plan.update(version=plan["version"] + 1, updated_at=timestamp)
            plan["history"].append({"action": body.action, "actor": actor, "at": timestamp, "note": note})
            tx.put("scenario_task_plans", plan_id, plan)
            tx.put("received_events", receipt_id, {"id": receipt_id, "fingerprint": fingerprint, "plan_id": plan_id})
            audit_id = uuid4().hex
            tx.put("audit_events", audit_id, {"id": audit_id, "action": "scenario_task_plan:" + body.action, "object_id": plan_id, "actor": actor, "at": timestamp})
            return plan

    app.include_router(router)
