from abc import ABC, abstractmethod

from pydantic import BaseModel


class RiskInputs(BaseModel):
    oracle_risk: float
    liquidity_risk: float
    liquidation_risk: float


class RiskProvider(ABC):
    @abstractmethod
    def get_inputs(self, *, chain: str, protocol: str, asset: str) -> RiskInputs:
        """Return risk factors for the requested chain, protocol, and asset."""
