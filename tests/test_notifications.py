import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.connector_runtime import NotificationConnector


def make_ticket(client):
    message = client.post('/api/messages', json={'question': '合成：请核实咖啡库存，页面缺货。', 'synthetic_or_redacted': True, 'event_id': str(uuid4())}).json()
    ticket = client.post('/api/collaboration/tickets', json={'actor': '客服', 'conversation_id': message['conversation_id'], 'expected_revision': 1, 'category': 'stock'}).json()
    return client.post('/api/collaboration/tickets/' + ticket['id'] + '/actions', json={'actor': '主管', 'expected_version': ticket['version'], 'action': 'approve'}).json()


def preview_and_queue(client, ticket, target='supply-team'):
    body = {'actor': '主管', 'ticket_id': ticket['id'], 'expected_version': ticket['version'], 'channel': 'dingtalk',
            'target_ref': target, 'request_id': str(uuid4()), 'synthetic_or_redacted': True}
    preview = client.post('/api/notifications', json=body)
    assert preview.status_code == 200 and preview.json()['state'] == 'preview_only'
    assert client.post('/api/notifications', json=body).json() == preview.json()
    queued = client.post('/api/notifications/' + preview.json()['id'] + '/queue', json={'actor': '主管', 'expected_version': ticket['version']})
    assert queued.status_code == 200 and queued.json()['state'] == 'queued'
    return queued.json()


def test_notification_outbox_keeps_send_disabled_without_config(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'notification.db'), notification_connector=NotificationConnector(targets={}))
    with TestClient(app) as client:
        ticket = make_ticket(client)
        queued = preview_and_queue(client, ticket)
        response = client.post('/api/notifications/' + queued['id'] + '/send', json={
            'actor': '主管', 'expected_version': ticket['version'], 'request_id': str(uuid4())})
        assert response.status_code == 409
        state = client.get('/api/notifications/state').json()
        assert state['send_enabled'] is False and state['items'][0]['state'] == 'queued'
    app.state.db.engine.dispose()


def test_dingtalk_send_is_signed_audited_and_idempotent(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'errcode': 0, 'errmsg': 'ok'})

    connector = NotificationConnector(
        targets={'dingtalk': {'supply-team': {'url': 'https://oapi.dingtalk.com/robot/send?access_token=synthetic', 'secret': 'synthetic-secret'}}},
        transport=httpx.MockTransport(handler), clock=lambda: 1700000000,
    )
    app = create_app('sqlite:///' + str(tmp_path / 'sent.db'), notification_connector=connector)
    with TestClient(app) as client:
        ticket = make_ticket(client)
        queued = preview_and_queue(client, ticket)
        request_id = str(uuid4())
        body = {'actor': '主管', 'expected_version': ticket['version'], 'request_id': request_id}
        sent = client.post('/api/notifications/' + queued['id'] + '/send', json=body)
        assert sent.status_code == 200 and sent.json()['state'] == 'sent'
        assert sent.json()['attempts'] == 1 and sent.json()['provider_receipt']['platform_code'] == '0'
        assert client.post('/api/notifications/' + queued['id'] + '/send', json=body).json() == sent.json()
        assert len(requests) == 1 and 'timestamp=1700000000000' in str(requests[0].url) and 'sign=' in str(requests[0].url)
        assert json.loads(requests[0].content)['msgtype'] == 'text'
        public_state = json.dumps(client.get('/api/notifications/state').json())
        assert 'access_token' not in public_state and 'synthetic-secret' not in public_state
    app.state.db.engine.dispose()


def test_feishu_send_uses_signed_text_payload(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'StatusCode': 0, 'StatusMessage': 'success'})

    connector = NotificationConnector(
        targets={'feishu': {'supply-team': {'url': 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic', 'secret': 'synthetic-secret'}}},
        transport=httpx.MockTransport(handler), clock=lambda: 1700000000,
    )
    app = create_app('sqlite:///' + str(tmp_path / 'feishu.db'), notification_connector=connector)
    with TestClient(app) as client:
        ticket = make_ticket(client)
        preview = client.post('/api/notifications', json={
            'actor': '主管', 'ticket_id': ticket['id'], 'expected_version': ticket['version'], 'channel': 'feishu',
            'target_ref': 'supply-team', 'request_id': str(uuid4()), 'synthetic_or_redacted': True,
        }).json()
        queued = client.post('/api/notifications/' + preview['id'] + '/queue', json={
            'actor': '主管', 'expected_version': ticket['version']}).json()
        sent = client.post('/api/notifications/' + queued['id'] + '/send', json={
            'actor': '主管', 'expected_version': ticket['version'], 'request_id': str(uuid4())}).json()
        payload = json.loads(requests[0].content)
        assert sent['state'] == 'sent' and payload['msg_type'] == 'text'
        assert payload['timestamp'] == '1700000000' and payload['sign'] and payload['content']['text']
    app.state.db.engine.dispose()


def test_explicit_platform_rejection_can_retry(tmp_path):
    connector = NotificationConnector(
        targets={'dingtalk': {'supply-team': {'url': 'https://oapi.dingtalk.com/robot/send?access_token=synthetic'}}},
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'errcode': 310000, 'errmsg': 'rejected'})),
    )
    app = create_app('sqlite:///' + str(tmp_path / 'failed.db'), notification_connector=connector)
    with TestClient(app) as client:
        ticket = make_ticket(client)
        queued = preview_and_queue(client, ticket)
        failed = client.post('/api/notifications/' + queued['id'] + '/send', json={
            'actor': '主管', 'expected_version': ticket['version'], 'request_id': str(uuid4())}).json()
        assert failed['state'] == 'failed' and failed['attempts'] == 1 and '310000' in failed['last_error']
        retried = client.post('/api/notifications/' + queued['id'] + '/retry', json={
            'actor': '主管', 'expected_version': ticket['version']})
        assert retried.status_code == 200 and retried.json()['state'] == 'queued'
    app.state.db.engine.dispose()


def test_unknown_delivery_requires_manual_resolution_before_retry(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout('synthetic timeout', request=request)

    connector = NotificationConnector(
        targets={'dingtalk': {'supply-team': {'url': 'https://oapi.dingtalk.com/robot/send?access_token=synthetic'}}},
        transport=httpx.MockTransport(timeout),
    )
    app = create_app('sqlite:///' + str(tmp_path / 'unknown.db'), notification_connector=connector)
    with TestClient(app) as client:
        ticket = make_ticket(client)
        queued = preview_and_queue(client, ticket)
        unknown = client.post('/api/notifications/' + queued['id'] + '/send', json={
            'actor': '主管', 'expected_version': ticket['version'], 'request_id': str(uuid4())}).json()
        assert unknown['state'] == 'unknown_delivery'
        retry = client.post('/api/notifications/' + queued['id'] + '/retry', json={'actor': '主管', 'expected_version': ticket['version']})
        assert retry.status_code == 409
        resolved = client.post('/api/notifications/' + queued['id'] + '/resolve-unknown', json={
            'actor': '主管', 'expected_version': ticket['version'], 'decision': 'not_delivered', 'note': '合成：已检查目标群，没有对应消息。'})
        assert resolved.status_code == 200 and resolved.json()['state'] == 'failed'
        assert client.post('/api/notifications/' + queued['id'] + '/retry', json={
            'actor': '主管', 'expected_version': ticket['version']}).json()['state'] == 'queued'
    app.state.db.engine.dispose()


def test_scenario_task_can_generate_notification_only_after_dependency_is_ready(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'task-plan-notification.db'), notification_connector=NotificationConnector(targets={}))
    with TestClient(app) as client:
        case = client.post('/api/scenarios/cases', json={
            'actor': '客服', 'text': '合成：客户反馈漏发少件。', 'synthetic_or_redacted': True, 'request_id': str(uuid4()),
            'scenario_hint_id': 'S086', 'confirmed_facts': ['order_ref', 'product_ref', 'quantity', 'logistics_status', 'evidence'],
        }).json()
        tasks = client.get(f"/api/scenarios/cases/{case['id']}/task-plan-suggestion").json()['tasks']
        plan = client.post('/api/scenarios/task-plans', json={
            'actor': '客服', 'case_id': case['id'], 'expected_case_revision': case['revision'], 'request_id': str(uuid4()),
            'synthetic_or_redacted': True, 'tasks': tasks,
        }).json()
        plan = client.post(f"/api/scenarios/task-plans/{plan['id']}/actions", json={
            'actor': '主管', 'expected_version': plan['version'], 'action': 'approve', 'request_id': str(uuid4()),
            'synthetic_or_redacted': True,
        }).json()
        waiting, blocked = plan['tasks']
        base = {'actor': '主管', 'task_plan_id': plan['id'], 'expected_version': plan['version'], 'channel': 'feishu',
                'target_ref': 'warehouse-team', 'request_id': str(uuid4()), 'synthetic_or_redacted': True}
        created = client.post('/api/notifications', json={**base, 'task_id': waiting['id']})
        assert created.status_code == 200 and created.json()['source_type'] == 'task_plan'
        blocked_result = client.post('/api/notifications', json={**base, 'request_id': str(uuid4()), 'task_id': blocked['id']})
        assert blocked_result.status_code == 409
    app.state.db.engine.dispose()
