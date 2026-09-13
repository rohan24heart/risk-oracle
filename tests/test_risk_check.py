import pytest
from fastapi.testclient import TestClient

from risk_oracle.main import app
from risk_oracle.providers.base import RiskInputs
from risk_oracle.providers.mock import MockProvider


def test_risk_check() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/risk-check",
            json={"chain": "base", "protocol": "aave-v3", "asset": "USDC"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "score": 50,
        "risk_level": "high",
        "confidence": 0.5,
    }


def test_risk_check_uses_provider_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []

    def get_inputs(
        self: MockProvider, *, chain: str, protocol: str, asset: str
    ) -> RiskInputs:
        requests.append({"chain": chain, "protocol": protocol, "asset": asset})
        return RiskInputs(oracle_risk=10, liquidity_risk=20, liquidation_risk=30)

    monkeypatch.setattr(MockProvider, "get_inputs", get_inputs)
    payload = {"chain": "base", "protocol": "aave-v3", "asset": "USDC"}

    with TestClient(app) as client:
        response = client.post("/v1/risk-check", json=payload)

    assert requests == [payload]
    assert response.status_code == 200
    assert response.json() == {
        "score": 20.0,
        "risk_level": "low",
        "confidence": 0.5,
    }
