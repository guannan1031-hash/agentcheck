from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app


def message(client):
    response = client.post('/api/messages', json={'question': '合成：400克规格没有货，可以怎么处理？', 'synthetic_or_redacted': True, 'event_id': str(uuid4())})
    assert response.status_code == 200
    return response.json()['conversation_id']


def test_stockout_evaluation_keeps_source_time_and_finds_safe_substitute(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'inventory.db'))
    with TestClient(app) as client:
        snapshot = client.post('/api/inventory/snapshots', json={
            'source': '合成库存快照', 'captured_at': '2026-09-14T10:00:00+08:00', 'synthetic_or_redacted': True,
            'items': [
                {'sku': 'MOCHA-400', 'name': '摩可纳400g', 'product_group': '摩可纳', 'grams': 400, 'sellable_qty': 0, 'warehouse_qty': 0, 'in_transit_qty': 0},
                {'sku': 'MOCHA-200', 'name': '摩可纳200g', 'product_group': '摩可纳', 'grams': 200, 'sellable_qty': 5, 'warehouse_qty': 5, 'in_transit_qty': 0},
            ]}).json()
        cid = message(client)
        result = client.post('/api/inventory/evaluate', json={'conversation_id': cid, 'expected_revision': 1, 'sku': 'MOCHA-400', 'target_grams': 400})
        assert result.status_code == 200
        body = result.json()
        assert body['status'] == 'out_of_stock' and body['alternatives'][0]['units'] == 2
        assert body['evidence'][0]['sellable_qty'] == 0 and body['captured_at'] == snapshot['captured_at']
        evaluations = client.get('/api/inventory/state').json()['evaluations']
        assert evaluations[0]['sku'] == 'MOCHA-400'
    app.state.db.engine.dispose()


def test_inventory_rejects_duplicate_items(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'duplicate.db'))
    with TestClient(app) as c:
        assert c.post('/api/inventory/snapshots', json={'source': '合成', 'captured_at': '2026-09-14', 'synthetic_or_redacted': True, 'items': [
            {'sku': 'A', 'name': '商品', 'product_group': '组', 'grams': 200, 'sellable_qty': 1, 'warehouse_qty': 1, 'in_transit_qty': 0},
            {'sku': 'A', 'name': '商品', 'product_group': '组', 'grams': 200, 'sellable_qty': 1, 'warehouse_qty': 1, 'in_transit_qty': 0}]}).status_code == 422
    app.state.db.engine.dispose()
