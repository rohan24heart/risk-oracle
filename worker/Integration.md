# Risk API - Agent Integration

Deterministic risk assessment for native USDC on Aave V3 Base.

## Endpoint

POST https://risk-oracle-api.rohan-rajnikanth.workers.dev/v1/risk-check

## Request

```json
{
  "chain": "base",
  "protocol": "aave-v3",
  "asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
}
```

## Payment

Price: $0.01 USDC on Base via x402.

### Flow

1. Agent sends request.
2. API returns HTTP 402 with payment requirements.
3. Agent completes the x402 payment.
4. Agent retries the request with payment proof.
5. API returns HTTP 200 with the risk assessment.

No API key is required.

## What the agent receives

The response includes:

- risk score: 0-100
- risk level
- confidence
- data freshness
- oracle risk
- liquidity/utilization risk
- collateral configuration risk
- operational restrictions

Higher scores indicate more observed stress or restriction. The score is not a probability of loss.

## Example use

Before an agent supplies USDC to Aave V3 on Base:

1. Call the Risk API.
2. Read the current assessment.
3. Use the result as an input to the agent's allocation policy.
4. Proceed, reduce exposure, or abstain according to the agent's own rules.

Risk data is precomputed and served deterministically. Customer requests do not trigger live RPC calls or scoring.

## Discovery

The API is indexed in Coinbase Bazaar and registered on x402scan.

OpenAPI schema:

https://risk-oracle-api.rohan-rajnikanth.workers.dev/openapi.json