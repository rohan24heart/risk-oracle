from risk_oracle.providers.base import RiskInputs, RiskProvider


class MockProvider(RiskProvider):
    def get_inputs(self, *, chain: str, protocol: str, asset: str) -> RiskInputs:
        return RiskInputs(
            oracle_risk=40,
            liquidity_risk=60,
            liquidation_risk=50,
        )
