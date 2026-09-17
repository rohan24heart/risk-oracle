import { z } from "zod";

export const USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913";
export const riskRequest = z.strictObject({
  chain: z.literal("base"),
  protocol: z.literal("aave-v3"),
  asset: z.string().regex(/^0x[0-9a-fA-F]{40}$/).transform((v) => v.toLowerCase()).pipe(z.literal(USDC)),
});

const timestamp = z.iso.datetime({ offset: true }).refine(
  (s) => /(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(s)
    && !/\.\d{7}/.test(s),
);
const factor = z.strictObject({
  factor: z.enum(["oracle_risk", "liquidity_utilization_risk", "collateral_configuration_risk", "operational_restriction_risk"]),
  status: z.literal("evaluated"),
  score: z.number().min(0).max(100),
  explanation: z.string().min(1).max(2000),
  rule_id: z.string().min(1).max(128),
});
const assessmentSchema = z.strictObject({
  assessment_id: z.uuid().nullable(),
  chain: z.literal("base"),
  protocol: z.literal("aave-v3"),
  asset: z.literal(USDC),
  methodology_version: z.literal("v0.1"),
  score: z.number().min(0).max(100).nullable(),
  risk_level: z.enum(["low", "moderate", "high", "critical", "unknown"]),
  confidence: z.number().min(0).max(1).nullable(),
  status: z.enum(["OK", "DEGRADED", "STALE", "UNKNOWN"]),
  freshness_status: z.enum(["fresh", "degraded", "stale", "unknown"]),
  reason: z.enum(["NO_ASSESSMENT", "UNSUPPORTED_SUBJECT", "INCOMPLETE_PUBLICATION", "INVALID_PUBLICATION", "EXPIRED_ASSESSMENT", "ASSESSMENT_UNKNOWN"]).nullable(),
  block_number: z.string().regex(/^(0|[1-9][0-9]{0,77})$/)
    .refine((v) => BigInt(v) <= (1n << 256n) - 1n).nullable(),
  calculated_at: timestamp.nullable(),
  fresh_until: timestamp.nullable(),
  expires_at: timestamp.nullable(),
  unknown_after: timestamp.nullable(),
  factors: z.array(factor).max(4),
});
export type Assessment = z.infer<typeof assessmentSchema>;

// Preserve PostgreSQL microseconds; Date.parse alone would truncate a .000001 deadline.
export function microseconds(value: string): bigint {
  const fraction = /\.(\d+)(?=Z|[+-]\d{2}:\d{2}$)/.exec(value)?.[1] ?? "";
  const whole = value.replace(/\.\d+(?=Z|[+-]\d{2}:\d{2}$)/, "");
  return BigInt(Date.parse(whole)) * 1000n + BigInt(fraction.padEnd(6, "0"));
}

export function validateAssessment(input: unknown, now = Date.now()): Assessment {
  const a = assessmentSchema.parse(input);
  const time = BigInt(now) * 1000n;
  const calculated = a.calculated_at === null ? null : microseconds(a.calculated_at);
  const fresh = a.fresh_until === null ? null : microseconds(a.fresh_until);
  const expires = a.expires_at === null ? null : microseconds(a.expires_at);
  const unknown = a.unknown_after === null ? null : microseconds(a.unknown_after);
  if ((calculated !== null && calculated > time)
    || ((fresh === null) !== (expires === null))
    || (fresh !== null && expires !== null && fresh > expires)
    || (unknown !== null && (expires === null || unknown < expires))) {
    throw new Error("Invalid assessment");
  }
  const actionable = a.status === "OK" || a.status === "DEGRADED";
  if (actionable) {
    if (a.assessment_id === null || a.block_number === null || calculated === null
      || fresh === null || expires === null || expires <= calculated
      || a.score === null || a.risk_level === "unknown" || a.reason !== null
      || a.factors.length !== 4 || new Set(a.factors.map((f) => f.factor)).size !== 4
      || a.freshness_status !== (a.status === "OK" ? "fresh" : "degraded")) {
      throw new Error("Invalid assessment");
    }
  } else if (a.score !== null || a.confidence !== null || a.risk_level !== "unknown"
    || a.factors.length !== 0 || a.reason === null
    || (a.status === "STALE" && a.freshness_status !== "stale")) {
    throw new Error("Invalid assessment");
  }
  // Delivery-time checks only: never change stored scores/confidence or extend deadlines.
  // An unavailable database result can never be upgraded by the edge clock.
  if (unknown !== null && time >= unknown) {
    return mask(a, "UNKNOWN", "unknown");
  }
  if (expires !== null && time >= expires) {
    return mask(a, a.status === "UNKNOWN" ? "UNKNOWN" : "STALE", "stale");
  }
  if (a.freshness_status === "fresh" && fresh !== null && time >= fresh) {
    return { ...a, status: a.status === "OK" ? "DEGRADED" : a.status, freshness_status: "degraded" };
  }
  return a;
}

function mask(a: Assessment, status: "STALE" | "UNKNOWN", freshness: "stale" | "unknown"): Assessment {
  return { ...a, status, freshness_status: freshness, reason: "EXPIRED_ASSESSMENT",
    score: null, risk_level: "unknown", confidence: null, factors: [] };
}
