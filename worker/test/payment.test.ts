import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { decodePaymentRequiredHeader, decodePaymentResponseHeader, encodePaymentSignatureHeader } from "@x402/core/http";
import type { PaymentRequirements } from "@x402/core/types";
import { validateDiscoveryExtension } from "@x402/extensions/bazaar";
import worker from "../src/index";
import { USDC, type Assessment } from "../src/contracts";
import { DATABASE_PATH } from "../src/supabase";
import { QUERY_EVENT_PATH } from "../src/telemetry";

const NOW = Date.parse("2026-09-16T12:00:00Z");
const payer = "0x" + "a".repeat(40);
const transaction = "0x" + "b".repeat(64);
const subject = { chain: "base", protocol: "aave-v3", asset: USDC } as const;
const env = {
  SUPABASE_URL: "https://synthetic.supabase.co", SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  SUPABASE_READER_JWT: `${btoa('{}').replace(/=/g, '')}.${btoa(JSON.stringify({ role: "risk_api_reader", exp: 2_000_000_000 })).replace(/=/g, "")}.synthetic`,
  X402_PAY_TO: "0x" + "1".repeat(40), X402_FACILITATOR_URL: "https://api.cdp.coinbase.com/platform/v2/x402",
  CDP_API_KEY_ID: "synthetic-cdp-key", CDP_API_KEY_SECRET: "", QUERY_PAYER_HMAC_KEY: "23".repeat(32),
};
const terms: PaymentRequirements = {
  scheme: "exact", network: "eip155:8453", asset: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", amount: "10000", payTo: env.X402_PAY_TO,
  maxTimeoutSeconds: 300, extra: { name: "USD Coin", version: "2" },
};
function signature(accepted = terms) {
  return encodePaymentSignatureHeader({ x402Version: 2, accepted, payload: {
    signature: "0x" + "e".repeat(130), authorization: { from: payer, to: env.X402_PAY_TO,
      value: "10000", validAfter: "0", validBefore: "2000000000", nonce: "0x" + "d".repeat(64) },
  } });
}
function request(payment?: string, body: unknown = subject) {
  return new Request("https://api.example.test/v1/risk-check", {
    method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "PRIVATE_USER_AGENT",
      Authorization: "Bearer PRIVATE_CALLER_JWT", ...(payment === undefined ? {} : { "PAYMENT-SIGNATURE": payment }) },
    body: JSON.stringify(body),
  });
}
function assessment(): Assessment {
  return { assessment_id: "00000000-0000-4000-8000-000000000001", ...subject,
    methodology_version: "v0.1", score: 20, risk_level: "low", confidence: 0.5,
    status: "OK", freshness_status: "fresh", reason: null, block_number: "51360621",
    calculated_at: "2026-09-16T11:59:00Z", fresh_until: "2026-09-16T12:20:00Z",
    expires_at: "2026-09-16T12:40:00Z", unknown_after: "2026-09-16T13:25:00Z",
    factors: ["oracle_risk", "liquidity_utilization_risk", "collateral_configuration_risk", "operational_restriction_risk"]
      .map(factor => ({ factor, status: "evaluated", score: 20, explanation: "Synthetic", rule_id: "v0.1:synthetic" })) as Assessment["factors"],
  };
}
let data: Assessment;
let verifyResult: unknown;
let settleResult: unknown;
let failingPath: string | undefined;
let upstreamStatus: number;
let clockAfterVerify: number | undefined;
let clockAfterSettle: number | undefined;
const outbound = vi.fn<typeof fetch>();
const logs = vi.fn();
const calls = (path: string) => outbound.mock.calls.filter(([url]) => new URL(String(url)).pathname.endsWith(path));

beforeAll(async () => {
  // Real official CDP JWT signing in workerd, using a generated test-only key.
  const keys = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", keys.privateKey) as ArrayBuffer);
  env.CDP_API_KEY_SECRET = `-----BEGIN PRIVATE KEY-----\n${btoa(String.fromCharCode(...pkcs8))}\n-----END PRIVATE KEY-----`;
});
beforeEach(() => {
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  logs.mockReset();
  vi.stubGlobal("console", { ...console, log: logs, warn: logs, error: logs });
  data = assessment();
  verifyResult = { isValid: true, payer };
  settleResult = { success: true, payer, transaction, network: "eip155:8453" };
  failingPath = undefined; upstreamStatus = 200; clockAfterVerify = undefined; clockAfterSettle = undefined;
  outbound.mockReset();
  outbound.mockImplementation(async (url, options) => {
    // Native Request construction catches options/runtime incompatibilities that
    // a plain resolved fetch mock would hide. No real network is reachable.
    const native = new Request(url, options);
    expect(native.redirect).toBe("manual");
    expect(native.signal.aborted).toBe(false);
    const path = new URL(native.url).pathname;
    if (path === failingPath) return new Response("PRIVATE_PROVIDER_ERROR", { status: upstreamStatus,
      headers: { Location: "https://untrusted.example.test", "Content-Type": "application/json" } });
    if (path === DATABASE_PATH) return Response.json(data);
    if (path === QUERY_EVENT_PATH) return new Response(null, { status: 204 });
    expect(native.headers.get("Authorization")).toMatch(/^Bearer [^.]+\.[^.]+\.[^.]+$/);
    expect(native.headers.has("apikey")).toBe(false);
    if (path.endsWith("/supported")) return Response.json({ kinds: [{ x402Version: 2, scheme: "exact", network: "eip155:8453" }], extensions: [], signers: {} });
    const body = await native.json() as Record<string, unknown>;
    expect(body.x402Version).toBe(2);
    expect(body.paymentRequirements).toEqual(terms);
    if (path.endsWith("/verify")) {
      if (clockAfterVerify !== undefined) vi.mocked(Date.now).mockReturnValue(clockAfterVerify);
      return Response.json(verifyResult);
    }
    if (path.endsWith("/settle")) {
      if (clockAfterSettle !== undefined) vi.mocked(Date.now).mockReturnValue(clockAfterSettle);
      return Response.json(settleResult);
    }
    throw new Error("Unexpected outbound request");
  });
  vi.stubGlobal("fetch", outbound);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
async function run(req = request(signature()), settings = env) {
  const ctx = createExecutionContext();
  const response = await worker.fetch(req, settings, ctx);
  await waitOnExecutionContext(ctx);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
  const serialized = JSON.stringify([...response.headers]) + await response.clone().text();
  for (const privateValue of ["PRIVATE_PROVIDER_ERROR", "PRIVATE_CALLER_JWT", env.CDP_API_KEY_SECRET, env.SUPABASE_READER_JWT]) {
    expect(serialized).not.toContain(privateValue);
    expect(JSON.stringify(logs.mock.calls)).not.toContain(privateValue);
  }
  return response;
}

describe("x402 paid risk queries in workerd", () => {
  it("returns a standard v2 402 for an unpaid usable query", async () => {
    const response = await run(request());
    expect(response.status).toBe(402);
    const challenge = decodePaymentRequiredHeader(response.headers.get("PAYMENT-REQUIRED")!);
    expect(challenge.x402Version).toBe(2);
    expect(challenge.accepts).toEqual([terms]);
    expect(challenge.extensions).toBeDefined();
    const bazaarExtension = challenge.extensions!.bazaar as Parameters<typeof validateDiscoveryExtension>[0];
    expect(validateDiscoveryExtension(bazaarExtension).valid).toBe(true);
    const bazaar = bazaarExtension as { info?: {
      input?: { type?: string; method?: string; bodyType?: string; body?: unknown };
      output?: { type?: string; example?: unknown };
    } };
    expect(bazaar.info?.input).toMatchObject({ type: "http", method: "POST", bodyType: "json", body: subject });
    expect(bazaar.info?.output).toMatchObject({ type: "json" });
    expect(bazaar.info?.output?.example).toBeDefined();
    expect(await response.json()).toEqual(challenge);
    expect(calls("/verify")).toHaveLength(0); expect(calls("/settle")).toHaveLength(0);
    expect(calls(QUERY_EVENT_PATH)).toHaveLength(0);
  });
  it("settles before returning the unchanged assessment and logs one sanitized event", async () => {
    const response = await run();
    expect(response.status).toBe(200); expect(await response.json()).toEqual(data);
    expect(decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE")!)).toEqual({
      success: true, payer, transaction, network: "eip155:8453", amount: "10000",
    });
    expect(outbound.mock.calls.map(([url]) => new URL(String(url)).pathname)).toEqual([
      DATABASE_PATH, "/platform/v2/x402/supported", "/platform/v2/x402/verify", "/platform/v2/x402/settle", QUERY_EVENT_PATH,
    ]);
    const [url, options] = calls(QUERY_EVENT_PATH)[0];
    expect(url).toBe(env.SUPABASE_URL + QUERY_EVENT_PATH);
    const event = JSON.parse(String(options!.body));
    expect(Object.keys(event)).toHaveLength(14);
    expect(event).toMatchObject({ p_chain: data.chain, p_protocol: data.protocol, p_asset: data.asset,
      p_assessment_id: data.assessment_id, p_methodology_version: data.methodology_version,
      p_score: data.score, p_risk_level: data.risk_level, p_freshness_status: data.freshness_status,
      p_payment_reference: transaction, p_payment_amount_atomic: "10000", p_payment_network: "eip155:8453", p_latency_ms: 0 });
    expect(event.p_event_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const key = await crypto.subtle.importKey("raw", new Uint8Array(32).fill(0x23), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const expected = Array.from(new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payer))), b => b.toString(16).padStart(2, "0")).join("");
    expect(event.p_payer_identifier).toBe(expected);
    for (const secret of [payer, "PRIVATE_USER_AGENT", "PRIVATE_CALLER_JWT", signature(), env.SUPABASE_READER_JWT]) {
      expect(String(options!.body)).not.toContain(secret);
    }
    expect(new Headers(options!.headers).get("Authorization")).toBe(`Bearer ${env.SUPABASE_READER_JWT}`);
  });
  it.each(["invalid header", signature({ ...terms, amount: "1" }), signature({ ...terms, network: "eip155:84532" })])(
    "rejects malformed or mismatched payment %# before verification", async header => {
      expect((await run(request(header))).status).toBe(402);
      expect(calls("/verify")).toHaveLength(0); expect(calls("/settle")).toHaveLength(0);
      expect(calls(QUERY_EVENT_PATH)).toHaveLength(0);
    });
  it("does not settle an invalid payment", async () => {
    verifyResult = { isValid: false, invalidReason: "PRIVATE_PROVIDER_ERROR" };
    expect((await run()).status).toBe(402);
    expect(calls("/settle")).toHaveLength(0); expect(calls(QUERY_EVENT_PATH)).toHaveLength(0);
  });
  it("does not return risk or log after failed settlement", async () => {
    settleResult = { success: false, transaction: "", network: "eip155:8453", errorMessage: "PRIVATE_PROVIDER_ERROR" };
    const response = await run(); expect(response.status).toBe(402);
    const challenge = decodePaymentRequiredHeader(response.headers.get("PAYMENT-REQUIRED")!);
    const body = await response.json();
    expect(body).toEqual(challenge);
    // Bazaar documents a static example, but must not expose the live assessment.
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain(`"score":${data.score}`);
    expect(serialized).not.toContain(data.block_number!);
    expect(serialized).not.toContain(data.calculated_at!);
    expect(calls(QUERY_EVENT_PATH)).toHaveLength(0);
  });
  it.each(["/supported", "/verify", "/settle"])("sanitizes facilitator HTTP failure at %s", async path => {
    failingPath = "/platform/v2/x402" + path; upstreamStatus = 503;
    expect((await run()).status).toBe(503); expect(calls(QUERY_EVENT_PATH)).toHaveLength(0);
  });
  it("rejects facilitator redirects in native Workers requests", async () => {
    failingPath = "/platform/v2/x402/verify"; upstreamStatus = 307;
    expect((await run()).status).toBe(503); expect(calls("/settle")).toHaveLength(0);
    expect(outbound.mock.calls.every(([url]) => !String(url).includes("untrusted"))).toBe(true);
  });
  it.each([{}, { ...subject, chain: "ethereum" }])("rejects malformed/unsupported input before payment: %j", async body => {
    expect((await run(request(signature(), body))).status).toBe(422); expect(outbound).not.toHaveBeenCalled();
  });
  it("rejects malformed JSON before payment", async () => {
    const req = new Request("https://api.example.test/v1/risk-check", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{" });
    expect((await run(req)).status).toBe(422); expect(outbound).not.toHaveBeenCalled();
  });
  it.each(["STALE", "UNKNOWN"] as const)("does not charge an unavailable %s assessment", async status => {
    data = { ...data, status, freshness_status: status === "STALE" ? "stale" : "unknown",
      reason: "EXPIRED_ASSESSMENT", score: null, risk_level: "unknown", confidence: null, factors: [] };
    expect((await run()).status).toBe(503); expect(outbound).toHaveBeenCalledTimes(1);
  });
  it("does not enter payment on database failure", async () => {
    failingPath = DATABASE_PATH; upstreamStatus = 503;
    expect((await run()).status).toBe(503); expect(outbound).toHaveBeenCalledTimes(1);
  });
  it("rechecks freshness after verification and does not settle near expiry", async () => {
    clockAfterVerify = Date.parse(data.expires_at!) - 14_999;
    expect((await run()).status).toBe(503); expect(calls("/settle")).toHaveLength(0);
  });
  it("returns and records the same degraded delivery result", async () => {
    clockAfterVerify = Date.parse(data.fresh_until!);
    const response = await run(); expect(response.status).toBe(200);
    expect((await response.json() as Assessment).freshness_status).toBe("degraded");
    expect(JSON.parse(String(calls(QUERY_EVENT_PATH)[0][1]!.body)).p_freshness_status).toBe("degraded");
  });
  it("preserves a freshness downgrade during settlement in both response and telemetry", async () => {
    clockAfterSettle = Date.parse(data.fresh_until!);
    const response = await run(); expect(response.status).toBe(200);
    expect((await response.json() as Assessment).freshness_status).toBe("degraded");
    expect(JSON.parse(String(calls(QUERY_EVENT_PATH)[0][1]!.body)).p_freshness_status).toBe("degraded");
  });  it.each(["http", "transport", "duplicate"])("keeps the paid 200 on telemetry %s failure", async failure => {
    failingPath = QUERY_EVENT_PATH; upstreamStatus = failure === "duplicate" ? 409 : 500;
    if (failure === "transport") {
      const delegate = outbound.getMockImplementation()!;
      outbound.mockImplementation((url, options) => String(url).endsWith(QUERY_EVENT_PATH)
        ? Promise.reject(new Error("PRIVATE_PROVIDER_ERROR")) : delegate(url, options));
    }
    expect((await run()).status).toBe(200); expect(calls(QUERY_EVENT_PATH)).toHaveLength(1);
  });
  it("uses the same event ID on a repeated confirmed settlement", async () => {
    await run(); await run();
    const events = calls(QUERY_EVENT_PATH).map(([, opts]) => JSON.parse(String(opts!.body)));
    expect(events).toHaveLength(2); expect(events[0].p_event_id).toBe(events[1].p_event_id);
  });
  it.each([
    { success: "true" }, { transaction: "not-a-transaction" }, { network: "eip155:84532" },
    { amount: "1" }, { payer: "0x" + "9".repeat(40) },
  ])("rejects an inconsistent settlement receipt: %j", async change => {
    settleResult = { ...(settleResult as object), ...change };
    expect((await run()).status).toBe(503); expect(calls(QUERY_EVENT_PATH)).toHaveLength(0);
  });
  it.each(["/platform/v2/x402/settle", QUERY_EVENT_PATH])("bounds stalled %s calls without retries", async path => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let entered!: () => void;
    const reached = new Promise<void>(resolve => { entered = resolve; });
    const delegate = outbound.getMockImplementation()!;
    outbound.mockImplementation((url, options) => {
      if (String(url).endsWith(path)) return new Promise((_resolve, reject) => {
        options!.signal!.addEventListener("abort", () => reject(new Error("PRIVATE_PROVIDER_ERROR")));
        entered();
      });
      return delegate(url, options);
    });
    const pending = run();
    await reached;
    await vi.advanceTimersByTimeAsync(path === QUERY_EVENT_PATH ? 3_000 : 15_000);
    expect((await pending).status).toBe(path === QUERY_EVENT_PATH ? 200 : 503);
    expect(calls(path)).toHaveLength(1);
    if (path !== QUERY_EVENT_PATH) expect(calls(QUERY_EVENT_PATH)).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });  it("fails closed before settlement when payer pseudonym configuration is missing", async () => {
    expect((await run(request(signature()), { ...env, QUERY_PAYER_HMAC_KEY: "" })).status).toBe(503);
    expect(calls("/settle")).toHaveLength(0);
  });
});
