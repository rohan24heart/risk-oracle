import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { USDC, type Assessment } from "../src/contracts";
import { DATABASE_PATH, type Env } from "../src/supabase";

const NOW = Date.parse("2026-09-16T12:00:00.000Z");
const input = { chain: "base", protocol: "aave-v3", asset: USDC } as const;
const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
const token = (role = "risk_api_reader", exp = 2_000_000_000) => `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ role, exp })}.syntheticSignature`;
const env: Env = {
  SUPABASE_URL: "https://synthetic.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_synthetic",
  SUPABASE_READER_JWT: token(),
};
const request = (body: unknown = input, path = "/v1/risk-check", method = "POST") => new Request(`https://api.example.test${path}`, {
  method, headers: { "Content-Type": "application/json", Authorization: "Bearer CALLER_MUST_NOT_FORWARD", apikey: "CALLER_KEY" },
  ...(method === "POST" ? { body: JSON.stringify(body) } : {}),
});
function assessment(): Assessment {
  return {
    assessment_id: "00000000-0000-4000-8000-000000000001", ...input,
    methodology_version: "v0.1", score: 20, risk_level: "low", confidence: 0.5,
    status: "OK", freshness_status: "fresh", reason: null,
    block_number: "51360621", calculated_at: "2026-09-16T11:59:00+00:00",
    fresh_until: "2026-09-16T12:20:00+00:00", expires_at: "2026-09-16T12:40:00+00:00",
    unknown_after: "2026-09-16T13:25:00+00:00",
    factors: ["oracle_risk", "liquidity_utilization_risk", "collateral_configuration_risk", "operational_restriction_risk"].map((factor) => ({
      factor, status: "evaluated", score: 20, explanation: "Synthetic public finding", rule_id: `v0.1:${factor}`,
    })) as Assessment["factors"],
  };
}
const outbound = vi.fn<typeof fetch>();
const errorLog = vi.fn();
function mockDatabase(body: unknown = assessment(), status = 200, type = "application/json") {
  outbound.mockResolvedValueOnce(new Response(JSON.stringify(body), { status, headers: { "Content-Type": type } }));
}
async function nonActionable(response: Response, httpStatus: number) {
  expect(response.status).toBe(httpStatus);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  const body = await response.json() as Record<string, unknown>;
  expect(body.score).toBeNull();
  expect(body.confidence).toBeNull();
  expect(body.risk_level).toBe("unknown");
  expect(body.factors).toEqual([]);
  const text = JSON.stringify(body);
  expect(text).not.toContain(env.SUPABASE_READER_JWT);
  expect(text).not.toContain(env.SUPABASE_PUBLISHABLE_KEY);
  expect(text).not.toContain("PRIVATE_DATABASE_DETAIL");
  return body;
}

beforeEach(() => {
  errorLog.mockReset();
  vi.stubGlobal("console", { ...console, error: errorLog });
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  outbound.mockReset();
  // No real network is reachable from tests, including unexpected fallback attempts.
  outbound.mockRejectedValue(new Error("Unexpected outbound request"));
  vi.stubGlobal("fetch", outbound);
});
afterEach(() => {
  for (const args of errorLog.mock.calls) {
    expect(args).toHaveLength(1);
    expect(args[0]).toMatch(/^risk_api_stage_failure=(CONFIGURATION|SUPABASE_FETCH_TIMEOUT|SUPABASE_FETCH_ERROR|SUPABASE_HTTP_STATUS|RESPONSE_READ|RESPONSE_VALIDATION)$/);
  }
  for (const [url, options] of outbound.mock.calls) {
    expect(url).toBe(env.SUPABASE_URL + DATABASE_PATH);
    expect(options?.method).toBe("POST");
    expect(options?.body).toBe("{}");
    expect(options?.redirect).toBe("manual");
    const headers = new Headers(options?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${env.SUPABASE_READER_JWT}`);
    expect(headers.get("apikey")).toBe(env.SUPABASE_PUBLISHABLE_KEY);
  }
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("precomputed-only risk endpoint", () => {
  it("accepts the actual outbound options in native Workers Request construction", async () => {
    // A plain fetch stub skips workerd's RequestInit validation. Keep the native
    // constructor here so an unsupported redirect mode cannot silently pass.
    outbound.mockImplementationOnce(async (url, options) => {
      const outgoing = new Request(url, options);
      expect(outgoing.redirect).toBe("manual");
      expect(outgoing.signal.aborted).toBe(false);
      expect(await outgoing.text()).toBe("{}");
      return Response.json(assessment());
    });
    const response = await worker.fetch(request(), env);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(assessment());
    expect(outbound).toHaveBeenCalledTimes(1);
    expect(errorLog).not.toHaveBeenCalled();
  });

  it.each([
    new TypeError("PRIVATE_DATABASE_DETAIL"),
    { name: "TypeError", message: env.SUPABASE_READER_JWT },
    { name: "AbortError", message: env.SUPABASE_URL },
    { name: env.SUPABASE_READER_JWT },
    "PRIVATE_DATABASE_DETAIL",
    null,
    { get name(): string { throw new Error("PRIVATE_DATABASE_DETAIL"); } },
  ])("logs only the fixed fetch failure stage for exception case %#", async (error) => {
    outbound.mockRejectedValueOnce(error);
    const body = await nonActionable(await worker.fetch(request(), env), 503);
    expect(body.reason).toBe("ASSESSMENT_UNAVAILABLE");
    expect(outbound.mock.calls[0][1]?.signal?.aborted).toBe(false);
    expect(errorLog.mock.calls).toEqual([["risk_api_stage_failure=SUPABASE_FETCH_ERROR"]]);
    expect(outbound).toHaveBeenCalledTimes(1);
  });
  it("does not classify an AbortError as timeout unless our controller aborted", async () => {
    outbound.mockRejectedValueOnce(new DOMException("PRIVATE_DATABASE_DETAIL", "AbortError"));
    await nonActionable(await worker.fetch(request(), env), 503);
    expect(outbound.mock.calls[0][1]?.signal?.aborted).toBe(false);
    expect(errorLog.mock.calls).toEqual([
      ["risk_api_stage_failure=SUPABASE_FETCH_ERROR"],
    ]);
    expect(outbound).toHaveBeenCalledTimes(1);
  });
  it.each(["CONFIGURATION", "SUPABASE_FETCH_ERROR", "SUPABASE_HTTP_STATUS", "RESPONSE_READ", "RESPONSE_VALIDATION"])(
    "logs only the fixed %s stage and preserves the error response", async (stage) => {
      let testEnv = env;
      if (stage === "CONFIGURATION") testEnv = { ...env, SUPABASE_READER_JWT: "PRIVATE_DATABASE_DETAIL" };
      if (stage === "SUPABASE_FETCH_ERROR") outbound.mockRejectedValueOnce(new Error(`${env.SUPABASE_URL} ${env.SUPABASE_READER_JWT}`));
      if (stage === "SUPABASE_HTTP_STATUS") mockDatabase({ message: "PRIVATE_DATABASE_DETAIL" }, 503);
      if (stage === "RESPONSE_READ") outbound.mockResolvedValueOnce(new Response("PRIVATE_DATABASE_DETAIL", { headers: { "Content-Type": "application/json" } }));
      if (stage === "RESPONSE_VALIDATION") mockDatabase({ ...assessment(), confidence: "PRIVATE_DATABASE_DETAIL" });
      const body = await nonActionable(await worker.fetch(request(), testEnv), 503);
      expect(body).toEqual({ status: "UNKNOWN", freshness_status: "unknown", reason: "ASSESSMENT_UNAVAILABLE",
        score: null, risk_level: "unknown", confidence: null, factors: [] });
      expect(errorLog.mock.calls).toEqual([[`risk_api_stage_failure=${stage}`]]);
    },
  );
  it("does not log successful reads or valid unavailable results", async () => {
    mockDatabase();
    expect((await worker.fetch(request(), env)).status).toBe(200);
    mockDatabase({ ...assessment(), status: "UNKNOWN", reason: "ASSESSMENT_UNKNOWN",
      score: null, risk_level: "unknown", confidence: null, factors: [] });
    await nonActionable(await worker.fetch(request(), env), 503);
    expect(errorLog).not.toHaveBeenCalled();
  });
  it("logs content-type rejection at the HTTP response gate", async () => {
    mockDatabase("PRIVATE_DATABASE_DETAIL", 200, "text/plain");
    await nonActionable(await worker.fetch(request(), env), 503);
    expect(errorLog.mock.calls).toEqual([["risk_api_stage_failure=SUPABASE_HTTP_STATUS"]]);
  });
  it("ages unscoreable data without making it actionable", async () => {
    mockDatabase({ ...assessment(), status: "UNKNOWN", reason: "ASSESSMENT_UNKNOWN",
      score: null, risk_level: "unknown", confidence: null, factors: [],
      fresh_until: "2026-09-16T11:59:10Z", expires_at: "2026-09-16T12:00:00Z" });
    const body = await nonActionable(await worker.fetch(request(), env), 503);
    expect(body.status).toBe("UNKNOWN");
    expect(body.freshness_status).toBe("stale");
  });
  it("handles the exact empty database projection", async () => {
    mockDatabase({ ...assessment(), assessment_id: null, block_number: null, calculated_at: null,
      fresh_until: null, expires_at: null, unknown_after: null, status: "UNKNOWN", freshness_status: "unknown",
      reason: "NO_ASSESSMENT", score: null, risk_level: "unknown", confidence: null, factors: [] });
    expect((await nonActionable(await worker.fetch(request(), env), 503)).reason).toBe("NO_ASSESSMENT");
  });
  it("preserves stored zero score and confidence without recalculating from factors", async () => {
    const data = { ...assessment(), score: 0, confidence: 0 };
    mockDatabase(data);
    expect(await (await worker.fetch(request(), env)).json()).toEqual(data);
  });
  it("rejects extra keys inside factor findings", async () => {
    const data = assessment();
    Object.assign(data.factors[0], { raw_evidence: "PRIVATE_DATABASE_DETAIL" });
    mockDatabase(data);
    await nonActionable(await worker.fetch(request(), env), 503);
  });
  it("returns the exact stored public projection through the fixed RPC", async () => {
    mockDatabase();
    const response = await worker.fetch(request(), env);
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toEqual(assessment());
    expect(outbound).toHaveBeenCalledTimes(1);
  });
  it("accepts the checksummed native USDC address", async () => {
    mockDatabase();
    expect((await worker.fetch(request({ ...input, asset: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913" }), env)).status).toBe(200);
  });
  it("preserves stored degraded status and confidence as of assessment time", async () => {
    const data = { ...assessment(), status: "DEGRADED", freshness_status: "degraded" };
    mockDatabase(data);
    expect(await (await worker.fetch(request(), env)).json()).toEqual(data);
  });
  it("keeps large block numbers as exact strings", async () => {
    const data = { ...assessment(), block_number: ((1n << 256n) - 1n).toString() };
    mockDatabase(data);
    expect(await (await worker.fetch(request(), env)).json()).toEqual(data);
  });
  it.each([
    {}, null, [], { ...input, chain: "ethereum" }, { ...input, protocol: "aave-v2" },
    { ...input, asset: "USDC" }, { ...input, asset: "0x" + "1".repeat(40) },
    { ...input, amount: 100 }, { ...input, methodology: "v0.1" }, { ...input, url: "https://rpc.example.test" },
  ])("rejects invalid/unsupported input without any database call: %j", async (body) => {
    await nonActionable(await worker.fetch(request(body), env), 422);
    expect(outbound).not.toHaveBeenCalled();
  });
  it("rejects query parameters", async () => {
    await nonActionable(await worker.fetch(request(input, "/v1/risk-check?refresh=true"), env), 422);
    expect(outbound).not.toHaveBeenCalled();
  });
  it.each(["/health", "/v1/supported", "/v1/providers/base/health", "/", "/v1/risk-check/"])("does not expose %s", async (path) => {
    await nonActionable(await worker.fetch(request(input, path), env), 404);
    expect(outbound).not.toHaveBeenCalled();
  });
  it.each(["GET", "PUT", "OPTIONS"])("rejects method %s", async (method) => {
    const response = await worker.fetch(request(input, "/v1/risk-check", method), env);
    expect(response.headers.get("Allow")).toBe("POST");
    await nonActionable(response, 405);
    expect(outbound).not.toHaveBeenCalled();
  });
  it("requires JSON content type", async () => {
    await nonActionable(await worker.fetch(new Request("https://api.example.test/v1/risk-check", {
      method: "POST", body: JSON.stringify(input),
    }), env), 415);
    expect(outbound).not.toHaveBeenCalled();
  });
  it.each(["{broken", "x".repeat(4097)])("rejects malformed or oversized bodies", async (body) => {
    await nonActionable(await worker.fetch(new Request("https://api.example.test/v1/risk-check", {
      method: "POST", headers: { "Content-Type": "application/json" }, body,
    }), env), 422);
    expect(outbound).not.toHaveBeenCalled();
  });
  it.each(["NO_ASSESSMENT", "INCOMPLETE_PUBLICATION", "INVALID_PUBLICATION", "UNSUPPORTED_SUBJECT", "ASSESSMENT_UNKNOWN"])("preserves non-actionable database reason %s", async (reason) => {
    mockDatabase({ ...assessment(), status: "UNKNOWN", freshness_status: "unknown", reason,
      score: null, risk_level: "unknown", confidence: null, factors: [] });
    const body = await nonActionable(await worker.fetch(request(), env), 503);
    expect(body.reason).toBe(reason);
  });
  it("masks a result that expired while being fetched", async () => {
    mockDatabase({ ...assessment(), fresh_until: "2026-09-16T11:59:30Z", expires_at: "2026-09-16T12:00:00Z" });
    const body = await nonActionable(await worker.fetch(request(), env), 503);
    expect(body.status).toBe("STALE");
    expect(body.expires_at).toBe("2026-09-16T12:00:00Z");
  });
  it("preserves microsecond precision immediately before expiry", async () => {
    mockDatabase({ ...assessment(), fresh_until: "2026-09-16T11:59:30Z", expires_at: "2026-09-16T12:00:00.000001Z" });
    const response = await worker.fetch(request(), env);
    expect(response.status).toBe(200);
    expect((await response.json() as Assessment).status).toBe("DEGRADED");
  });
  it("downgrades fresh delivery at the exact fresh_until boundary", async () => {
    mockDatabase({ ...assessment(), fresh_until: "2026-09-16T12:00:00.000000Z" });
    const response = await worker.fetch(request(), env);
    expect(response.status).toBe(200);
    expect((await response.json() as Assessment).status).toBe("DEGRADED");
  });
  it("returns UNKNOWN at unknown_after", async () => {
    mockDatabase({ ...assessment(), fresh_until: "2026-09-16T11:59:10Z", expires_at: "2026-09-16T11:59:20Z", unknown_after: "2026-09-16T12:00:00Z" });
    expect((await nonActionable(await worker.fetch(request(), env), 503)).status).toBe("UNKNOWN");
  });
  it("never upgrades an already stale database result", async () => {
    mockDatabase({ ...assessment(), status: "STALE", freshness_status: "stale", reason: "EXPIRED_ASSESSMENT",
      score: null, risk_level: "unknown", confidence: null, factors: [] });
    expect((await nonActionable(await worker.fetch(request(), env), 503)).status).toBe("STALE");
  });
  it.each([
    { score: 101 }, { score: "20" }, { confidence: 1.1 }, { asset: "0x" + "1".repeat(40) },
    { methodology_version: "v0.2" }, { calculated_at: "2099-01-01T00:00:00Z" },
    { expires_at: null }, { fresh_until: "2026-09-16T13:00:00Z" }, { block_number: "01" },
    { factors: [] }, { raw_evidence: "PRIVATE_DATABASE_DETAIL" }, { status: "UNKNOWN" },
  ])("fails closed on inconsistent database projection: %j", async (change) => {
    mockDatabase({ ...assessment(), ...change });
    expect((await nonActionable(await worker.fetch(request(), env), 503)).reason).toBe("ASSESSMENT_UNAVAILABLE");
  });
  it("rejects oversized or extra factor findings", async () => {
    const data = assessment();
    data.factors[0].explanation = "x".repeat(2001);
    mockDatabase(data);
    await nonActionable(await worker.fetch(request(), env), 503);
  });
  it.each([401, 403, 429, 500, 503, 302])("sanitizes database HTTP %s without retry/fallback", async (status) => {
    mockDatabase({ message: "PRIVATE_DATABASE_DETAIL", secret: env.SUPABASE_READER_JWT }, status);
    await nonActionable(await worker.fetch(request(), env), 503);
    expect(outbound).toHaveBeenCalledTimes(1);
  });
  it.each([301, 302, 303, 307, 308])("rejects HTTP %s without following its Location", async (status) => {
    outbound.mockImplementationOnce(async (url, options) => {
      const outgoing = new Request(url, options);
      expect(outgoing.redirect).toBe("manual");
      return new Response(null, { status, headers: {
        Location: "https://redirect.example.test/must-not-receive-credentials",
        "Content-Type": "application/json",
      } });
    });
    const body = await nonActionable(await worker.fetch(request(), env), 503);
    expect(body.reason).toBe("ASSESSMENT_UNAVAILABLE");
    expect(outbound).toHaveBeenCalledTimes(1);
    expect(errorLog.mock.calls).toEqual([["risk_api_stage_failure=SUPABASE_HTTP_STATUS"]]);
  });
  it("sanitizes transport failures", async () => {
    outbound.mockRejectedValueOnce(new Error(`PRIVATE_DATABASE_DETAIL ${env.SUPABASE_READER_JWT}`));
    await nonActionable(await worker.fetch(request(), env), 503);
    expect(outbound).toHaveBeenCalledTimes(1);
  });
  it.each(["{broken", "x".repeat(65537)])("rejects malformed or oversized database response", async (text) => {
    outbound.mockResolvedValueOnce(new Response(text, { headers: { "Content-Type": "application/json" } }));
    await nonActionable(await worker.fetch(request(), env), 503);
  });
  it("does not cache a previous successful assessment on database failure", async () => {
    mockDatabase();
    expect((await worker.fetch(request(), env)).status).toBe(200);
    await nonActionable(await worker.fetch(request(), env), 503);
    expect(outbound).toHaveBeenCalledTimes(2);
  });
  it.each([
    { SUPABASE_READER_JWT: token("service_role") }, { SUPABASE_READER_JWT: token("anon") },
    { SUPABASE_READER_JWT: token("risk_api_reader", 1) }, { SUPABASE_READER_JWT: "" },
    { SUPABASE_PUBLISHABLE_KEY: "sb_secret_PRIVATE_DATABASE_DETAIL" },
    { SUPABASE_URL: "https://rpc.example.test" }, { SUPABASE_URL: env.SUPABASE_URL + "/rest/v1/assessments" },
  ])("rejects unsafe configuration without egress: %j", async (change) => {
    await nonActionable(await worker.fetch(request(), { ...env, ...change }), 503);
    expect(outbound).not.toHaveBeenCalled();
  });
  it("aborts a timed-out database read without retries", async () => {
    vi.useFakeTimers();
    outbound.mockImplementationOnce((_url, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new Error("PRIVATE_DATABASE_DETAIL")));
    }));
    const response = worker.fetch(request(), env);
    await vi.advanceTimersByTimeAsync(4999);
    expect(errorLog).not.toHaveBeenCalled();
    expect(outbound.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await nonActionable(await response, 503);
    expect(outbound.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(errorLog.mock.calls).toEqual([
      ["risk_api_stage_failure=SUPABASE_FETCH_TIMEOUT"],
    ]);
    expect(vi.getTimerCount()).toBe(0);
    expect(outbound).toHaveBeenCalledTimes(1);
  });
});
