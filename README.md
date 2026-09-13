# Risk Oracle

A minimal Python FastAPI application. The only application endpoint is
`GET /health`, which returns HTTP 200 with:

```json
{"status": "ok"}
```

## Setup

Requires Python 3.12 or newer.

```sh
python -m venv .venv
```

Activate the virtual environment:

- PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Install the application and test dependencies:

```sh
python -m pip install -e ".[dev]"
```

## Run

```sh
python -m uvicorn risk_oracle.main:app --reload
```

The health endpoint is available at <http://127.0.0.1:8000/health>.

## Test

```sh
python -m pytest
```

This initial project has no database, blockchain integration, x402 payments,
Docker configuration, AI/LLM functionality, or risk engine.

## Production v0 storage (migration only)

`supabase/migrations/20260912180000_precomputed_risk_storage.sql` defines three
private tables. No migration has been applied or Supabase connection made.

- `assessments`: precomputed scores, confidence, freshness, methodology and block
  identity. The future Worker reads this table; requests never trigger RPC/scoring.
- `reserve_snapshots`: complete shared-model JSON, one snapshot per assessment.
  A composite foreign key enforces the assessment's asset and block.
- `evidence_records`: source references, observation timestamps and evidence JSON.
  Never store credential-bearing RPC URLs or provider error bodies.

Future ingestion appends these records in one transaction approximately every
15 minutes. The unique `(chain, protocol, asset, block_number, methodology_version)`
key prevents duplicates; retries reuse existing results without overwriting them.
History preserves the evidence needed to explain and reproduce past assessments.

UUID row IDs are separate from content IDs/hashes preserved inside JSON. Assets
use lowercase token addresses. Block numbers use validated uint256 decimal text.
Serialize JSON through the shared models so large quantities remain decimal
strings; SQL checks object shape and snapshot identity, not the full JSON Schema.

The follow-up migration `20260913000000_canonical_assessment_contract.sql` changes
score to a nullable 0-100 float and confidence to a nullable 0-1 float. Risk levels
are `low`, `moderate`, `high`, `critical`, `unknown`; assessment freshness statuses
are `fresh`, `degraded`, `stale`, `unknown`. Unknown risk has null score and confidence independently of freshness. Only unknown
freshness has null `stale_after`. Snapshot/observation freshness metadata retains its existing
uppercase enum, validated against the lowercase assessment status. The migration
requires an empty assessments table and aborts if legacy history exists: it never
rescales or relabels old results. Neither migration has been applied by this change.
Timestamps use `timestamptz`; `stale_after` maps to `expires_at`. The Worker must
check expiry when serving, returning STALE/UNKNOWN without live fallback. Latest
lookup sorts by `calculated_at DESC, created_at DESC, id DESC` within the requested
chain/protocol/asset and supported methodology version.

RLS is enabled and forced, with no public policies. PUBLIC, anon and authenticated
receive no table privileges. Trusted service_role ingestion receives SELECT/INSERT
only; administrators retain maintenance access. The service role bypasses RLS and
must stay server-side. Worker read-only credentials will be provisioned separately.
See [Supabase RLS guidance](https://supabase.com/docs/guides/database/postgres/row-level-security).


### Snapshot freshness v0

`freshness_policy.snapshot_freshness(observed_at=..., calculated_at=...)` uses only
explicit timezone-aware timestamps: age <=25 minutes is fresh, >25 to <=45 is
degraded, >45 to <=90 is stale, and >90 is unknown. Future observations are rejected.
The collector applies this to snapshot `collected_at` (database `observed_at`).
Observation/price-feed freshness and evidence completeness are separate. No score
is calculated or populated by this policy; fresh data can have unknown risk.
Existing exclusive deadlines use one microsecond past each inclusive boundary;
`unknown_after` preserves the 90-minute transition in serialized freshness metadata.
Migration `20260913010000_independent_assessment_freshness.sql` permits an unscored
assessment with known data freshness. It has not been applied.
