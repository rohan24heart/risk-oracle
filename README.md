# Risk Oracle

An experimental, deterministic reserve-condition risk index for **native USDC on Aave V3 Base**.

Scores range from 0 to 100; higher means more observed stress or restriction. A score is **not** a probability of loss, investment advice, or a guarantee of safety.

Risk API v0.1 is a live, agent-consumable risk primitive designed to be queried programmatically before an automated system moves funds.

## Current status

- Live Base JSON-RPC integration through `AlchemyProvider` is implemented.
- The Aave V3 Base collector reads native-USDC reserve configuration, balances, liquidity, caps, and oracle data at one hash-pinned block.
- Chainlink oracle provenance includes source/adapter, proxy and aggregator addresses, verified metadata, feed timestamps, heartbeat, and deviation settings.
- **v0.1 deterministic scoring is live**, with four explainable factors:
  - oracle risk
  - liquidity/utilization risk
  - collateral/configuration risk
  - operational/restriction risk
- No LLM or mock scorer runs in the production scoring path.
- Shared models preserve provenance, block references, timestamps, units, and exact large integers.
- Missing required evidence produces UNKNOWN with null score and confidence. Confidence is calculated separately from score.
- Scheduled ingestion precomputes assessments before they are served.
- Customer requests do **not** trigger live RPC collection or scoring.
- Supabase persistence is live for assessments, reserve snapshots, evidence records, and paid-query telemetry.
- The production Cloudflare Worker is live.
- The API is protected by **x402** and charges **$0.01 USDC on Base** per successful paid query.
- Missing, stale, or unavailable assessments fail closed before payment is accepted.
- The complete production payment flow has been validated with both a human-controlled wallet and a fully headless software-agent payer.
- **Risk API v0.1** is preserved in Git as the `risk-api-v0.1` tag.

Oracle risk measures feed freshness/integrity and verified adapter behavior, **not independent economic accuracy of USDC's market price**.

See the [v0.1 methodology](docs/methodology-v0.1.md) for formulas and limitations.

## Public API

Production endpoint:

```text
POST https://risk-oracle-api.rohan-rajnikanth.workers.dev/v1/risk-check
```

Current supported request:

```json
{
  "chain": "base",
  "protocol": "aave-v3",
  "asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
}
```

The asset is native USDC on Base.

### Payment

A valid, usable request requires:

- **x402**
- **USDC**
- **Base**
- **$0.01 per successful query**

An unpaid valid request returns:

```text
402 Payment Required
```

An x402-capable client can read the payment requirements, authorize the payment, retry the request, and receive the risk assessment automatically.

The production flow is:

```text
client / agent
-> POST /v1/risk-check
-> HTTP 402
-> USDC payment authorization
-> facilitator verification / settlement
-> automatic retry
-> HTTP 200 risk assessment
```

This flow has been validated end-to-end with a fully headless software client requiring **no browser wallet and no human approval during execution**.

## API behavior

Production request semantics are intentionally strict:

| Condition | Response |
| --- | --- |
| Malformed JSON | `422` |
| Unsupported input | `422` |
| Wrong content type | `415` |
| Query string supplied | `422` |
| Assessment stale or unavailable | `503` before charging |
| Valid usable request without payment | `402 Payment Required` |
| Valid request with successful x402 payment | `200` |

Responses include:

```text
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

A customer query never triggers live RPC access or on-demand scoring.

## Freshness

The implemented policy uses explicit timestamps:

| Age | Status |
| --- | --- |
| Up to 25 minutes | fresh |
| Over 25 through 45 minutes | degraded |
| Over 45 through 90 minutes | stale |
| Over 90 minutes | unknown |

The scorer also checks block age so a recent collection cannot make old state appear fresh.

Stale or unknown snapshots do not receive a current actionable score.

Snapshot freshness is separate from feed freshness and evidence completeness.

## Production architecture

```text
Alchemy / Base / Aave / Chainlink
-> scheduled ingestion
-> deterministic v0.1 scoring
-> Supabase
-> Cloudflare Worker
-> x402 payment gate
-> paid JSON response
-> query_events telemetry
```

### Ingestion

The production ingestion entrypoint and [GitHub Actions workflow](.github/workflows/ingest.yml) run on Python 3.12.

The workflow is scheduled every 15 minutes:

```text
7,22,37,52 * * * *
```

It performs one collection/snapshot, one deterministic v0.1 assessment, and one persistence cycle.

Valid UNKNOWN assessments remain UNKNOWN.

Failures exit nonzero with sanitized stage diagnostics.

Selected HTTP 408/503/504 and transport failures receive bounded retries with exponential backoff and jitter. Validation, authentication, constraint, and partial-write failures are not blindly retried.

Existing database constraints protect against duplicate assessment writes, and ambiguous commit states require reconciliation rather than being assumed successful.

One concurrency group prevents overlapping scheduled/manual ingestion runs without cancelling an active writer.

Freshness remains authoritative even if a scheduled run is delayed.

### Storage

Supabase stores private tables with RLS enabled:

- `assessments`
  - precomputed score
  - confidence
  - freshness
  - methodology version
  - Base block reference
- `reserve_snapshots`
  - full shared-model snapshot JSON
- `evidence_records`
  - provenance
  - raw evidence
  - assessment factors and explanations
- `query_events`
  - successful paid-query telemetry

History supports reproducibility, auditing, and methodology validation.

The assessment writer performs separate inserts rather than one database transaction, so partial failures must be reconciled before affected state is served.

## Paid-query telemetry

Telemetry exists to verify payment execution, measure service performance, audit paid-query behavior, and support future reliability analysis.

A successful paid query records fields such as:

- event ID
- timestamp
- chain
- protocol
- asset
- assessment ID
- methodology version
- score
- risk level
- freshness status
- pseudonymous payer identifier
- payment reference
- payment amount
- payment network
- request latency

The payer identifier is **HMAC-pseudonymized** before storage.

The telemetry layer is intentionally designed **not** to store:

- raw IP addresses
- raw user-agent strings
- JWTs
- private keys
- wallet secrets
- authorization headers
- full request headers

Telemetry is written for **successful paid queries only**.

The API does not require a conventional user account or personal profile in order to query the risk endpoint.

## Headless agent example

A reference x402 client is available at:

```text
examples/x402-agent-client
```

It demonstrates a fully automated software client that:

1. sends the risk request
2. receives the x402 challenge
3. parses the payment requirements
4. checks the payment against local policy
5. refuses to sign if the challenge does not match expected constraints
6. signs the USDC authorization programmatically
7. retries automatically
8. receives and parses the HTTP 200 risk assessment

The example currently enforces:

- Base network: `eip155:8453`
- x402 `exact` scheme
- native Base USDC
- payment amount: `10000` atomic USDC units (`$0.01`)
- expected payment receiver

This is intended as a developer integration reference, not as a production key-management system.

Never commit an agent payer private key.

## Local development

Requires Python 3.12 or newer.

Create and activate a virtual environment, then install:

```sh
python -m venv .venv
python -m pip install -e ".[dev]"
```

Configure:

```text
BASE_RPC_URL
SUPABASE_URL
SUPABASE_SERVICE_KEY
```

in the environment or a local `.env` file.

Environment variables take precedence.

Keep credentials private. `.env` is gitignored and `.env.example` must contain placeholders only.

### Python development infrastructure

FastAPI remains available for local development and testing:

- `GET /health`
- `GET /v1/providers/base/health`
- local development/test risk routes

The production paid API is the Cloudflare Worker endpoint documented above. Do not treat the local FastAPI implementation as the production payment path.

For direct collection and scoring:

```python
AaveV3BaseCollector.collect(...)
scoring_v01.assess_snapshot(snapshot, calculated_at=...)
```

Persistence is an explicit operation:

```python
SupabaseWriter.write(..., methodology_version="v0.1")
```

Run locally with:

```sh
python -m uvicorn risk_oracle.main:app --reload
python -m pytest -q
```

Python tests use mocked/offline inputs unless a test explicitly requires external infrastructure.

## Worker development

The production API Worker lives under:

```text
worker/
```

Useful commands:

```powershell
cd worker
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

The v0.1 production snapshot passed:

- **124 tests**
- TypeScript typechecking
- Cloudflare Worker dry-run build
- real x402 payment settlement
- real HTTP 200 paid response
- successful paid-query telemetry
- fully headless-agent payment/query execution

## Versioning

The first validated paid production baseline is tagged:

```text
risk-api-v0.1
```

That tag represents the known-good version that successfully completed the full production x402 payment and agent-query flow.

Future changes can evolve independently while preserving this rollback and audit point.
