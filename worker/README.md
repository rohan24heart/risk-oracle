# Precomputed risk API Worker

A single route: `POST /v1/risk-check`. This app reads only
`/rest/v1/rpc/get_latest_risk_assessment` on the configured Supabase project.
It contains no collector, scorer, blockchain client, scheduler, payment path.

## Request

Send `Content-Type: application/json` and exactly these fields:

```json
{
  "chain": "base",
  "protocol": "aave-v3",
  "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
}
```

Address hex casing is normalized. Symbols, other subjects, extra fields and query
parameters are rejected. Request bodies are limited to 4 KiB.

## Database access and secrets

The prerequisite is `supabase/migrations/20260916000000_precomputed_serving.sql`.
Applying it and provisioning credentials are separate operator tasks; this app
never applies migrations or signs JWTs.

Configure these Worker secrets through Cloudflare's dashboard or interactive
`wrangler secret put NAME` during an authorized deployment:

- `SUPABASE_URL`: canonical project origin, `https://PROJECT_REF.supabase.co`.
  Custom domains, alternate paths, ports and non-HTTPS endpoints are rejected.
- `SUPABASE_PUBLISHABLE_KEY`: the project's `sb_publishable_...` gateway key,
  sent as `apikey`. This is not the database reader credential.
- `SUPABASE_READER_JWT`: a separately provisioned, unexpired JWT signed by a key
  trusted by this project's PostgREST, with `role: "risk_api_reader"` and `exp`.
  The role can execute the serving function but cannot read raw tables or write data.

Do not supply an ingestion key, `sb_secret_...`, a service-role JWT, or a JWT
signing key. An ordinary authenticated-user JWT is also insufficient. The app
checks the configured role/expiry to catch mistakes; Supabase verifies the signature
and enforces grants. Rotate the reader JWT before expiry. Caller credentials are
never forwarded. No credentials are included in Wrangler configuration.

For local development, copy `.dev.vars.example` to the ignored `.dev.vars` and
populate it locally. Tests use only synthetic credentials and mocked database responses.

## Responses

Successful responses preserve the database function's public JSON projection:
assessment ID, subject, v0.1 methodology, stored score/risk level/confidence,
delivery/freshness status, block number as an exact decimal string, calculation
and freshness timestamps, and four bounded factor findings. Confidence is **as of
calculated_at**, not recalculated at delivery.

| HTTP | Meaning |
| --- | --- |
| 200 | `OK` or `DEGRADED` precomputed result |
| 503 | `STALE`/`UNKNOWN`, unavailable database/configuration, or invalid database response |
| 422 | Invalid JSON/body or unsupported request |
| 415 | JSON content type required |
| 404 / 405 | Unknown route / unsupported method |
| 500 | Sanitized unexpected error |

Every response has `Cache-Control: no-store`. Non-actionable responses have null
score/confidence, `risk_level: "unknown"`, and no factors. Database errors and
exception details are never returned or logged. The database response is bounded
at 64 KiB, schema-checked, and checked against its original deadlines at delivery
with microsecond precision. The Worker can downgrade but never upgrade a stored
unavailable result. It never extends deadlines, computes risk, retries, refreshes,
or falls back to an old cached result. Database reads time out after five seconds;
redirects are forbidden. The outbound request uses `redirect: "manual"` and
rejects every status other than HTTP 200. Workers rejects `redirect: "error"`
during request construction, before contacting the upstream.

## Local checks

Use Node 22 or newer, from `worker/`:

```sh
npm ci
npm run typecheck
npm test
npm run build
```

Tests run in Cloudflare's Workers runtime with all outbound fetches replaced by
mocks; each call must target the one Supabase function. The compatibility
regression passes the app's actual outbound options through the native Workers
`Request` constructor, which a plain fetch stub would otherwise bypass. Redirect
response tests also require a single outbound call and a non-actionable result. `build` performs only a
Wrangler dry-run bundle into ignored `dist/`; it does not deploy. `npm run dev`
starts a local Worker and requires the restricted credentials for real DB reads.

No custom domain/routes are configured. `workers_dev` is enabled; preview URLs
are disabled.
The serving app has no cron trigger; it does not change the external scheduler.

References: [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/),
[Supabase API keys](https://supabase.com/docs/guides/api/api-keys),
[Worker testing](https://developers.cloudflare.com/workers/testing/vitest-integration/).

## Diagnostic logging

Failures inside `latestAssessment()` emit `console.error` messages containing
only `risk_api_stage_failure=STAGE`. Fixed stages are `CONFIGURATION`,
`SUPABASE_FETCH_TIMEOUT` (only after our five-second controller abort),
`SUPABASE_FETCH_ERROR` (all other fetch exceptions), `SUPABASE_HTTP_STATUS` (including content-type rejection),
`RESPONSE_READ` (including JSON decoding), and `RESPONSE_VALIDATION`.
No exception details, credentials, URLs or bodies are logged. Successful reads
and valid STALE/UNKNOWN database results do not emit failure markers.
