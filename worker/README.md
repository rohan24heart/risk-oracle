# Precomputed risk API Worker

A single route: `POST /v1/risk-check`. The assessment is read only from
`/rest/v1/rpc/get_latest_risk_assessment` on the configured Supabase project.
Usable results require x402 v2 settlement of $0.01 (10,000 atomic units) native USDC on Base mainnet. The app contains no collector, scorer, blockchain client or scheduler.

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
  The role can execute the serving function but cannot access raw tables. After the query-events migration it can also execute the restricted telemetry insert RPC.

Do not supply an ingestion key, `sb_secret_...`, a service-role JWT, or a JWT
signing key. An ordinary authenticated-user JWT is also insufficient. The app
checks the configured role/expiry to catch mistakes; Supabase verifies the signature
and enforces grants. Rotate the reader JWT before expiry. Caller credentials are
never forwarded. No credentials are included in Wrangler configuration.

For local development, copy `.dev.vars.example` to the ignored `.dev.vars` and
populate it locally. Tests use only synthetic credentials, generated test signing keys, and mocked upstream responses.

## Responses

Successful responses preserve the database function's public JSON projection:
assessment ID, subject, v0.1 methodology, stored score/risk level/confidence,
delivery/freshness status, block number as an exact decimal string, calculation
and freshness timestamps, and four bounded factor findings. Confidence is **as of
calculated_at**, not recalculated at delivery.

| HTTP | Meaning |
| --- | --- |
| 200 | Successfully settled `OK` or `DEGRADED` precomputed result |
| 402 | x402 v2 payment required or rejected; see `PAYMENT-REQUIRED` |
| 503 | Unusable assessment, upstream/configuration failure, unconfirmed settlement, or insufficient time before expiry |
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
mocks. Serving contract tests simulate successful payment; payment tests exercise the real x402 resource server and CDP authentication helper, including native workerd Request construction. The compatibility
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

## x402 v2 payments

The plain Worker handler validates the request and retrieves/validates the
assessment before entering payment. Unsupported or malformed requests, database
failures, and stale/unknown results never reach the facilitator. Both `OK` and
`DEGRADED` results remain usable, as in the existing API. Successful payment
preserves the assessment JSON schema and adds a `PAYMENT-RESPONSE` receipt header.
An unpaid usable request receives a v2 `PAYMENT-REQUIRED` header and matching JSON
challenge. Clients retry with `PAYMENT-SIGNATURE`; legacy v1 headers are not accepted.

Pinned official packages are `@x402/core` and `@x402/evm` 2.26.0 and
`@coinbase/x402` 2.1.0. `x402ResourceServer` and `ExactEvmScheme` build and match
terms for `exact`, `eip155:8453`, `$0.01`, native USDC, and EIP-3009. No Hono
middleware or local cryptographic payment verifier is used. Coinbase's external
production facilitator performs verification and settlement, authenticated using
the official CDP helper. `nodejs_compat` supports that helper's runtime dependencies.

The small facilitator HTTP adapter rejects redirects, bounds response reads at
64 KiB, and projects response fields. The SDK default HTTP adapter follows
redirects and can log extension response data, which does not fit this service's
privacy rules. No upstream error details are returned or logged. There are no
automatic verify/settle retries or live risk-data fallbacks.

Configure before deployment:

| Variable/secret | Value |
| --- | --- |
| `X402_PAY_TO` | Your nonzero EVM USDC receiver address; no private wallet key |
| `X402_FACILITATOR_URL` | `https://api.cdp.coinbase.com/platform/v2/x402` |
| `CDP_API_KEY_ID` | Secret: production CDP API key ID with facilitator access |
| `CDP_API_KEY_SECRET` | Secret: corresponding CDP signing key; official helper supports EC or Ed25519 |
| `QUERY_PAYER_HMAC_KEY` | Secret: 32 random bytes encoded as 64 hex characters |

The current adapter intentionally accepts only the CDP endpoint, so CDP credentials
cannot be sent to another host. Switching providers requires an explicit auth
adapter change. Keep all existing Supabase secrets with the `risk_api_reader`
role; no service-role key is introduced. Missing payment/HMAC configuration fails
closed for otherwise usable requests.

Capability discovery and verification each time out after 5 seconds; settlement
times out after 15 seconds. Freshness is rechecked before charging and after
settlement. Before initiating settlement, the assessment must have at least the
settlement timeout remaining before expiry; otherwise the request receives a
sanitized 503 without settlement. No assessment deadline is extended.

## Settled-query telemetry

After confirmed settlement and a successful response, `ctx.waitUntil` calls only
`/rest/v1/rpc/record_query_event` with the exact delivered assessment summary.
The payer is lowercase-address HMAC-SHA256 using `QUERY_PAYER_HMAC_KEY`; the raw
wallet is never persisted. Only the RPC's 14 allowlisted parameters are sent,
including exact decimal atomic amount, payment network, transaction and latency
from request start to response preparation. No request metadata, signatures,
credentials, provider errors, or response extensions enter telemetry.

A domain-separated SHA-256 of network and normalized settlement transaction yields
a stable UUIDv8. The deployed primary key prevents duplicate rows when the same
settlement is reported again, including across isolates and HMAC-key rotations.
This assumes the normal exact EVM flow has one payment per settlement transaction.
Unpaid/rejected requests create no events. Telemetry is best effort, bounded to
3 seconds, and never changes a paid response; duplicate/HTTP/network failures are
contained and their bodies are discarded. There is no durable retry queue.

## Before production deployment

Provision the receiver and CDP production access/keys and generate the HMAC secret.
The query-events migration must already be applied. This change does not deploy,
change database permissions, or configure a wallet. Then test a real Base-mainnet
query using an official v2 client: check the 10,000-unit USDC transfer to the
receiver, receipt, unchanged risk response and exactly one pseudonymized row.
Also confirm CDP credentials, account limits and fees for the production account.

Mocks cannot prove real authorization acceptance, settlement confirmation latency,
or facilitator replay behavior. Payment settlement and HTTP delivery are not an
atomic transaction: a settlement timeout or lost response can occur after a chain
transfer. The Worker fails closed, does not automatically resettle, and does not
invent a receipt/event for an unconfirmed result. Reconcile an indeterminate
payment before authorizing a new payment. Stable telemetry IDs deduplicate known
settlements; they do not provide a durable response cache or guarantee telemetry
delivery if the logging attempt fails.

Official references: [x402 seller guide](https://docs.x402.org/getting-started/quickstart-for-sellers),
[CDP production seller guide](https://docs.cdp.coinbase.com/x402/seller/quickstart),
[x402 TypeScript SDK](https://github.com/x402-foundation/x402/tree/main/typescript/packages).
