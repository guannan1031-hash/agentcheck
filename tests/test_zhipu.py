import json
import httpx
import pytest
from backend.model import DeepSeek, ModelUnavailable

@pytest.mark.parametrize('method', ['complete', 'review_ticket'])
def test_zhipu_contract(monkeypatch, method):
    monkeypatch.setenv('CS_MODEL_PROVIDER', 'zhipu')
    monkeypatch.setenv('CS_MODEL_API_KEY', 'synthetic-key')
    monkeypatch.delenv('CS_MODEL_NAME', raising=False)
    expected = ({'answer': '请核对商品规格。', 'evidence_ids': ['K1'], 'needs_human': False}
                if method == 'complete' else {'category': 'stock', 'approve': True, 'needs_human': False})
    def handler(request):
        assert str(request.url) == 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
        payload = json.loads(request.content)
        assert payload['model'] == 'glm-4.7-flash'
        assert payload['thinking'] == {'type': 'disabled'}
        assert payload['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(expected)}}]})
    provider = DeepSeek(httpx.MockTransport(handler))
    result = provider.complete('合成咨询', [{'label': 'K1'}], [])[0] if method == 'complete' else provider.review_ticket('合成库存核实', 'stock')
    assert result == expected


def test_invalid_provider_fails_closed(monkeypatch):
    monkeypatch.setenv('CS_MODEL_PROVIDER', 'invalid')
    with pytest.raises(ModelUnavailable, match='MODEL_PROVIDER_INVALID'):
        DeepSeek()
