"""Deterministic, read-only inventory facts for the first paid-use-case slice."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import actor_ref, require_role


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryItem(Input):
    sku: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    product_group: str = Field(min_length=1, max_length=80)
    grams: int = Field(gt=0, le=100000)
    sellable_qty: int = Field(ge=0, le=100000000)
    warehouse_qty: int = Field(ge=0, le=100000000)
    in_transit_qty: int = Field(ge=0, le=100000000)


class SnapshotInput(Input):
    source: str = Field(min_length=1, max_length=120)
    captured_at: str = Field(min_length=1, max_length=80)
    synthetic_or_redacted: Literal[True]
    items: list[InventoryItem] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique(self):
        if len({item.sku for item in self.items}) != len(self.items):
            raise ValueError("Duplicate SKU")
        return self


class EvaluateInput(Input):
    conversation_id: UUID
    expected_revision: int = Field(ge=1)
    sku: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    target_grams: int = Field(gt=0, le=100000)


def now():
    return datetime.now(timezone.utc).isoformat()


def evaluate_inventory(tx, body: EvaluateInput):
    conv = tx.get("conversations", str(body.conversation_id))
    if conv is None or conv["revision"] != body.expected_revision:
        return {"status": "stale", "reason": "咨询不存在或版本已变化，请刷新后重试。"}
    snapshots = tx.all("inventory_snapshots")
    if not snapshots:
        return {"status": "unknown", "reason": "没有可核验的库存快照。", "evidence": []}
    snapshot = sorted(snapshots, key=lambda row: row["captured_at"])[-1]
    exact = next((item for item in snapshot["items"] if item["sku"] == body.sku), None)
    if exact is None:
        return {"status": "unknown", "reason": "库存快照没有该 SKU，不能从商品页面推断库存。", "evidence": [], "source": snapshot["source"], "captured_at": snapshot["captured_at"]}
    evidence = [{"sku": exact["sku"], "name": exact["name"], "grams": exact["grams"], "sellable_qty": exact["sellable_qty"], "warehouse_qty": exact["warehouse_qty"], "in_transit_qty": exact["in_transit_qty"], "source": snapshot["source"], "captured_at": snapshot["captured_at"]}]
    if exact["sellable_qty"] > 0:
        return {"status": "in_stock", "reason": "该 SKU 有可售库存。", "evidence": evidence, "source": snapshot["source"], "captured_at": snapshot["captured_at"]}
    alternatives = []
    for item in snapshot["items"]:
        if item["sku"] == exact["sku"] or item["product_group"] != exact["product_group"] or body.target_grams % item["grams"]:
            continue
        units = body.target_grams // item["grams"]
        if units > 0 and item["sellable_qty"] >= units:
            alternatives.append({"sku": item["sku"], "name": item["name"], "grams": item["grams"], "units": units, "sellable_qty": item["sellable_qty"], "source": snapshot["source"], "captured_at": snapshot["captured_at"]})
    return {"status": "out_of_stock", "reason": "目标 SKU 当前无可售库存，需人工核实替代规格或补货安排。", "evidence": evidence, "alternatives": alternatives, "source": snapshot["source"], "captured_at": snapshot["captured_at"]}


def register_inventory(app, db):
    router = APIRouter(prefix="/api/inventory")

    @router.get("/state")
    def state(request: Request):
        require_role(request, "客服", "主管", "运营", "供应链", "仓储物流", "财务")
        with db.transaction() as tx:
            snapshots = sorted(tx.all("inventory_snapshots"), key=lambda row: row["captured_at"], reverse=True)
            return {"snapshots": snapshots[:20], "evaluations": sorted(tx.all("inventory_evaluations"), key=lambda row: row["created_at"], reverse=True)[:20]}

    @router.post("/snapshots")
    def snapshot(body: SnapshotInput, request: Request):
        require_role(request, "主管")
        row = {"id": uuid4().hex, "source": body.source, "captured_at": body.captured_at, "items": [item.model_dump() for item in body.items], "created_at": now()}
        with db.transaction() as tx:
            tx.put("inventory_snapshots", row["id"], row)
            key = uuid4().hex
            tx.put("audit_events", key, {"id": key, "action": "inventory_snapshot_saved", "object_id": row["id"], "at": now(), "actor": actor_ref(request) if app.state.auth.enabled else "local-developer"})
        return row

    @router.post("/evaluate")
    def evaluate(body: EvaluateInput, request: Request):
        require_role(request, "客服", "主管")
        with db.transaction() as tx:
            result = evaluate_inventory(tx, body)
            row = {"id": uuid4().hex, "conversation_id": str(body.conversation_id), "message_revision": body.expected_revision, "sku": body.sku, "target_grams": body.target_grams, "result": result, "created_at": now()}
            tx.put("inventory_evaluations", row["id"], row)
            return {"evaluation_id": row["id"], **result}

    app.include_router(router)
