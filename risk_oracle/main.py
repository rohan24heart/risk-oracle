from typing import Literal

from fastapi import FastAPI, Response
from pydantic import BaseModel

from risk_oracle.providers.alchemy import AlchemyProvider
from risk_oracle.providers.mock import MockProvider
from risk_oracle.risk_engine import calculate_risk


app = FastAPI(title="Risk Oracle")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class RiskCheckRequest(BaseModel):
    chain: str
    protocol: str
    asset: str


class RiskCheckResponse(BaseModel):
    score: float
    risk_level: str
    confidence: float


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.post("/v1/risk-check", response_model=RiskCheckResponse)
def risk_check(request: RiskCheckRequest) -> RiskCheckResponse:
    inputs = MockProvider().get_inputs(
        chain=request.chain, protocol=request.protocol, asset=request.asset
    )
    return RiskCheckResponse.model_validate(calculate_risk(inputs))


class ProviderHealthResponse(BaseModel):
    provider: Literal["base"] = "base"
    status: Literal["ok", "unavailable"]
    latest_block: int | None = None


@app.get(
    "/v1/providers/base/health",
    response_model=ProviderHealthResponse,
    response_model_exclude_none=True,
    responses={503: {"model": ProviderHealthResponse}},
)
def base_provider_health(response: Response) -> ProviderHealthResponse:
    try:
        latest_block = AlchemyProvider().get_latest_block_number()
    except RuntimeError:
        response.status_code = 503
        return ProviderHealthResponse(status="unavailable")
    return ProviderHealthResponse(status="ok", latest_block=latest_block)
