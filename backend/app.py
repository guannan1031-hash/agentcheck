import hashlib
import json
import os
import re
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import SCENARIOS, answer_question
from .model import DeepSeek, ModelUnavailable, model_provider
from .safety import contains_private, risky
from .storage import Database
from .collaboration import register_collaboration
from .auth import COOKIE_NAME, LocalAuth, actor_ref, require_role
from .inventory import register_inventory
from .notifications import register_notifications
from .scenario_center import register_scenario_center
from .quality_api import register_quality
from .taobao import list_synthetic_orders, status as taobao_status, synthetic_order

ROOT = Path(__file__).resolve().parents[1]


def deployment_mode():
    mode = os.environ.get("CS_DEPLOYMENT_MODE", "development").strip().lower()
    if mode not in {"development", "preproduction", "production"}:
        raise ValueError("CS_DEPLOYMENT_MODE 只能是 development、preproduction 或 production。")
    return mode


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require(ok, code, message):
    if not ok:
        raise HTTPException(code, message)


def clean(value):
    value = value.strip()
    require(bool(value) and not contains_private(value), 422, "请仅提交虚构或已脱敏内容，勿输入联系方式、身份信息或凭据。")
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageInput(Input):
    question: str = Field(min_length=1, max_length=1500)
    synthetic_or_redacted: Literal[True]
    event_id: UUID
    conversation_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class PublicFaqInput(Input):
    question: str = Field(min_length=1, max_length=500)


class PublicFaqFeedbackInput(Input):
    helpful: bool


class PilotGoalsInput(Input):
    expected_version: int = Field(ge=1)
    target_case_count: int = Field(ge=1, le=100000)
    baseline_median_minutes: int = Field(ge=0, le=1440)
    target_median_minutes: int = Field(ge=0, le=1440)
    max_overdue_tickets: int = Field(ge=0, le=100000)

    @model_validator(mode="after")
    def target_is_not_slower_than_baseline(self):
        if self.baseline_median_minutes and self.target_median_minutes:
            if self.target_median_minutes > self.baseline_median_minutes:
                raise ValueError("试点目标处理时长不能高于基线。")
        return self


class DraftInput(Input):
    expected_revision: int = Field(ge=1)
    model: Literal["stub", "deepseek", "zhipu"] = "stub"


class EditInput(Input):
    expected_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)
    reason: Literal["事实需要修正", "语气或表达调整", "信息不足需补充", "应转人工", "其他"] = "其他"


class SendInput(Input):
    expected_version: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: UUID


class OutcomeInput(Input):
    expected_version: int = Field(ge=1)
    outcome: Literal["已解决", "待跟进", "需转人工"]
    note: str = Field(default="", max_length=500)


class ControlInput(Input):
    expected_control_revision: int = Field(ge=1)
    mode: Literal["human", "assist"]


class PauseInput(Input):
    paused: bool


class Product(Input):
    sku: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    grams: int = Field(gt=0, le=100000)
    price: str = Field(pattern=r"^\d{1,6}(\.\d{1,2})?$")
    ingredients: str = Field(min_length=1, max_length=500)
    taste: str = Field(max_length=80)
    shelf_life_months: int = Field(gt=0, le=120)
    storage: str = Field(min_length=1, max_length=300)
    batch_date: None = None


class Rule(Input):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    text: str = Field(min_length=1, max_length=600)


class Catalog(Input):
    source: str = Field(min_length=1, max_length=200)
    valid_until: date
    products: list[Product] = Field(min_length=1, max_length=20)
    rules: list[Rule] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique(self):
        if len({p.sku for p in self.products}) != len(self.products) or len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("Duplicate SKU or rule ID")
        return self


class PublishInput(Input):
    expected_version: int = Field(ge=1)
    catalog: Catalog


class KnowledgeTaskInput(Input):
    draft_id: UUID
    expected_draft_version: int = Field(ge=1)
    category: Literal["商品事实", "业务规则", "回复表达", "风险处理", "其他"]
    note: str = Field(min_length=1, max_length=500)


class KnowledgeTaskUpdate(Input):
    expected_version: int = Field(ge=1)
    state: Literal["处理中", "已完成"]
    note: str = Field(default="", max_length=500)


class TicketInput(Input):
    conversation_id: UUID
    expected_revision: int = Field(ge=1)
    owner_role: Literal["售后负责人", "客服主管"]
    due_hours: int = Field(ge=1, le=168)
    priority: Literal["普通", "优先", "紧急"] = "普通"


class TicketUpdate(Input):
    expected_version: int = Field(ge=1)
    state: Literal["处理中", "待外部反馈", "已关闭"]
    resolution: str = Field(default="", max_length=500)


class TicketPriorityInput(Input):
    expected_version: int = Field(ge=1)
    priority: Literal["普通", "优先", "紧急"]
    reason: str = Field(min_length=1, max_length=300)


class ClearPilotDataInput(Input):
    confirmation: Literal["CLEAR_LOCAL_TRIAL_DATA"]


def create_app(database_url=None, model_client=None, auth_accounts=None, notification_connector=None):
    if database_url is None:
        database_url = os.environ.get("CS_DATABASE_URL")
        if not database_url:
            (ROOT / "state/local").mkdir(parents=True, exist_ok=True)
            database_url = "sqlite:///" + str(ROOT / "state/local/customer_service.db")
    mode = deployment_mode()
    default_tenant_id = os.environ.get("CS_DEFAULT_TENANT_ID", "local-demo").strip().lower()
    public_tenant_id = os.environ.get("CS_PUBLIC_TENANT_ID", default_tenant_id).strip().lower()
    db = Database(database_url, default_tenant_id=default_tenant_id)
    tenant_token = db.set_tenant(public_tenant_id)
    db.reset_tenant(tenant_token)
    provider = model_client or DeepSeek()
    auth = LocalAuth(auth_accounts)
    public_faq_calls = deque()
    public_faq_feedback_calls = deque()
    protected_write_calls = {}
    try:
        protected_write_limit = min(1000, max(10, int(os.environ.get("CS_WRITE_RATE_LIMIT_PER_MINUTE", "120"))))
    except ValueError:
        raise ValueError("CS_WRITE_RATE_LIMIT_PER_MINUTE 必须是 10 到 1000 的整数。") from None
    cookie_secure = os.environ.get("CS_COOKIE_SECURE", "").lower() == "true"
    if mode == "production":
        if db.engine.dialect.name != "postgresql":
            raise ValueError("生产模式必须使用 PostgreSQL，不能使用 SQLite。")
        if not cookie_secure:
            raise ValueError("生产模式必须设置 CS_COOKIE_SECURE=true。")
        if auth_accounts is None:
            raise ValueError("生产模式必须提供受控身份来源，不能使用未登录的演示模式。")
    app = FastAPI(title="Food CS Local Development", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.auth = auth
    app.state.default_tenant_id = default_tenant_id
    app.state.deployment_mode = mode
    public_host = os.environ.get("CS_DEMO_PUBLIC_HOST")
    if public_host and not re.fullmatch(r"[a-z0-9-]+\.(?:loca\.lt|trycloudflare\.com)", public_host):
        raise ValueError("临时演示域名格式无效。")
    if mode == "production" and public_host:
        raise ValueError("生产模式不能使用 CS_DEMO_PUBLIC_HOST 临时演示域名。")
    faq_host = os.environ.get("CS_PUBLIC_FAQ_HOST", "").strip().lower()
    if faq_host and not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", faq_host):
        raise ValueError("公开 FAQ 域名格式无效。")
    allowed_hosts = ["127.0.0.1", "localhost", "testserver"] + ([public_host] if public_host else []) + ([faq_host] if faq_host else [])
    allowed_origins = {"http://127.0.0.1:8878", "http://localhost:8878", "http://127.0.0.1:5173", "http://localhost:5173"}
    if public_host:
        allowed_origins.add("https://" + public_host)
    if faq_host:
        allowed_origins.add("https://" + faq_host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    def harden_response(response, path):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if path == "/customer":
            response.headers["Content-Security-Policy"] = "default-src 'self'; base-uri 'none'; object-src 'none'; form-action 'self'; frame-ancestors 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'"
        else:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "default-src 'self'; base-uri 'none'; object-src 'none'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'"
        if cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    def limit_authenticated_write(tenant_id, principal):
        calls = protected_write_calls.setdefault(tenant_id + ":" + principal.user_ref, deque())
        timestamp = time.monotonic()
        while calls and timestamp - calls[0] >= 60:
            calls.popleft()
        if len(calls) >= protected_write_limit:
            return False
        calls.append(timestamp)
        return True

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        tenant_id = public_tenant_id
        token = None
        try:
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                origin_parts = urlparse(origin) if origin else None
                same_origin = bool(origin_parts and origin_parts.scheme in {"http", "https"} and origin_parts.netloc == request.headers.get("host"))
                if origin and origin not in allowed_origins and not same_origin:
                    return harden_response(JSONResponse({"detail": "跨来源写入已阻止。"}, status_code=403), request.url.path)
            if auth.enabled and request.url.path.startswith("/api/") and request.url.path not in {"/api/auth/status", "/api/auth/login", "/api/public/faq", "/api/public/faq/config", "/api/public/faq/feedback"}:
                principal = auth.principal(request)
                if not principal:
                    return harden_response(JSONResponse({"detail": "请先登录。"}, status_code=401), request.url.path)
                request.state.principal = principal
                tenant_id = principal.tenant_id
                if request.method not in ("GET", "HEAD", "OPTIONS") and not auth.csrf_valid(request):
                    return harden_response(JSONResponse({"detail": "登录会话校验失败，请刷新后重试。"}, status_code=403), request.url.path)
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    if not limit_authenticated_write(tenant_id, principal):
                        return harden_response(JSONResponse({"detail": "当前账号操作过于频繁，请稍后再试。"}, status_code=429), request.url.path)
            if request.url.path.startswith("/api/"):
                bootstrap_tenant(tenant_id)
                token = db.set_tenant(tenant_id)
            response = await call_next(request)
            return harden_response(response, request.url.path)
        finally:
            if token is not None:
                db.reset_tenant(token)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "输入格式不符合要求，请检查必填项、长度和数据类型。"}, status_code=422)

    def get(tx, table, key):
        item = tx.get(table, str(key))
        require(item is not None, 404, "记录不存在。")
        return item

    def audit(tx, action, object_id, request=None):
        key = uuid4().hex
        actor = actor_ref(request) if request and auth.enabled else "local-developer"
        tx.put("audit_events", key, {"id": key, "action": action, "object_id": object_id, "at": now(), "actor": actor})

    def public_faq_configuration():
        brand = os.environ.get("CS_PUBLIC_FAQ_BRAND", "商品客服").strip()[:60] or "商品客服"
        handoff_url = os.environ.get("CS_PUBLIC_HUMAN_CONTACT_URL", "").strip()
        parsed = urlparse(handoff_url)
        return {"brand": brand, "handoff_url": handoff_url if parsed.scheme == "https" and parsed.netloc else None}

    if mode == "production" and os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true":
        if not faq_host or not public_faq_configuration()["handoff_url"]:
            raise ValueError("生产公开 FAQ 必须配置 CS_PUBLIC_FAQ_HOST 和 HTTPS 人工接管地址。")

    def record_public_faq_metric(tx, metric):
        key = date.today().isoformat()
        row = tx.get("public_faq_daily", key) or {"date": key, "answered": 0, "handoffs": 0, "helpful": 0, "not_helpful": 0}
        row[metric] += 1
        tx.put("public_faq_daily", key, row)

    def limit_public_calls(calls, maximum, message):
        timestamp = time.monotonic()
        while calls and timestamp - calls[0] >= 60:
            calls.popleft()
        require(len(calls) < maximum, 429, message)
        calls.append(timestamp)

    def pilot_readiness(catalog):
        faq_enabled = os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true"
        handoff_ready = bool(public_faq_configuration()["handoff_url"])
        connector = taobao_status()
        return [
            {"id": "knowledge", "label": "商品知识有效期", "status": "ready" if date.fromisoformat(catalog["valid_until"]) >= date.today() else "attention",
             "detail": "当前商品知识可用于试点" if date.fromisoformat(catalog["valid_until"]) >= date.today() else "商品知识已过期，发布更新后再生成或发送草稿。"},
            {"id": "public_faq", "label": "匿名商品 FAQ", "status": "ready" if faq_enabled and handoff_ready else "optional" if not faq_enabled else "attention",
             "detail": "已启用并提供 HTTPS 人工接管渠道。" if faq_enabled and handoff_ready else "尚未启用；仅在商家官网需要公开商品咨询时配置。" if not faq_enabled else "已启用但未配置 HTTPS 人工客服链接。"},
            {"id": "taobao_tmall", "label": "淘宝/天猫官方接入", "status": "attention" if connector["configured"] else "blocked",
             "detail": "已具备应用配置，仍需商家 OAuth 授权、获批权限和店铺联调。" if connector["configured"] else "缺少官方应用配置；未使用 Cookie、私有接口或绕过平台限制。"},
            {"id": "consumer_send", "label": "真实消费者发送", "status": "manual_control",
             "detail": "当前保持关闭；需在获权咨询入口和人工审核流程完成验收后单独启用。"},
        ]

    def pilot_goals(tx):
        return tx.get("store_meta", "pilot_goals") or {"version": 1, "target_case_count": 30,
            "baseline_median_minutes": 0, "target_median_minutes": 0, "max_overdue_tickets": 0}

    def fresh(tx, draft, conv, meta):
        return (conv["current_draft_id"] == draft["id"] and draft["message_revision"] == conv["revision"]
                and draft["control_revision"] == conv["control_revision"] and draft["knowledge_version"] == meta["knowledge_version"])

    bootstrapped_tenants = set()

    def bootstrap_tenant(tenant_id):
        if tenant_id in bootstrapped_tenants:
            return
        with db.transaction(tenant_id) as tx:
            meta = tx.get("store_meta", "demo")
            if meta is None:
                meta = {"schema_version": 1, "knowledge_version": 0, "paused": False, "model_calls": 0}
                tx.put("store_meta", "demo", meta)
            if meta["knowledge_version"] == 0:
                catalog = json.loads((ROOT / "demo-data/catalog.json").read_text(encoding="utf-8"))
                catalog["valid_until"] = (date.today() + timedelta(days=365)).isoformat()
                tx.put("knowledge_versions", "1", {"version": 1, "catalog": catalog, "published_at": now()})
                meta["knowledge_version"] = 1
                tx.put("store_meta", "demo", meta)
        bootstrapped_tenants.add(tenant_id)

    bootstrap_tenant(default_tenant_id)

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        principal = auth.principal(request) if auth.enabled else None
        return {"enabled": auth.enabled, "authenticated": bool(principal),
                "user_ref": principal.user_ref if principal else None, "role": principal.role if principal else None,
                "tenant_id": principal.tenant_id if principal else None, "csrf": auth.csrf(request) if principal else None}

    @app.get("/healthz")
    def healthz():
        with db.transaction() as tx:
            meta = get(tx, "store_meta", "demo")
            return {"ok": True, "storage": db.engine.dialect.name, "knowledge_version": meta["knowledge_version"],
                    "public_faq_enabled": os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true"}

    @app.get("/readyz")
    def readyz():
        with db.transaction() as tx:
            get(tx, "store_meta", "demo")
        return {"ok": True}

    class LoginInput(Input):
        user_ref: str = Field(min_length=1, max_length=64)
        password: str = Field(min_length=1, max_length=256)

    @app.post("/api/auth/login")
    def login(body: LoginInput, response: Response):
        require(auth.enabled, 409, "当前运行方式未启用登录。")
        result = auth.login(body.user_ref, body.password)
        require(result is not None, 401, "账号或口令不正确。")
        token, csrf, principal = result
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=cookie_secure, max_age=auth.session_seconds, path="/api")
        return {"enabled": True, "authenticated": True, "user_ref": principal.user_ref, "role": principal.role, "tenant_id": principal.tenant_id, "csrf": csrf}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        auth.logout(request)
        response.delete_cookie(COOKIE_NAME, path="/api")
        return {"ok": True}

    @app.get("/api/state")
    def snapshot(request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            meta = get(tx, "store_meta", "demo")
            catalog = get(tx, "knowledge_versions", str(meta["knowledge_version"]))["catalog"]
            convs = tx.all("conversations")
            drafts = tx.all("drafts")
            edits = [draft for draft in drafts if draft.get("edited")]
            edit_reasons = {reason: sum(draft.get("edit_reason") == reason for draft in edits) for reason in ("事实需要修正", "语气或表达调整", "信息不足需补充", "应转人工", "其他")}
            sends = tx.all("send_records")
            outcomes = {outcome: sum(send.get("outcome") == outcome for send in sends) for outcome in ("已解决", "待跟进", "需转人工")}
            quality = {"drafts_generated": len(drafts), "drafts_edited": len(edits), "edit_reasons": edit_reasons,
                       "human_takeovers": sum(conv["mode"] == "human" for conv in convs),
                       "blocked_drafts": sum(draft.get("state") == "blocked" for draft in drafts),
                       "sent_replies": len(sends), "outcomes": outcomes}
            for conv in convs:
                draft = tx.get("drafts", conv["current_draft_id"]) if conv["current_draft_id"] else None
                if draft and not fresh(tx, draft, conv, meta):
                    draft["state"] = "stale"
                conv["draft"] = draft
                conv["send_records"] = [s for s in tx.all("send_records") if s["conversation_id"] == conv["id"]]
            return {"mode": app.state.deployment_mode, "storage": db.engine.dialect.name, "paused": meta["paused"], "model_configured": bool(os.environ.get("CS_MODEL_API_KEY")), "model_provider": model_provider(),
                    "model_calls": meta["model_calls"], "knowledge": get(tx, "knowledge_versions", str(meta["knowledge_version"])), "pilot_readiness": pilot_readiness(catalog), "quality": quality,
                    "pilot_goals": pilot_goals(tx),
                    "public_faq": {"enabled": os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true", "customer_path": "/customer", "embed_path": "/customer/embed.js"},
                    "public_faq_feedback": meta.get("public_faq_feedback", {"helpful": 0, "not_helpful": 0, "handoffs": 0}),
                    "public_faq_daily": sorted(tx.all("public_faq_daily"), key=lambda row: row["date"], reverse=True)[:14],
                    "conversations": sorted(convs, key=lambda c: c["updated_at"], reverse=True), "tickets": tx.all("tickets"),
                    "knowledge_tasks": sorted(tx.all("knowledge_tasks"), key=lambda task: task["created_at"], reverse=True),
                    "audit": sorted(tx.all("audit_events"), key=lambda event: event["at"], reverse=True)[:50], "scenarios": SCENARIOS}

    @app.put("/api/pilot/goals")
    def update_pilot_goals(body: PilotGoalsInput, request: Request):
        require_role(request, "主管")
        with db.transaction() as tx:
            current = pilot_goals(tx)
            require(current["version"] == body.expected_version, 409, "试点目标已变化，请刷新后再保存。")
            result = {**body.model_dump(exclude={"expected_version", "actor"}), "version": current["version"] + 1}
            tx.put("store_meta", "pilot_goals", result)
            audit(tx, "pilot_goals_updated", "pilot_goals", request)
            return result

    @app.delete("/api/pilot/data")
    def clear_pilot_data(body: ClearPilotDataInput, request: Request):
        require_role(request, "主管")
        tables = ("conversations", "drafts", "send_records", "tickets", "received_events", "knowledge_tasks", "public_faq_daily",
                  "inventory_snapshots", "inventory_evaluations", "notification_outbox", "damage_cases", "scenario_cases", "scenario_reviews",
                  "scenario_test_reviews", "scenario_task_plans")
        with db.transaction() as tx:
            cleared = {table: len(tx.all(table)) for table in tables}
            for table in tables:
                tx.clear(table)
            meta = get(tx, "store_meta", "demo")
            meta.update(model_calls=0, public_faq_feedback={"helpful": 0, "not_helpful": 0, "handoffs": 0})
            tx.put("store_meta", "demo", meta)
            audit(tx, "pilot_data_cleared", "pilot_data", request)
            return {"ok": True, "cleared": cleared, "knowledge_version": meta["knowledge_version"]}

    @app.get("/api/reports/pilot-summary")
    def pilot_summary(request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            report_at = now()
            meta = get(tx, "store_meta", "demo")
            conversations = tx.all("conversations")
            drafts = tx.all("drafts")
            sends = tx.all("send_records")
            tasks = tx.all("knowledge_tasks")
            outcomes = {name: sum(record.get("outcome") == name for record in sends) for name in ("已解决", "待跟进", "需转人工")}
            priorities = {name: sum(row.get("priority", "普通") == name and row.get("state") != "已关闭" for row in tx.all("tickets")) for name in ("紧急", "优先", "普通")}
            overdue_tickets = sum(bool(row.get("due_at")) and row["due_at"] < report_at and row.get("state") != "已关闭" for row in tx.all("tickets"))
            goals = pilot_goals(tx)
            latest_outcomes = {}
            for record in sends:
                if record.get("outcome_at") and (record["conversation_id"] not in latest_outcomes or record["outcome_at"] > latest_outcomes[record["conversation_id"]]["outcome_at"]):
                    latest_outcomes[record["conversation_id"]] = record
            cycles = []
            for conversation in conversations:
                result = latest_outcomes.get(conversation["id"])
                messages = conversation.get("messages", [])
                if not result or result.get("outcome") != "已解决" or not messages or not messages[0].get("at"):
                    continue
                try:
                    cycles.append(max(0, (datetime.fromisoformat(result["outcome_at"]) - datetime.fromisoformat(messages[0]["at"])).total_seconds() / 60))
                except ValueError:
                    continue
            cycle_median = round(median(cycles), 2) if cycles else None
            return {
                "generated_at": report_at,
                "scope": "本地单店试点汇总，仅含合成或脱敏数据的聚合数量",
                "knowledge_version": meta["knowledge_version"],
                "goals": goals,
                "operations": {
                    "cases": len(conversations), "drafts_generated": len(drafts), "simulated_replies": len(sends),
                    "human_takeovers": sum(row["mode"] == "human" for row in conversations), "blocked_drafts": sum(row.get("state") == "blocked" for row in drafts),
                    "overdue_tickets": overdue_tickets,
                    "open_ticket_priorities": priorities,
                    "outcomes": outcomes,
                    "resolved_case_cycle": {"cases": len(cycles), "median_minutes": cycle_median,
                                            "basis": "从首条合成或脱敏咨询到客服标注已解决的本地流程历时，不等同于客服主动操作时长"},
                },
                "goal_checks": {
                    "case_volume": {"actual": len(conversations), "target": goals["target_case_count"], "met": len(conversations) >= goals["target_case_count"]},
                    "overdue_tickets": {"actual": overdue_tickets, "maximum": goals["max_overdue_tickets"], "met": overdue_tickets <= goals["max_overdue_tickets"]},
                },
                "knowledge_follow_up": {"open": sum(row["state"] != "已完成" for row in tasks), "completed": sum(row["state"] == "已完成" for row in tasks)},
                "public_faq": {
                    "enabled": os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true",
                    "feedback": meta.get("public_faq_feedback", {"helpful": 0, "not_helpful": 0, "handoffs": 0}),
                    "daily": sorted(tx.all("public_faq_daily"), key=lambda row: row["date"], reverse=True)[:14],
                },
            }

    @app.post("/api/messages")
    def receive(body: MessageInput, request: Request):
        require_role(request, "客服", "主管")
        question = clean(body.question)
        fingerprint = digest(json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False))
        with db.transaction() as tx:
            prior = tx.get("received_events", str(body.event_id))
            if prior:
                require(prior["fingerprint"] == fingerprint, 409, "事件标识已用于其他内容。")
                return {"conversation_id": prior["conversation_id"], "duplicate": True}
            if body.conversation_id:
                conv = get(tx, "conversations", str(body.conversation_id))
                require(body.expected_revision == conv["revision"], 409, "会话已更新，请刷新。")
                conv["revision"] += 1
            else:
                require(len(tx.all("conversations")) < 500, 409, "本地验证库已达到500个会话上限。")
                conv = {"id": str(uuid4()), "revision": 1, "control_revision": 1, "mode": "assist", "messages": [], "current_draft_id": None, "last_sent_revision": None}
            conv["messages"].append({"text": question, "at": now()})
            require(len(conv["messages"]) <= 50, 409, "本地验证会话最多50条消息，请新建会话。")
            conv["updated_at"] = now()
            tx.put("conversations", conv["id"], conv)
            tx.put("received_events", str(body.event_id), {"fingerprint": fingerprint, "conversation_id": conv["id"]})
            audit(tx, "message_received", conv["id"], request)
            return {"conversation_id": conv["id"], "duplicate": False}

    @app.post("/api/public/faq")
    def public_faq(body: PublicFaqInput):
        require(os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true", 404, "匿名商品咨询尚未启用。")
        limit_public_calls(public_faq_calls, 40, "当前咨询较多，请稍后再试。")
        question = clean(body.question)
        with db.transaction() as tx:
            meta = get(tx, "store_meta", "demo")
            catalog = get(tx, "knowledge_versions", str(meta["knowledge_version"]))["catalog"]
            result = answer_question(question, catalog, meta["knowledge_version"])
        allowed = result["state"] in {"reviewable", "needs_clarification"} and bool(result["evidence"])
        if not allowed:
            with db.transaction() as tx:
                current_meta = get(tx, "store_meta", "demo")
                metrics = current_meta.setdefault("public_faq_feedback", {"helpful": 0, "not_helpful": 0, "handoffs": 0})
                metrics.setdefault("handoffs", 0)
                metrics["handoffs"] += 1
                record_public_faq_metric(tx, "handoffs")
                tx.put("store_meta", "demo", current_meta)
            return {"state": "human_handoff", "text": "这类问题需要人工客服核实。此页面仅解答公开商品信息，不处理订单、物流、退款、投诉、食品安全或个人信息。", "evidence": [], "handoff_url": public_faq_configuration()["handoff_url"]}
        with db.transaction() as tx:
            record_public_faq_metric(tx, "answered")
        return {"state": result["state"], "text": result["text"], "evidence": result["evidence"], "missing": result["missing"],
                "knowledge_version": meta["knowledge_version"], "source": "本地商品知识与规则"}

    @app.get("/api/public/faq/config")
    def public_faq_config():
        require(os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true", 404, "匿名商品咨询尚未启用。")
        return public_faq_configuration()

    @app.post("/api/public/faq/feedback")
    def public_faq_feedback(body: PublicFaqFeedbackInput):
        require(os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true", 404, "匿名商品咨询尚未启用。")
        limit_public_calls(public_faq_feedback_calls, 80, "当前反馈较多，请稍后再试。")
        with db.transaction() as tx:
            meta = get(tx, "store_meta", "demo")
            metrics = meta.setdefault("public_faq_feedback", {"helpful": 0, "not_helpful": 0, "handoffs": 0})
            metrics.setdefault("handoffs", 0)
            metrics["helpful" if body.helpful else "not_helpful"] += 1
            record_public_faq_metric(tx, "helpful" if body.helpful else "not_helpful")
            tx.put("store_meta", "demo", meta)
            return {"ok": True, "metrics": metrics}

    @app.get("/customer/embed.js")
    def public_faq_embed():
        require(os.environ.get("CS_PUBLIC_FAQ_ENABLED", "").lower() == "true", 404, "匿名商品咨询尚未启用。")
        script = """(function(){
var tag=document.currentScript,base=new URL(tag.src).origin,open=false;
var button=document.createElement('button'),frame=document.createElement('iframe');
button.textContent='商品咨询';button.setAttribute('aria-label','打开商品咨询');
button.style.cssText='position:fixed;right:24px;bottom:24px;z-index:2147483647;border:0;border-radius:999px;padding:13px 18px;background:#087e70;color:#fff;font:14px sans-serif;cursor:pointer;box-shadow:0 6px 20px #0003';
frame.src=base+'/customer';frame.title='商品咨询';frame.style.cssText='display:none;position:fixed;right:20px;bottom:78px;width:min(390px,calc(100vw - 32px));height:min(620px,calc(100vh - 110px));z-index:2147483647;border:1px solid #d9e3df;border-radius:14px;background:#fff;box-shadow:0 14px 40px #0003';
button.onclick=function(){open=!open;frame.style.display=open?'block':'none';button.textContent=open?'关闭咨询':'商品咨询'};
document.body.appendChild(frame);document.body.appendChild(button);
})();"""
        return Response(content=script, media_type="application/javascript")

    @app.post("/api/conversations/{conversation_id}/drafts")
    def draft(conversation_id: UUID, body: DraftInput, request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            conv = get(tx, "conversations", str(conversation_id))
            meta = get(tx, "store_meta", "demo")
            require(body.expected_revision == conv["revision"], 409, "会话已更新，请刷新。")
            require(conv["mode"] == "assist" and not meta["paused"], 409, "人工接管或全局暂停中。")
            catalog = get(tx, "knowledge_versions", str(meta["knowledge_version"]))["catalog"]
            question = conv["messages"][-1]["text"]
            context_risk = any(risky(m["text"]) for m in conv["messages"])
            result = answer_question("食品品质投诉要求赔偿" if context_risk else question, catalog, meta["knowledge_version"])
            wants_model = body.model != "stub" and result["state"] != "blocked" and bool(result["evidence"]) and "当前发货批次" not in result["missing"]
            if wants_model:
                require(body.model == model_provider(), 409, "模型选择已变更，请刷新页面。")
                require(bool(os.environ.get("CS_MODEL_API_KEY")), 409, "尚未配置模型，请使用模拟模式。")
                require(meta["model_calls"] < int(os.environ.get("CS_MODEL_CALL_LIMIT", "50")), 429, "已达到本地模型调用上限。")
                meta["model_calls"] += 1
                tx.put("store_meta", "demo", meta)
        # Slow provider call happens outside the database transaction; freshness is rechecked below.
        if wants_model:
            try:
                output, usage = provider.complete(question, result["evidence"], result["calculations"])
                valid_ids = {e["label"] for e in result["evidence"]}
                grounded = bool(output["evidence_ids"]) and set(output["evidence_ids"]).issubset(valid_ids)
                allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", json.dumps(result["evidence"] + result["calculations"], ensure_ascii=False)))
                numbers_ok = set(re.findall(r"\d+(?:\.\d+)?", output["answer"])).issubset(allowed_numbers)
                safe = grounded and numbers_ok and not contains_private(output["answer"]) and not risky(output["answer"]) and not output["needs_human"]
                result.update(model=model_provider(), usage=usage)
                if safe:
                    result.update(text=output["answer"], state="reviewable", missing=[])
                else:
                    result.update(state="blocked", text="模型回复未通过证据或风险检查，请人工核实。", risk_code="MODEL_OUTPUT_REJECTED")
            except ModelUnavailable:
                result.update(model=model_provider(), state="failed", text="模型调用失败或响应异常，请稍后重试或转人工。", risk_code="MODEL_UNAVAILABLE")
        record = {**result, "id": str(uuid4()), "conversation_id": conv["id"], "message_revision": conv["revision"], "control_revision": conv["control_revision"], "knowledge_version": meta["knowledge_version"], "version": 1, "created_at": now()}
        record["content_hash"] = digest(record["text"])
        with db.transaction() as tx:
            current = get(tx, "conversations", conv["id"])
            current_meta = get(tx, "store_meta", "demo")
            if current["revision"] != conv["revision"] or current["control_revision"] != conv["control_revision"] or current_meta["knowledge_version"] != meta["knowledge_version"] or current_meta["paused"]:
                record["state"] = "stale"
            current["current_draft_id"] = record["id"]
            tx.put("drafts", record["id"], record)
            tx.put("conversations", current["id"], current)
            audit(tx, "draft_generated:" + record["model"], record["id"], request)
        return record

    @app.patch("/api/drafts/{draft_id}")
    def edit(draft_id: UUID, body: EditInput, request: Request):
        require_role(request, "客服", "主管")
        text = clean(body.text)
        with db.transaction() as tx:
            record = get(tx, "drafts", str(draft_id))
            conv = get(tx, "conversations", record["conversation_id"])
            meta = get(tx, "store_meta", "demo")
            require(fresh(tx, record, conv, meta) and record["version"] == body.expected_version, 409, "草稿已变化，请重新生成或刷新。")
            require(record["state"] == "reviewable" and not any(risky(m["text"]) for m in conv["messages"]), 422, "风险或追问草稿不可改写为结论；请转人工或补齐信息后重生成。")
            require(not risky(text), 422, "修改内容涉及风险，请转人工。")
            record.update(text=text, content_hash=digest(text), version=record["version"] + 1, edited=True, edit_reason=body.reason)
            tx.put("drafts", record["id"], record)
            audit(tx, "draft_edited:" + body.reason, record["id"], request)
            return record

    @app.post("/api/drafts/{draft_id}/approve-send")
    def send(draft_id: UUID, body: SendInput, request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            record = get(tx, "drafts", str(draft_id))
            prior = tx.get("send_records", record["id"])
            if prior:
                require(prior["content_hash"] == body.content_hash and prior["draft_version"] == body.expected_version, 409, "该草稿已发送其他版本。")
                return prior
            require(not any(s["idempotency_key"] == str(body.idempotency_key) for s in tx.all("send_records")), 409, "幂等标识已被使用。")
            conv = get(tx, "conversations", record["conversation_id"])
            meta = get(tx, "store_meta", "demo")
            require(not meta["paused"] and conv["mode"] == "assist", 409, "人工接管或全局暂停中，禁止发送。")
            require(fresh(tx, record, conv, meta) and record["version"] == body.expected_version and record["content_hash"] == body.content_hash, 409, "草稿或上下文已变化，请重新审核。")
            require(record["state"] in ("reviewable", "needs_clarification"), 422, "风险、失败或失效草稿不可发送。")
            require(not any(risky(m["text"]) for m in conv["messages"]) and not risky(record["text"]), 422, "风险会话必须转人工。")
            require(conv["last_sent_revision"] != conv["revision"], 409, "当前消息已有已发送回复，禁止重复发送。")
            catalog = get(tx, "knowledge_versions", str(meta["knowledge_version"]))["catalog"]
            require(date.fromisoformat(catalog["valid_until"]) >= date.today(), 409, "知识已过期。")
            sent = {"id": record["id"], "conversation_id": conv["id"], "content_hash": record["content_hash"], "draft_version": record["version"], "idempotency_key": str(body.idempotency_key), "status": "simulated_sent", "text": record["text"], "at": now(), "version": 1, "outcome": None, "outcome_note": ""}
            conv["last_sent_revision"] = conv["revision"]
            record["state"] = "sent"
            tx.put("send_records", record["id"], sent)
            tx.put("conversations", conv["id"], conv)
            tx.put("drafts", record["id"], record)
            audit(tx, "simulated_send", record["id"], request)
            return sent

    @app.patch("/api/send-records/{send_id}/outcome")
    def update_outcome(send_id: UUID, body: OutcomeInput, request: Request):
        require_role(request, "客服", "主管")
        note = clean(body.note) if body.note else ""
        with db.transaction() as tx:
            sent = get(tx, "send_records", str(send_id))
            require(sent.get("status") == "simulated_sent", 409, "仅可标注本地模拟发送记录。")
            require(sent.get("version", 1) == body.expected_version, 409, "接待结果已变化，请刷新。")
            require(body.outcome != "已解决" or bool(note), 422, "标注已解决前请填写脱敏核对说明。")
            sent.update(outcome=body.outcome, outcome_note=note, outcome_at=now(), version=sent.get("version", 1) + 1)
            tx.put("send_records", sent["id"], sent)
            audit(tx, "send_outcome:" + body.outcome, sent["id"], request)
            return sent

    @app.post("/api/conversations/{conversation_id}/control")
    def control(conversation_id: UUID, body: ControlInput, request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            conv = get(tx, "conversations", str(conversation_id))
            require(conv["control_revision"] == body.expected_control_revision, 409, "接管状态已变化，请刷新。")
            conv.update(mode=body.mode, control_revision=conv["control_revision"] + 1)
            tx.put("conversations", conv["id"], conv)
            audit(tx, "control:" + body.mode, conv["id"], request)
        return {"ok": True}

    @app.post("/api/pause")
    def pause(body: PauseInput, request: Request):
        require_role(request, "主管")
        with db.transaction() as tx:
            meta = get(tx, "store_meta", "demo")
            meta["paused"] = body.paused
            tx.put("store_meta", "demo", meta)
            for conv in tx.all("conversations"):
                conv["control_revision"] += 1
                tx.put("conversations", conv["id"], conv)
            audit(tx, "pause:" + str(body.paused), "demo", request)
        return {"ok": True}

    @app.post("/api/knowledge/publish")
    def publish(body: PublishInput, request: Request):
        require_role(request, "主管")
        catalog = body.catalog.model_dump(mode="json")
        clean(json.dumps(catalog, ensure_ascii=False))
        require(body.catalog.valid_until >= date.today(), 422, "不能发布已过期的知识。")
        with db.transaction() as tx:
            meta = get(tx, "store_meta", "demo")
            require(meta["knowledge_version"] == body.expected_version, 409, "知识已有新版本，请重新加载后修改。")
            meta["knowledge_version"] += 1
            tx.put("knowledge_versions", str(meta["knowledge_version"]), {"version": meta["knowledge_version"], "catalog": catalog, "published_at": now()})
            tx.put("store_meta", "demo", meta)
            audit(tx, "knowledge_published", str(meta["knowledge_version"]), request)
        return {"version": meta["knowledge_version"]}

    @app.get("/api/knowledge/search")
    def search_knowledge(request: Request, query: str = Query(min_length=1, max_length=120)):
        require_role(request, "客服", "主管")
        query = clean(query)
        normalized = re.sub(r"\s+", "", query).lower()

        def score(text):
            haystack = re.sub(r"\s+", "", text).lower()
            exact = 100 if normalized in haystack else 0
            overlap = sum(1 for char in set(normalized) if char in haystack)
            return exact + overlap

        with db.transaction() as tx:
            meta = get(tx, "store_meta", "demo")
            catalog = get(tx, "knowledge_versions", str(meta["knowledge_version"]))["catalog"]
            rows = []
            for product in catalog["products"]:
                text = " ".join(str(product.get(field, "")) for field in ("sku", "name", "ingredients", "taste", "storage"))
                value = score(text)
                if value >= 2:
                    rows.append({"kind": "商品资料", "title": product["name"], "content": f"{product['sku']} · {product['grams']}g · 展示价 {product['price']} · {product['storage']}", "score": value})
            for rule in catalog["rules"]:
                value = score(rule["id"] + " " + rule["text"])
                if value >= 2:
                    rows.append({"kind": "业务规则", "title": rule["id"], "content": rule["text"], "score": value})
            for task in tx.all("knowledge_tasks"):
                value = score(task["category"] + " " + task["note"])
                if value >= 2:
                    rows.append({"kind": "知识待办", "title": task["category"] + " · " + task["state"], "content": task["note"], "score": value})
            return {"query": query, "knowledge_version": meta["knowledge_version"], "results": sorted(rows, key=lambda row: (-row["score"], row["kind"], row["title"]))[:12]}

    @app.post("/api/knowledge/tasks")
    def create_knowledge_task(body: KnowledgeTaskInput, request: Request):
        require_role(request, "客服", "主管")
        note = clean(body.note)
        with db.transaction() as tx:
            draft = get(tx, "drafts", str(body.draft_id))
            require(draft["version"] == body.expected_draft_version, 409, "草稿已变化，请刷新后再登记。")
            prior = next((task for task in tx.all("knowledge_tasks") if task["draft_id"] == draft["id"] and task["category"] == body.category and task["note"] == note), None)
            if prior:
                return prior
            task = {"id": str(uuid4()), "draft_id": draft["id"], "draft_version": draft["version"], "category": body.category,
                    "note": note, "state": "待补充", "version": 1, "created_at": now(), "updated_at": now(), "resolution": ""}
            tx.put("knowledge_tasks", task["id"], task)
            audit(tx, "knowledge_task_created:" + body.category, task["id"], request)
            return task

    @app.patch("/api/knowledge/tasks/{task_id}")
    def update_knowledge_task(task_id: UUID, body: KnowledgeTaskUpdate, request: Request):
        require_role(request, "主管")
        resolution = clean(body.note) if body.note else ""
        with db.transaction() as tx:
            task = get(tx, "knowledge_tasks", str(task_id))
            require(task["version"] == body.expected_version, 409, "知识待办已变化，请刷新。")
            require((task["state"] == "待补充" and body.state == "处理中") or (task["state"] == "处理中" and body.state == "已完成"), 409, "不允许该待办状态跳转。")
            require(body.state != "已完成" or bool(resolution), 422, "完成前请填写脱敏处理说明。")
            task.update(state=body.state, resolution=resolution, version=task["version"] + 1, updated_at=now())
            tx.put("knowledge_tasks", task["id"], task)
            audit(tx, "knowledge_task_updated:" + body.state, task["id"], request)
            return task

    @app.post("/api/tickets")
    def ticket(body: TicketInput, request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            conv = get(tx, "conversations", str(body.conversation_id))
            require(conv["revision"] == body.expected_revision, 409, "会话已更新，请刷新后建单。")
            key = f'{conv["id"]}:{conv["revision"]}'
            prior = tx.get("tickets", key)
            if prior:
                require(prior.get("workflow") != "collaboration", 409, "此消息已进入协作工单，请在协作工单处理。")
                return prior
            risk = any(risky(m["text"]) for m in conv["messages"])
            row = {"id": key, "conversation_id": conv["id"], "summary": "食品/售后风险咨询，待人工核实。" if risk else "商品资料或服务问题，待人工跟进。", "owner_role": body.owner_role, "priority": body.priority, "due_at": (datetime.now(timezone.utc) + timedelta(hours=body.due_hours)).isoformat(), "state": "待处理", "version": 1, "resolution": "", "created_at": now()}
            tx.put("tickets", key, row)
            audit(tx, "ticket_created", key, request)
            return row

    @app.patch("/api/tickets/{ticket_id}")
    def update_ticket(ticket_id: str, body: TicketUpdate, request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            row = get(tx, "tickets", ticket_id)
            require(row.get("workflow") != "collaboration", 409, "协作工单必须使用带审批校验的专用动作。")
            require(row["version"] == body.expected_version, 409, "工单已变化，请刷新。")
            allowed = {"待处理": {"处理中"}, "处理中": {"待外部反馈", "已关闭"}, "待外部反馈": {"处理中", "已关闭"}, "已关闭": set()}
            require(body.state in allowed[row["state"]], 409, "不允许该工单状态跳转。")
            resolution = clean(body.resolution) if body.resolution else ""
            require(body.state != "已关闭" or bool(resolution), 422, "关闭工单前请填写脱敏处理结果。")
            row.update(state=body.state, version=row["version"] + 1, resolution=resolution)
            tx.put("tickets", row["id"], row)
            audit(tx, "ticket_updated:" + body.state, row["id"], request)
            return row

    @app.patch("/api/tickets/{ticket_id}/priority")
    def update_ticket_priority(ticket_id: str, body: TicketPriorityInput, request: Request):
        require_role(request, "客服", "主管")
        reason = clean(body.reason)
        with db.transaction() as tx:
            row = get(tx, "tickets", ticket_id)
            require(row.get("workflow") != "collaboration", 409, "协作工单必须使用带审批校验的专用动作。")
            require(row["state"] != "已关闭", 409, "已关闭工单不能调整优先级。")
            require(row["version"] == body.expected_version, 409, "工单已变化，请刷新。")
            row.update(priority=body.priority, priority_reason=reason, version=row["version"] + 1, updated_at=now())
            tx.put("tickets", row["id"], row)
            audit(tx, "ticket_priority:" + body.priority, row["id"], request)
            return row

    @app.get("/api/channels")
    def channels(request: Request):
        require_role(request, "客服", "主管")
        return [{"id": "mock", "name": "合成咨询通道", "status": "仅模拟", "receive": True, "send": "simulated_only"}, taobao_status()] + [{"id": key, "name": name, "status": "待授权验证", "receive": False, "send": "unavailable", "requirements": ["尚未选择为首期接入平台"]} for key, name in [("feige", "飞鸽店铺咨询"), ("pdd", "拼多多店铺咨询"), ("xhs", "小红书店铺咨询")]]

    @app.get("/api/taobao/orders")
    def taobao_orders(request: Request):
        require_role(request, "客服", "主管")
        return {"source": "合成淘宝/天猫订单快照", "real_api_enabled": False, "orders": list_synthetic_orders()}

    @app.get("/api/taobao/orders/{order_ref}")
    def taobao_order(order_ref: str, request: Request):
        require_role(request, "客服", "主管")
        require(re.fullmatch(r"demo-order-\d{3}", order_ref) is not None, 404, "订单引用不存在。")
        order = synthetic_order(order_ref)
        require(order is not None, 404, "订单引用不存在。")
        return {"source": "合成淘宝/天猫订单快照", "real_api_enabled": False, "order": order}

    @app.post("/api/channels/{channel_id}/send")
    def disabled_connector(channel_id: str, request: Request):
        require_role(request, "主管")
        raise HTTPException(409, "真实平台通道尚未授权和验收，不允许发送。")

    register_collaboration(app, db, provider)
    register_inventory(app, db)
    register_notifications(app, db, notification_connector)
    register_scenario_center(app, db)
    register_quality(app)
    dist = ROOT / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
