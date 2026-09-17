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
  async fetch(request: Request, env: Env): Promise<Response> {
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
      try {
        const assessment = await latestAssessment(env);
        return reply(assessment, assessment.status === "OK" || assessment.status === "DEGRADED" ? 200 : 503);
      } catch {
        // Never return/log exception messages, URLs, credentials, database errors or raw bodies.
        return unavailable("ASSESSMENT_UNAVAILABLE", 503);
      }
    } catch {
      return unavailable("INTERNAL_ERROR", 500);
    }
  },
} satisfies ExportedHandler<Env>;
