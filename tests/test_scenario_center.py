import json
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.scenario_center import classify_scene, evaluate


def payload(**overrides):
    body = {
        "actor": "客服",
        "text": "合成测试：客户咨询“催物流/催发货”，请判断下一步。",
        "synthetic_or_redacted": True,
        "request_id": str(uuid4()),
        "scenario_hint_id": "S002",
        "confirmed_facts": [],
    }
    body.update(overrides)
    return body


def test_registry_and_full_benchmark_cover_all_218_scenes():
    registry = json.load(open("configs/scenarios-v1.4.json", encoding="utf-8"))
    benchmark = json.load(open("tests/fixtures/full-scenario-benchmark-v1.4.json", encoding="utf-8"))
    assert registry["meta"]["scenario_count"] == len(registry["scenarios"]) == 218
    assert len({row["id"] for row in registry["scenarios"]}) == 218
    assert len(benchmark["cases"]) == 218
    assert {row["expected_scene_id"] for row in benchmark["cases"]} == {row["id"] for row in registry["scenarios"]}
    for case in benchmark["cases"]:
        scene, source, confidence = classify_scene(case["synthetic_text"])
        assert scene["id"] == case["expected_scene_id"]
        assert source == "exact_title" and confidence == 1.0
        assert evaluate(case["synthetic_text"], scene, case["confirmed_facts"], source, confidence)["route"] == case["expected_route"]


def test_case_moves_from_missing_facts_to_rule_review_then_risk(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "scenarios.db"))
    with TestClient(app) as client:
        state = client.get("/api/scenarios/state")
        assert state.status_code == 200 and len(state.json()["scenarios"]) == 218

        first = client.post("/api/scenarios/cases", json=payload())
        assert first.status_code == 200
        row = first.json()
        assert row["scene_id"] == "S002" and row["route"] == "needs_evidence"
        assert len(row["missing_facts"]) == 3

        complete = payload(case_id=row["id"], expected_revision=row["revision"], text="合成：已核验所需事实。",
                           confirmed_facts=["order_ref", "logistics_status", "customer_request"])
        second = client.post("/api/scenarios/cases", json=complete)
        assert second.status_code == 200 and second.json()["route"] == "needs_business_rule"

        risk = payload(case_id=row["id"], expected_revision=second.json()["revision"], text="合成：客户现在要求投诉并索赔。",
                       confirmed_facts=["order_ref", "logistics_status", "customer_request"])
        third = client.post("/api/scenarios/cases", json=risk)
        assert third.status_code == 200 and third.json()["route"] == "human_triage"
        assert len(third.json()["history"]) == 2
    app.state.db.engine.dispose()


def test_unknown_private_input_and_idempotency_are_guarded(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "scenario-guards.db"))
    with TestClient(app) as client:
        request_id = str(uuid4())
        body = payload(request_id=request_id)
        first = client.post("/api/scenarios/cases", json=body)
        repeat = client.post("/api/scenarios/cases", json=body)
        assert repeat.status_code == 200 and repeat.json()["id"] == first.json()["id"]
        changed = client.post("/api/scenarios/cases", json={**body, "text": "合成：不同内容"})
        assert changed.status_code == 409

        unknown = client.post("/api/scenarios/cases", json=payload(text="合成：这个事情怎么办？", scenario_hint_id=None))
        assert unknown.status_code == 200 and unknown.json()["route"] == "human_triage"
        assert unknown.json()["classification_source"] == "unknown"

        private = client.post("/api/scenarios/cases", json=payload(text="手机号13800138000"))
        assert private.status_code == 422
    app.state.db.engine.dispose()


def test_quality_role_can_read_registry_but_cannot_create_case(tmp_path):
    accounts = {"quality": {"role": "质量", "password": "synthetic-quality-pass"}}
    app = create_app("sqlite:///" + str(tmp_path / "quality.db"), auth_accounts=accounts)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"user_ref": "quality", "password": accounts["quality"]["password"]})
        assert login.status_code == 200
        assert client.get("/api/scenarios/state").status_code == 200
        response = client.post("/api/scenarios/cases", json={key: value for key, value in payload().items() if key != "actor"},
                               headers={"x-csrf-token": login.json()["csrf"]})
        assert response.status_code == 403
    app.state.db.engine.dispose()


def test_service_feedback_and_supervisor_confirmation_are_versioned(tmp_path):
    accounts = {
        "service": {"role": "客服", "password": "synthetic-service-pass"},
        "lead": {"role": "主管", "password": "synthetic-lead-pass"},
    }
    app = create_app("sqlite:///" + str(tmp_path / "reviews.db"), auth_accounts=accounts)
    with TestClient(app) as client:
        service = client.post("/api/auth/login", json={"user_ref": "service", "password": accounts["service"]["password"]}).json()
        request_id = str(uuid4())
        feedback = {"scenario_id": "S002", "occurrence": "yes", "frequency_30d": 36,
                    "current_steps": "客服登记→主管审批→仓储物流核查", "current_systems": "客服系统、吉客云、钉钉",
                    "notes": "合成业务反馈：等待部门回执较久", "synthetic_or_redacted": True, "request_id": request_id}
        submitted = client.post("/api/scenarios/reviews", json=feedback, headers={"x-csrf-token": service["csrf"]})
        assert submitted.status_code == 200 and submitted.json()["status"] == "submitted" and submitted.json()["version"] == 1
        assert client.post("/api/scenarios/reviews", json=feedback, headers={"x-csrf-token": service["csrf"]}).json() == submitted.json()
        assert client.post("/api/scenarios/reviews", json={**feedback, "notes": "不同内容"}, headers={"x-csrf-token": service["csrf"]}).status_code == 409
        private = {**feedback, "request_id": str(uuid4()), "expected_version": 1, "notes": "手机号13800138000"}
        assert client.post("/api/scenarios/reviews", json=private, headers={"x-csrf-token": service["csrf"]}).status_code == 422
        client.post("/api/auth/logout", json={}, headers={"x-csrf-token": service["csrf"]})

        lead = client.post("/api/auth/login", json={"user_ref": "lead", "password": accounts["lead"]["password"]}).json()
        decision = {"expected_version": 1, "decision": "confirmed", "note": "确认的是流程资料，不含自动执行授权。", "request_id": str(uuid4())}
        confirmed = client.post("/api/scenarios/reviews/S002/decision", json=decision, headers={"x-csrf-token": lead["csrf"]})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed" and confirmed.json()["version"] == 2
        assert client.post("/api/scenarios/reviews/S002/decision", json={**decision, "request_id": str(uuid4())}, headers={"x-csrf-token": lead["csrf"]}).status_code == 409
        state = client.get("/api/scenarios/state").json()
        assert state["review_summary"]["confirmed"] == 1 and state["reviews"]["S002"]["frequency_30d"] == 36
    app.state.db.engine.dispose()


def test_classification_review_creates_labeled_versioned_sample(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "test-review.db"))
    with TestClient(app) as client:
        case = client.post("/api/scenarios/cases", json=payload(scenario_hint_id=None)).json()
        request_id = str(uuid4())
        review = {"actor": "客服", "case_id": case["id"], "expected_case_revision": case["revision"],
                  "verdict": "wrong", "expected_scene_id": "S003", "note": "合成测试中更接近长时间未发货",
                  "request_id": request_id}
        saved = client.post("/api/scenarios/case-reviews", json=review)
        assert saved.status_code == 200 and saved.json()["predicted_scene_id"] == "S002"
        assert saved.json()["expected_scene_id"] == "S003" and saved.json()["expected_scene_name"]
        assert client.post("/api/scenarios/case-reviews", json=review).json() == saved.json()
        assert client.post("/api/scenarios/case-reviews", json={**review, "request_id": str(uuid4())}).status_code == 409
        state = client.get("/api/scenarios/state").json()
        assert state["test_summary"] == {"correct": 0, "wrong": 1, "unsure": 0}
    app.state.db.engine.dispose()
