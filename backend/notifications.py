"""Audited DingTalk/Feishu notification outbox and manual delivery runtime."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import actor_ref, require_role
from .connector_runtime import DeliveryError, NotificationConnector
from .safety import contains_private


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: Literal["客服", "主管", "运营", "供应链", "仓储物流", "财务", "质量"] | None = None


class CreateInput(Input):
    ticket_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_version: int = Field(ge=1)
    channel: Literal["dingtalk", "feishu"]
    target_ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    request_id: UUID
    synthetic_or_redacted: Literal[True]

    @model_validator(mode="after")
    def source(self):
        if bool(self.ticket_id) == bool(self.task_plan_id):
            raise ValueError("必须且只能选择一种通知来源。")
        if bool(self.task_plan_id) != bool(self.task_id):
            raise ValueError("多部门计划通知必须指定具体任务。")
        return self


class ActionInput(Input):
    expected_version: int = Field(ge=1)


class SendInput(ActionInput):
    request_id: UUID


class ResolveInput(ActionInput):
    decision: Literal["delivered", "not_delivered"]
    note: str = Field(min_length=1, max_length=300)


def now():
    return datetime.now(timezone.utc).isoformat()


def require(ok, status, message):
    if not ok:
        raise HTTPException(status, message)


def register_notifications(app, db, connector=None):
    router = APIRouter(prefix="/api/notifications")
    runtime = connector or NotificationConnector()

    with db.transaction() as tx:
        for row in tx.all("notification_outbox"):
            if "source_type" not in row and row.get("ticket_id"):
                row.update(source_type="ticket", source_id=row["ticket_id"], source_version=row["ticket_version"], task_id=None)
            if row.get("state") == "sending":
                row.update(state="unknown_delivery", last_error="服务中断时正在发送，请先在目标群核对。", updated_at=now())
            tx.put("notification_outbox", row["id"], row)

    def actor(request, claimed):
        principal = require_role(request, "主管")
        if app.state.auth.enabled:
            return actor_ref(request, claimed)
        require(claimed == "主管", 403, "需要主管演示角色。")
        return actor_ref(request, claimed)

    def source_for_create(tx, body):
        if body.ticket_id:
            ticket = tx.get("tickets", body.ticket_id)
            require(ticket is not None and ticket.get("workflow") == "collaboration", 404, "协作工单不存在。")
            require(ticket["version"] == body.expected_version, 409, "工单已变化，请刷新后生成通知。")
            require(ticket["state"] in ("待接单", "处理中", "待客服确认"), 409, "工单尚未派发，不能生成部门通知。")
            require(ticket.get("department") is not None, 422, "工单没有责任部门。")
            evidence = ticket.get("inventory_evidence", {})
            reason = evidence.get("reason", "请核查该协作工单并回传脱敏结果")
            return {"source_type": "ticket", "source_id": ticket["id"], "source_version": ticket["version"],
                    "task_id": None, "department": ticket["department"], "summary": ticket["summary"], "reason": reason}
        plan = tx.get("scenario_task_plans", body.task_plan_id)
        require(plan is not None, 404, "多部门核查计划不存在。")
        require(plan["version"] == body.expected_version, 409, "核查计划已变化，请刷新后生成通知。")
        require(plan["state"] in ("dispatched", "in_progress"), 409, "核查计划尚未批准或已结束。")
        task = next((item for item in plan["tasks"] if item["id"] == body.task_id), None)
        require(task is not None, 404, "核查任务不存在。")
        require(task["state"] in ("waiting", "working"), 409, "该任务尚未到达可通知状态或已回传。")
        return {"source_type": "task_plan", "source_id": plan["id"], "source_version": plan["version"],
                "task_id": task["id"], "department": task["department"], "summary": plan["scene_name"], "reason": task["summary"]}

    def require_current_source(tx, row):
        if row["source_type"] == "ticket":
            source = tx.get("tickets", row["source_id"])
            require(source is not None and source.get("workflow") == "collaboration", 404, "关联协作工单不存在。")
            require(source["version"] == row["source_version"], 409, "关联工单已变化，请重新生成通知。")
            require(source["state"] in ("待接单", "处理中", "待客服确认"), 409, "关联工单当前不可发送通知。")
            return
        source = tx.get("scenario_task_plans", row["source_id"])
        require(source is not None, 404, "关联核查计划不存在。")
        require(source["version"] == row["source_version"], 409, "关联核查计划已变化，请重新生成通知。")
        task = next((item for item in source["tasks"] if item["id"] == row["task_id"]), None)
        require(task is not None and task["state"] in ("waiting", "working"), 409, "关联任务当前不可发送通知。")

    def get_action_row(tx, notification_id, expected_version):
        row = tx.get("notification_outbox", notification_id)
        require(row is not None, 404, "通知记录不存在。")
        require(row["source_version"] == expected_version, 409, "关联业务版本已变化，请重新预览。")
        require_current_source(tx, row)
        return row

    @router.get("/state")
    def state(request: Request):
        principal = require_role(request, "客服", "主管", "运营", "供应链", "仓储物流", "财务", "质量")
        with db.transaction() as tx:
            rows = []
            for row in tx.all("notification_outbox"):
                if not app.state.auth.enabled or principal.role in ("客服", "主管") or row.get("department") == principal.role:
                    rows.append(row)
            channels = runtime.channel_state()
            return {"items": sorted(rows, key=lambda row: row["created_at"], reverse=True),
                    "send_enabled": any(item["configured"] for item in channels), "channels": channels}

    @router.post("")
    def create(body: CreateInput, request: Request):
        who = actor(request, body.actor)
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        receipt_key = "notification-request:" + str(body.request_id)
        with db.transaction() as tx:
            prior = tx.get("received_events", receipt_key)
            if prior:
                require(prior["fingerprint"] == fingerprint, 409, "通知请求标识已用于不同内容。")
                return prior["result"]
            source = source_for_create(tx, body)
            key = f"{source['source_type']}:{source['source_id']}:{source['source_version']}:{source['task_id']}:{body.channel}:{body.target_ref}"
            duplicate = next((row for row in tx.all("notification_outbox") if row.get("dedup_key") == key), None)
            if duplicate:
                result = duplicate
            else:
                text = f"协作核查：{source['summary']}。责任部门：{source['department']}。{source['reason']}。请在系统中回传脱敏结果。"
                result = {"id": uuid4().hex, "dedup_key": key, **source, "channel": body.channel,
                          "target_ref": body.target_ref, "text": text, "state": "preview_only", "attempts": 0,
                          "last_error": "", "provider_receipt": None, "created_at": now(), "updated_at": now(), "actor": who}
                if source["source_type"] == "ticket":
                    result.update(ticket_id=source["source_id"], ticket_version=source["source_version"])
                tx.put("notification_outbox", result["id"], result)
            tx.put("received_events", receipt_key, {"fingerprint": fingerprint, "result": result})
            return result

    @router.post("/{notification_id}/queue")
    def queue(notification_id: str, body: ActionInput, request: Request):
        who = actor(request, body.actor)
        with db.transaction() as tx:
            row = get_action_row(tx, notification_id, body.expected_version)
            require(row["state"] in ("preview_only", "failed"), 409, "通知当前不能排队。")
            row.update(state="queued", last_error="", updated_at=now())
            tx.put("notification_outbox", row["id"], row)
            audit = uuid4().hex
            tx.put("audit_events", audit, {"id": audit, "action": "notification_queued", "object_id": row["id"], "actor": who, "at": now()})
            return row

    @router.post("/{notification_id}/retry")
    def retry(notification_id: str, body: ActionInput, request: Request):
        actor(request, body.actor)
        with db.transaction() as tx:
            row = get_action_row(tx, notification_id, body.expected_version)
            require(row["state"] == "failed", 409, "只有明确失败的通知可以重试；未知送达状态须先人工核对。")
            require(row.get("attempts", 0) < 3, 429, "本地重试次数已达到3次。")
            row.update(state="queued", updated_at=now(), last_error="")
            tx.put("notification_outbox", row["id"], row)
            return row

    @router.post("/{notification_id}/send")
    def send(notification_id: str, body: SendInput, request: Request):
        who = actor(request, body.actor)
        fingerprint = hashlib.sha256(f"{notification_id}:{body.expected_version}".encode()).hexdigest()
        receipt_key = "notification-send:" + str(body.request_id)
        with db.transaction() as tx:
            prior = tx.get("received_events", receipt_key)
            if prior:
                require(prior["fingerprint"] == fingerprint, 409, "发送请求标识已用于不同内容。")
                return prior["result"]
            row = get_action_row(tx, notification_id, body.expected_version)
            require(row["state"] == "queued", 409, "请先由主管确认排队；发送中或送达未知时禁止重复发送。")
            require(runtime.configured(row["channel"], row["target_ref"]), 409, "该目标未配置真实连接器，通知仍保留在本地队列。")
            row.update(state="sending", attempts=row.get("attempts", 0) + 1, last_error="", updated_at=now(),
                       active_delivery_id=str(body.request_id))
            tx.put("notification_outbox", row["id"], row)
        try:
            provider_receipt = runtime.send(row["channel"], row["target_ref"], row["text"], str(body.request_id))
            state_value, error = "sent", ""
        except DeliveryError as delivery_error:
            provider_receipt = None
            state_value = "unknown_delivery" if delivery_error.unknown else "failed"
            error = str(delivery_error)
        with db.transaction() as tx:
            current = tx.get("notification_outbox", notification_id)
            require(current is not None and current.get("active_delivery_id") == str(body.request_id), 409, "通知状态已变化，请刷新核对。")
            current.update(state=state_value, last_error=error, provider_receipt=provider_receipt,
                           sent_at=now() if state_value == "sent" else None, updated_at=now())
            current.pop("active_delivery_id", None)
            tx.put("notification_outbox", current["id"], current)
            tx.put("received_events", receipt_key, {"fingerprint": fingerprint, "result": current})
            audit = uuid4().hex
            tx.put("audit_events", audit, {"id": audit, "action": "notification_" + state_value,
                   "object_id": current["id"], "actor": who, "at": now()})
            return current

    @router.post("/{notification_id}/resolve-unknown")
    def resolve_unknown(notification_id: str, body: ResolveInput, request: Request):
        who = actor(request, body.actor)
        note = body.note.strip()
        require(bool(note) and not contains_private(note), 422, "请填写脱敏核对说明。")
        with db.transaction() as tx:
            row = get_action_row(tx, notification_id, body.expected_version)
            require(row["state"] == "unknown_delivery", 409, "只有送达未知的通知需要人工核对。")
            if body.decision == "delivered":
                row.update(state="sent", sent_at=now(), last_error="", provider_receipt={"manual_confirmation": True})
            else:
                row.update(state="failed", last_error="人工核对确认未送达。")
            row.update(updated_at=now(), resolution_note=note)
            tx.put("notification_outbox", row["id"], row)
            audit = uuid4().hex
            tx.put("audit_events", audit, {"id": audit, "action": "notification_unknown_resolved:" + body.decision,
                   "object_id": row["id"], "actor": who, "at": now()})
            return row

    app.include_router(router)
