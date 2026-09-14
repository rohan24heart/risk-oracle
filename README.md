# Risk Oracle

An experimental, deterministic reserve-condition risk index for **native USDC on
Aave V3 Base**. Scores range from 0 to 100; higher means more observed stress or
restriction, not a probability of loss or a guarantee of investment safety.

## Current status

- Live Base JSON-RPC integration through `AlchemyProvider` is implemented.
- The Aave V3 Base collector reads native-USDC reserve configuration, balances,
  liquidity, caps, and oracle data at one hash-pinned block.
- Chainlink oracle provenance includes source/adapter, proxy and aggregator
  addresses, verified metadata, feed timestamps, heartbeat and deviation settings.
- **v0.1 scoring is implemented**, with four explainable factors: oracle risk,
  liquidity/utilization risk, collateral/configuration risk, and
  operational/restriction risk. No LLM or mock scorer runs in this live scoring path.
- Shared models preserve provenance, block references, timestamps, units and exact
  large integers. Missing required evidence produces UNKNOWN with null score and
  confidence. Confidence is calculated separately from score.
- Supabase persistence is implemented. The three production migrations in
  [supabase/migrations](supabase/migrations/) have been applied manually to Supabase.
- One real native-USDC **v0.1** assessment, its snapshot and 47 related evidence
  records were successfully stored and verified at Base block **51,274,699**.

Oracle risk measures feed freshness/integrity and verified adapter behavior,
**not independent economic accuracy of USDC's market price**. See the
[v0.1 methodology](docs/methodology-v0.1.md) for formulas and limitations.

## Freshness

The implemented policy uses explicit timestamps:

| Age | Status |
| --- | --- |
| Up to 25 minutes | fresh |
| Over 25 through 45 minutes | degraded |
| Over 45 through 90 minutes | stale |
| Over 90 minutes | unknown |

The scorer also checks block age so a recent collection cannot make old state
appear fresh. Stale/unknown snapshots do not receive a current actionable score.
Snapshot freshness is separate from feed freshness and evidence completeness.

## Storage and planned production architecture

Supabase stores private tables with RLS enabled:

- `assessments`: precomputed scores, confidence, freshness, methodology and block.
- `reserve_snapshots`: full shared-model snapshot JSON.
- `evidence_records`: provenance and raw evidence, including the complete assessment
  with factor scores and explanations.

History supports reproducibility and auditing. The writer performs separate inserts,
not one atomic transaction; partial failures require reconciliation before serving.

The production ingestion entrypoint and [GitHub Actions workflow](.github/workflows/ingest.yml)
are implemented. The workflow runs `python -m risk_oracle.ingest` on Python 3.12,
scheduled every 15 minutes (`7,22,37,52 * * * *`, UTC), with manual dispatch support.
It performs one collection/snapshot, real v0.1 assessment, and persistence cycle.
Valid UNKNOWN assessments remain UNKNOWN. Failures exit nonzero with sanitized
stage diagnostics. First-table HTTP 408/503/504 and transport failures receive up
to three identical attempts, with exponential backoff plus jitter. Validation,
authentication, constraint and partial-write failures are not retried. Existing
unique keys prevent duplicate first-table inserts; an ambiguous-commit conflict
stops the cycle rather than being assumed successful.

To activate it, publish the workflow on the repository's default branch, enable
Actions, and configure GitHub Secrets `BASE_RPC_URL`, `SUPABASE_URL`, and
`SUPABASE_SERVICE_KEY`. Manual runs are restricted to the default branch. One
concurrency group prevents overlapping scheduled/manual runs without cancelling
an active writer. The job times out after 10 minutes (ingestion step: 8 minutes).
Scheduled runs can be delayed; freshness remains authoritative. After a partial or
ambiguous write failure, reconcile stored rows before rerunning; duplicates are
rejected by the existing database constraint.

The workflow installs project runtime dependencies only, uses commit-pinned
checkout/setup actions, and exposes secrets only to the ingestion step. No live
workflow run was performed during implementation.

Cloudflare Worker serving and x402 payment gating remain planned. The Worker will
read precomputed Supabase results only, returning STALE/UNKNOWN without live RPC
or scoring fallback. Planned payments settle in USDC on Base to a controlled wallet.

## Local development

Requires Python 3.12 or newer. Create and activate a virtual environment, then install:

```sh
python -m venv .venv
python -m pip install -e ".[dev]"
```

Configure `BASE_RPC_URL`, `SUPABASE_URL`, and `SUPABASE_SERVICE_KEY` in the environment
or a local `.env` file. Environment variables take precedence. Keep credentials
private; `.env` is gitignored and `.env.example` contains no credentials.

FastAPI remains local development/test infrastructure:

- `GET /health`: application health.
- `GET /v1/providers/base/health`: live Base RPC reachability.
- `POST /v1/risk-check`: legacy mock-backed demo, **not the real v0.1 scoring path**.

Use `AaveV3BaseCollector.collect(...)`, then
`scoring_v01.assess_snapshot(snapshot, calculated_at=...)` for real assessments.
Persistence is a separate explicit `SupabaseWriter.write(..., methodology_version="v0.1")`
operation.

```sh
python -m uvicorn risk_oracle.main:app --reload
python -m pytest -q
```

Tests use mocked/offline inputs; they do not require live RPC or Supabase access.
