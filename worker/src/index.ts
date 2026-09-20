import { encodePaymentRequiredHeader, encodePaymentResponseHeader } from "@x402/core/http";
import { purchase } from "./payment";
import { recordSettledQuery } from "./telemetry";
import type { PaymentEnv } from "./facilitator";
import { riskRequest } from "./contracts";
import { latestAssessment, readJson, type Env } from "./supabase";

function reply(body: unknown, status: number, extra: Record<string, string> = {}): Response {
  return Response.json(body, { status, headers: {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", ...extra,
  } });
}
function unavailable(reason: string, status: number, extra: Record<string, string> = {}): Response {
  return reply({ status: "UNKNOWN", freshness_status: "unknown", reason,
    score: null, risk_level: "unknown", confidence: null, factors: [] }, status, extra);
}

export default {
  async fetch(request: Request, env: Env & PaymentEnv, ctx?: ExecutionContext): Promise<Response> {
    const started = Date.now();
    try {
      const url = new URL(request.url);
      if (url.pathname !== "/v1/risk-check") return unavailable("NOT_FOUND", 404);
      if (request.method !== "POST") return unavailable("METHOD_NOT_ALLOWED", 405, { Allow: "POST" });
      if (url.search) return unavailable("INVALID_REQUEST", 422);
      if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(request.headers.get("content-type") ?? "")) {
        return unavailable("JSON_REQUIRED", 415);
      }
      let input: unknown;
      try { input = await readJson(request, 4096); }
      catch { return unavailable("INVALID_REQUEST", 422); }
      if (!riskRequest.safeParse(input).success) return unavailable("INVALID_OR_UNSUPPORTED_REQUEST", 422);
      let assessment;
      try { assessment = await latestAssessment(env); }
      catch { return unavailable("ASSESSMENT_UNAVAILABLE", 503); }
      if (assessment.status !== "OK" && assessment.status !== "DEGRADED") return reply(assessment, 503);
      try {
        const result = await purchase(request, env, assessment);
        if (result.kind === "unavailable") return reply(result.assessment, 503);
        if (result.kind === "required") return reply(result.challenge, 402, {
          "PAYMENT-REQUIRED": encodePaymentRequiredHeader(result.challenge),
        });
        const response = reply(result.assessment, 200, {
          "PAYMENT-RESPONSE": encodePaymentResponseHeader(result.settlement),
        });
        try {
          const logging = recordSettledQuery(env, result.assessment, result.settlement, Date.now() - started);
          if (ctx) ctx.waitUntil(logging);
          else await logging;
        } catch { /* Telemetry scheduling must also leave the paid response intact. */ }
        return response;
      } catch {
        // Neither provider errors nor payment signatures are returned or logged.
        return unavailable("PAYMENT_UNAVAILABLE", 503);
      }
    } catch {
      return unavailable("INTERNAL_ERROR", 500);
    }
  },
} satisfies ExportedHandler<Env & PaymentEnv>;
