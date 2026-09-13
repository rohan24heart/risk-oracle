import httpx
import pytest
from fastapi.testclient import TestClient

from risk_oracle.config import Settings
from risk_oracle.main import app
from risk_oracle.providers import alchemy


@pytest.mark.parametrize("scenario", ["ok", "http_error", "rpc_error", "timeout", "invalid_json", "missing_config"])
def test_base_provider_health(monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    rpc_url = "https://rpc.example.test/private-test-key"
    monkeypatch.setattr(
        alchemy,
        "load_settings",
        lambda: Settings(base_rpc_url=None if scenario == "missing_config" else rpc_url),
    )
    requests = []

    def post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        requests.append(json)
        assert url == rpc_url
        assert timeout == 10.0
        request = httpx.Request("POST", url)
        if scenario == "timeout":
            raise httpx.ReadTimeout(rpc_url, request=request)
        if scenario == "invalid_json":
            return httpx.Response(200, text=rpc_url, request=request)
        payload = {"jsonrpc": "2.0", "id": 1, "result": "0x7b"}
        if scenario == "rpc_error":
            payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": rpc_url}}
        return httpx.Response(503 if scenario == "http_error" else 200, json=payload, request=request)

    monkeypatch.setattr(alchemy.httpx, "post", post)
    with TestClient(app) as client:
        response = client.get("/v1/providers/base/health")

    if scenario == "ok":
        assert response.status_code == 200
        assert response.json() == {"provider": "base", "status": "ok", "latest_block": 123}
    else:
        assert response.status_code == 503
        assert response.json() == {"provider": "base", "status": "unavailable"}
    assert "private-test-key" not in response.text
    assert requests == ([] if scenario == "missing_config" else [
        {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
    ])
