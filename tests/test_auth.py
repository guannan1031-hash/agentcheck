from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app


ACCOUNTS = {
    'service': {'role': '客服', 'password': 'synthetic-service-pass'},
    'lead': {'role': '主管', 'password': 'synthetic-lead-pass'},
    'ops': {'role': '运营', 'password': 'synthetic-ops-pass'},
    'finance': {'role': '财务', 'password': 'synthetic-finance-pass'},
}


def login(client, user_ref, password):
    response = client.post('/api/auth/login', json={'user_ref': user_ref, 'password': password})
    assert response.status_code == 200, response.text
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    return response.json()['csrf']


def write(client, path, body, csrf):
    return client.post(path, json=body, headers={'x-csrf-token': csrf})


def test_login_enforces_server_role_csrf_and_department_queue(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'auth.db'), auth_accounts=ACCOUNTS)
    with TestClient(app) as client:
        assert client.get('/api/state').status_code == 401
        assert client.get('/api/reports/pilot-summary').status_code == 401
        assert client.request('DELETE', '/api/pilot/data', json={'confirmation': 'CLEAR_LOCAL_TRIAL_DATA'}).status_code == 401
        assert client.post('/api/messages', json={}).status_code == 401
        bad = client.post('/api/auth/login', json={'user_ref': 'service', 'password': 'wrong-password'})
        assert bad.status_code == 401 and 'wrong-password' not in bad.text

        csrf = login(client, 'service', ACCOUNTS['service']['password'])
        assert client.get('/api/auth/status').json()['role'] == '客服'
        assert client.request('DELETE', '/api/pilot/data', json={'confirmation': 'CLEAR_LOCAL_TRIAL_DATA'}, headers={'x-csrf-token': csrf}).status_code == 403
        assert client.put('/api/pilot/goals', json={
            'expected_version': 1, 'target_case_count': 30, 'baseline_median_minutes': 0,
            'target_median_minutes': 0, 'max_overdue_tickets': 0,
        }, headers={'x-csrf-token': csrf}).status_code == 403
        assert client.post('/api/messages', json={'question': '合成：请核实咖啡库存，页面缺货。', 'synthetic_or_redacted': True, 'event_id': str(uuid4())}).status_code == 403
        created = write(client, '/api/messages', {'question': '合成：请核实咖啡库存，页面缺货。', 'synthetic_or_redacted': True, 'event_id': str(uuid4())}, csrf).json()
        ticket = write(client, '/api/collaboration/tickets', {'actor': '主管', 'conversation_id': created['conversation_id'], 'expected_revision': 1, 'category': 'stock'}, csrf)
        assert ticket.status_code == 403
        ticket = write(client, '/api/collaboration/tickets', {'conversation_id': created['conversation_id'], 'expected_revision': 1, 'category': 'stock'}, csrf).json()
        assert ticket['state'] == '待审批'
        assert write(client, '/api/auth/logout', {}, csrf).status_code == 200
        assert client.get('/api/collaboration/state').status_code == 401

        lead_csrf = login(client, 'lead', ACCOUNTS['lead']['password'])
        assert client.put('/api/pilot/goals', json={
            'expected_version': 1, 'target_case_count': 30, 'baseline_median_minutes': 0,
            'target_median_minutes': 0, 'max_overdue_tickets': 0,
        }, headers={'x-csrf-token': lead_csrf}).status_code == 200
        approved = write(client, '/api/collaboration/tickets/' + ticket['id'] + '/actions', {'expected_version': ticket['version'], 'action': 'approve'}, lead_csrf)
        assert approved.status_code == 200 and approved.json()['department'] == '运营'
        write(client, '/api/auth/logout', {}, lead_csrf)

        finance_csrf = login(client, 'finance', ACCOUNTS['finance']['password'])
        assert client.get('/api/state').status_code == 403
        assert client.get('/api/collaboration/state').json()['tickets'] == []
        assert write(client, '/api/collaboration/tickets/' + ticket['id'] + '/actions', {'expected_version': approved.json()['version'], 'action': 'accept'}, finance_csrf).status_code == 403
        write(client, '/api/auth/logout', {}, finance_csrf)

        ops_csrf = login(client, 'ops', ACCOUNTS['ops']['password'])
        rows = client.get('/api/collaboration/state').json()['tickets']
        assert [row['id'] for row in rows] == [ticket['id']]
        assert write(client, '/api/collaboration/tickets/' + ticket['id'] + '/actions', {'expected_version': approved.json()['version'], 'action': 'accept'}, ops_csrf).status_code == 200
    app.state.db.engine.dispose()


def test_public_faq_configuration_stays_available_when_console_login_is_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv('CS_PUBLIC_FAQ_ENABLED', 'true')
    app = create_app('sqlite:///' + str(tmp_path / 'public-faq-auth.db'), auth_accounts=ACCOUNTS)
    with TestClient(app) as client:
        assert client.get('/api/public/faq/config').json() == {'brand': '商品客服', 'handoff_url': None}
        assert client.get('/api/public/faq/config').headers['cache-control'] == 'no-store'
    app.state.db.engine.dispose()


def test_logged_in_tenants_are_isolated_in_storage_and_api_reads(tmp_path):
    accounts = {
        'alpha_service': {'role': '客服', 'tenant_id': 'alpha-shop', 'password': 'synthetic-alpha-pass'},
        'beta_service': {'role': '客服', 'tenant_id': 'beta-shop', 'password': 'synthetic-beta-pass'},
    }
    app = create_app('sqlite:///' + str(tmp_path / 'tenant.db'), auth_accounts=accounts)
    with TestClient(app) as client:
        alpha_csrf = login(client, 'alpha_service', accounts['alpha_service']['password'])
        assert client.get('/api/auth/status').json()['tenant_id'] == 'alpha-shop'
        alpha = write(client, '/api/messages', {
            'question': '合成：alpha 店铺咨询燕麦规格。', 'synthetic_or_redacted': True, 'event_id': str(uuid4()),
        }, alpha_csrf)
        assert alpha.status_code == 200
        assert len(client.get('/api/state').json()['conversations']) == 1
        assert write(client, '/api/auth/logout', {}, alpha_csrf).status_code == 200

        beta_csrf = login(client, 'beta_service', accounts['beta_service']['password'])
        assert client.get('/api/auth/status').json()['tenant_id'] == 'beta-shop'
        assert client.get('/api/state').json()['conversations'] == []
        beta = write(client, '/api/messages', {
            'question': '合成：beta 店铺咨询咖啡库存。', 'synthetic_or_redacted': True, 'event_id': str(uuid4()),
        }, beta_csrf)
        assert beta.status_code == 200
        assert len(client.get('/api/state').json()['conversations']) == 1
        assert write(client, '/api/auth/logout', {}, beta_csrf).status_code == 200

        alpha_csrf = login(client, 'alpha_service', accounts['alpha_service']['password'])
        rows = client.get('/api/state').json()['conversations']
        assert [row['id'] for row in rows] == [alpha.json()['conversation_id']]
        assert beta.json()['conversation_id'] not in [row['id'] for row in rows]
    app.state.db.engine.dispose()


def test_gateway_security_headers_and_secure_cookie_mode(tmp_path, monkeypatch):
    monkeypatch.setenv('CS_COOKIE_SECURE', 'true')
    app = create_app('sqlite:///' + str(tmp_path / 'gateway.db'), auth_accounts=ACCOUNTS)
    with TestClient(app) as client:
        health = client.get('/healthz')
        assert health.headers['x-frame-options'] == 'DENY'
        assert health.headers['referrer-policy'] == 'no-referrer'
        assert "frame-ancestors 'none'" in health.headers['content-security-policy']
        assert health.headers['strict-transport-security'].startswith('max-age=31536000')
        login_response = client.post('/api/auth/login', json={'user_ref': 'service', 'password': ACCOUNTS['service']['password']})
        assert 'Secure' in login_response.headers['set-cookie']
        assert login_response.json()['tenant_id'] == 'local-demo'
    app.state.db.engine.dispose()


def test_authenticated_writes_are_rate_limited_per_tenant_account(tmp_path, monkeypatch):
    monkeypatch.setenv('CS_WRITE_RATE_LIMIT_PER_MINUTE', '10')
    app = create_app('sqlite:///' + str(tmp_path / 'write-rate.db'), auth_accounts=ACCOUNTS)
    with TestClient(app) as client:
        csrf = login(client, 'service', ACCOUNTS['service']['password'])
        for _ in range(10):
            response = write(client, '/api/messages', {
                'question': '合成：核对商品规格。', 'synthetic_or_redacted': True, 'event_id': str(uuid4()),
            }, csrf)
            assert response.status_code == 200
        assert write(client, '/api/messages', {
            'question': '合成：超过操作频率限制。', 'synthetic_or_redacted': True, 'event_id': str(uuid4()),
        }, csrf).status_code == 429
    app.state.db.engine.dispose()
