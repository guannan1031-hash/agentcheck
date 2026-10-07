from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app


ACCOUNTS = {
    "service": {"role": "客服", "password": "synthetic-service-pass"},
    "lead": {"role": "主管", "password": "synthetic-lead-pass"},
    "ops": {"role": "运营", "password": "synthetic-operations-pass"},
    "warehouse": {"role": "仓储物流", "password": "synthetic-warehouse-pass"},
    "finance": {"role": "财务", "password": "synthetic-finance-pass"},
}


def login(client, user):
    response = client.post("/api/auth/login", json={"user_ref": user, "password": ACCOUNTS[user]["password"]})
    assert response.status_code == 200
    return response.json()["csrf"]


def post(client, path, body, csrf):
    return client.post(path, json=body, headers={"x-csrf-token": csrf})


def action(client, plan, csrf, kind, task_id=None, note=""):
    return post(client, f"/api/scenarios/task-plans/{plan['id']}/actions",
                 {"expected_version": plan["version"], "action": kind, "task_id": task_id,
                 "note": note, "request_id": str(uuid4()), "synthetic_or_redacted": True}, csrf)


def test_multi_department_tasks_wait_for_dependencies_and_all_receipts(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "multi-tasks.db"), auth_accounts=ACCOUNTS)
    with TestClient(app) as client:
        service_csrf = login(client, "service")
        case = post(client, "/api/scenarios/cases", {
            "text": "合成：客户反馈少件，请核查仓库并确认退款进度。", "synthetic_or_redacted": True,
            "request_id": str(uuid4()), "scenario_hint_id": "S087", "confirmed_facts": []}, service_csrf).json()
        create_body = {"case_id": case["id"], "expected_case_revision": case["revision"], "request_id": str(uuid4()), "synthetic_or_redacted": True,
                       "tasks": [
                           {"key": "warehouse_check", "department": "仓储物流", "summary": "核查合成出库与少件事实", "depends_on": []},
                           {"key": "refund_check", "department": "财务", "summary": "核查合成退款进度", "depends_on": ["warehouse_check"]},
                       ]}
        created = post(client, "/api/scenarios/task-plans", create_body, service_csrf)
        assert created.status_code == 200 and created.json()["state"] == "pending_approval"
        assert post(client, "/api/scenarios/task-plans", create_body, service_csrf).json() == created.json()

        lead_csrf = login(client, "lead")
        plan = action(client, created.json(), lead_csrf, "approve").json()
        assert [task["state"] for task in plan["tasks"]] == ["waiting", "blocked"]

        ops_csrf = login(client, "ops")
        assert client.get("/api/scenarios/state").json()["task_plans"] == []

        finance_csrf = login(client, "finance")
        finance_view = client.get("/api/scenarios/state").json()["task_plans"][0]
        assert finance_view["tasks"][0]["summary"] == "其他部门任务"
        finance_task = next(task for task in finance_view["tasks"] if task["department"] == "财务")
        assert action(client, finance_view, finance_csrf, "accept_task", finance_task["id"]).status_code == 409

        warehouse_csrf = login(client, "warehouse")
        warehouse_view = client.get("/api/scenarios/state").json()["task_plans"][0]
        warehouse_task = next(task for task in warehouse_view["tasks"] if task["department"] == "仓储物流")
        plan = action(client, warehouse_view, warehouse_csrf, "accept_task", warehouse_task["id"]).json()
        plan = action(client, plan, warehouse_csrf, "report_task", warehouse_task["id"], "合成回执：出库复核完成").json()
        assert next(task for task in plan["tasks"] if task["department"] == "财务")["state"] == "waiting"

        service_csrf = login(client, "service")
        assert action(client, plan, service_csrf, "close", note="合成：尝试提前结案").status_code == 409

        finance_csrf = login(client, "finance")
        finance_task = next(task for task in plan["tasks"] if task["department"] == "财务")
        plan = action(client, plan, finance_csrf, "accept_task", finance_task["id"]).json()
        plan = action(client, plan, finance_csrf, "report_task", finance_task["id"], "合成回执：仅核查进度，未执行退款").json()
        assert plan["state"] == "waiting_customer"

        service_csrf = login(client, "service")
        returned = action(client, plan, service_csrf, "return_task", warehouse_task["id"], "合成：仓库依据需要补充").json()
        assert returned["state"] == "in_progress"
        assert [task["state"] for task in returned["tasks"]] == ["working", "blocked"]

        warehouse_csrf = login(client, "warehouse")
        plan = action(client, returned, warehouse_csrf, "report_task", warehouse_task["id"], "合成回执：补充复核依据").json()
        finance_csrf = login(client, "finance")
        finance_task = next(task for task in plan["tasks"] if task["department"] == "财务")
        plan = action(client, plan, finance_csrf, "accept_task", finance_task["id"]).json()
        plan = action(client, plan, finance_csrf, "report_task", finance_task["id"], "合成回执：重新核查进度").json()
        service_csrf = login(client, "service")
        closed = action(client, plan, service_csrf, "close", note="合成：全部核查回执已由客服确认").json()
        assert closed["state"] == "closed" and closed["close_note"]
    app.state.db.engine.dispose()


def test_task_plan_rejects_cycles_private_text_and_stale_case(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "task-guards.db"))
    with TestClient(app) as client:
        case = client.post("/api/scenarios/cases", json={"actor": "客服", "text": "合成：核查物流", "synthetic_or_redacted": True,
                           "request_id": str(uuid4()), "scenario_hint_id": "S002", "confirmed_facts": []}).json()
        cycle = {"actor": "客服", "case_id": case["id"], "expected_case_revision": 1, "request_id": str(uuid4()), "synthetic_or_redacted": True, "tasks": [
            {"key": "warehouse", "department": "仓储物流", "summary": "核查物流", "depends_on": ["finance"]},
            {"key": "finance", "department": "财务", "summary": "核查进度", "depends_on": ["warehouse"]}]}
        assert client.post("/api/scenarios/task-plans", json=cycle).status_code == 422
        private = {**cycle, "request_id": str(uuid4()), "tasks": [{"key": "warehouse", "department": "仓储物流", "summary": "手机号13800138000", "depends_on": []}]}
        assert client.post("/api/scenarios/task-plans", json=private).status_code == 422
        updated = client.post("/api/scenarios/cases", json={"actor": "客服", "text": "合成：补充信息", "synthetic_or_redacted": True,
                              "request_id": str(uuid4()), "scenario_hint_id": "S002", "confirmed_facts": [],
                              "case_id": case["id"], "expected_revision": 1}).json()
        stale = {**private, "request_id": str(uuid4()), "expected_case_revision": 1,
                 "tasks": [{"key": "warehouse", "department": "仓储物流", "summary": "核查合成物流", "depends_on": []}]}
        assert updated["revision"] == 2 and client.post("/api/scenarios/task-plans", json=stale).status_code == 409
    app.state.db.engine.dispose()


def test_reviewed_task_pack_requires_facts_and_preserves_dependencies(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "task-pack.db"))
    with TestClient(app) as client:
        case = client.post("/api/scenarios/cases", json={
            "actor": "客服", "text": "合成：客户反馈漏发少件。", "synthetic_or_redacted": True,
            "request_id": str(uuid4()), "scenario_hint_id": "S086", "confirmed_facts": [],
        }).json()
        blocked = client.get(f"/api/scenarios/cases/{case['id']}/task-plan-suggestion").json()
        assert blocked["available"] is False
        assert blocked["tasks"] == []

        complete = client.post("/api/scenarios/cases", json={
            "actor": "客服", "case_id": case["id"], "expected_revision": case["revision"],
            "text": "合成：所需订单、商品、数量、物流和证据已由客服核验。", "synthetic_or_redacted": True,
            "request_id": str(uuid4()), "scenario_hint_id": "S086",
            "confirmed_facts": ["order_ref", "product_ref", "quantity", "logistics_status", "evidence"],
        }).json()
        suggestion = client.get(f"/api/scenarios/cases/{complete['id']}/task-plan-suggestion").json()
        assert suggestion["available"] is True
        assert suggestion["requires_supervisor_approval"] is True
        assert [task["department"] for task in suggestion["tasks"]] == ["仓储物流", "财务"]
        assert suggestion["tasks"][1]["depends_on"] == ["warehouse"]
        assert "不执行资金动作" in suggestion["tasks"][1]["summary"]
    app.state.db.engine.dispose()


def test_task_pack_returns_manual_path_for_unsupported_scene(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "unsupported-task-pack.db"))
    with TestClient(app) as client:
        case = client.post("/api/scenarios/cases", json={
            "actor": "客服", "text": "合成：客户咨询催发货。", "synthetic_or_redacted": True,
            "request_id": str(uuid4()), "scenario_hint_id": "S002", "confirmed_facts": [],
        }).json()
        suggestion = client.get(f"/api/scenarios/cases/{case['id']}/task-plan-suggestion").json()
        assert suggestion["available"] is False
        assert "手工拆分" in suggestion["reason"]
    app.state.db.engine.dispose()
