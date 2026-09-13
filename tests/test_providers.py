from risk_oracle.providers.base import RiskInputs, RiskProvider
from risk_oracle.providers.mock import MockProvider


def test_mock_provider() -> None:
    provider: RiskProvider = MockProvider()

    inputs = provider.get_inputs(chain="base", protocol="aave-v3", asset="USDC")

    assert isinstance(inputs, RiskInputs)
    assert inputs.model_dump() == {
        "oracle_risk": 40,
        "liquidity_risk": 60,
        "liquidation_risk": 50,
    }
