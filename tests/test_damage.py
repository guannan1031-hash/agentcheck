from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app


def post_message(client, question, conversation_id=None, expected_revision=None):
    body = {'question': question, 'synthetic_or_redacted': True, 'event_id': str(uuid4())}
    if conversation_id:
        body.update(conversation_id=conversation_id, expected_revision=expected_revision)
    response = client.post('/api/messages', json=body)
    assert response.status_code == 200, response.text
    return response.json()['conversation_id']


def evaluate(client, conversation_id, context, request_id=None):
    return client.post('/api/collaboration/damage/evaluate-and-ticket', json={
        'actor': '客服', 'conversation_id': conversation_id, 'expected_revision': 1,
        'context': context, 'request_id': str(request_id or uuid4()), 'synthetic_or_redacted': True,
    })


def test_damage_missing_context_only_creates_evidence_request_and_is_idempotent(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'damage.db'))
    with TestClient(app) as client:
        cid = post_message(client, '合成：收到的商品破了。')
        request_id = uuid4()
        context = {'product_ref': 'DEMO-COFFEE', 'damage_type': 'unknown', 'quantity_band': 'unknown',
                   'evidence_status': 'missing', 'business_fact_status': 'unknown'}
        first = evaluate(client, cid, context, request_id)
        assert first.status_code == 200
        result = first.json()
        assert result['case']['route'] == 'needs_evidence'
        assert result['case']['state'] == '待补证'
        assert result['ticket'] is None
        assert '外包装照片' in result['case']['reply_draft']
        assert evaluate(client, cid, context, request_id).json() == result
    app.state.db.engine.dispose()


def test_damage_uses_context_change_to_block_old_approval_and_route_risk_to_human(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'damage-risk.db'))
    with TestClient(app) as client:
        cid = post_message(client, '合成：收到的商品破损了，请核查。')
        ready = {'product_ref': 'DEMO-COFFEE', 'damage_type': 'product_breakage', 'quantity_band': 'one',
                 'evidence_status': 'provided', 'business_fact_status': 'verified'}
        ticket = evaluate(client, cid, ready).json()['ticket']
        assert ticket['state'] == '待审批' and ticket['category'] == 'damage'
        updated_cid = post_message(client, '合成：漏液还有异味，担心食品安全。', cid, 1)
        assert updated_cid == cid
        blocked = client.post('/api/collaboration/tickets/' + ticket['id'] + '/actions', json={
            'actor': '主管', 'expected_version': ticket['version'], 'action': 'approve'})
        assert blocked.status_code == 409
        risk = client.post('/api/collaboration/damage/evaluate-and-ticket', json={
            'actor': '客服', 'conversation_id': cid, 'expected_revision': 2, 'context': {**ready, 'damage_type': 'leak'},
            'request_id': str(uuid4()), 'synthetic_or_redacted': True,
        })
        assert risk.status_code == 200
        assert risk.json()['case']['route'] == 'human_triage'
        assert risk.json()['case']['ticket_id'] is None
    app.state.db.engine.dispose()


def test_damage_ready_case_requires_supervisor_before_warehouse_queue(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'damage-approval.db'))
    with TestClient(app) as client:
        cid = post_message(client, '合成：商品破损，请核查包装与物流。')
        context = {'product_ref': 'DEMO-COFFEE', 'damage_type': 'product_breakage', 'quantity_band': 'one',
                   'evidence_status': 'provided', 'business_fact_status': 'verified'}
        ticket = evaluate(client, cid, context).json()['ticket']
        assert ticket['state'] == '待审批'
        approved = client.post('/api/collaboration/tickets/' + ticket['id'] + '/actions', json={
            'actor': '主管', 'expected_version': ticket['version'], 'action': 'approve'})
        assert approved.status_code == 200
        assert approved.json()['state'] == '待接单'
        assert approved.json()['department'] == '仓储物流'
    app.state.db.engine.dispose()
