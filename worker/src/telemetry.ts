import type { Assessment } from "./contracts";
import type { PaymentEnv } from "./facilitator";
import type { Settlement } from "./payment";
import { configuration, type Env } from "./supabase";

export const QUERY_EVENT_PATH = "/rest/v1/rpc/record_query_event";
const encoder = new TextEncoder();
const hex = (bytes: ArrayBuffer) => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");

export async function recordSettledQuery(env: Env & PaymentEnv, assessment: Assessment,
  settlement: Settlement, latencyMs: number): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const config = configuration(env);
    const keyBytes = Uint8Array.from(env.QUERY_PAYER_HMAC_KEY.match(/.{2}/g)!, v => parseInt(v, 16));
    const key = await crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const payer = hex(await crypto.subtle.sign("HMAC", key, encoder.encode(settlement.payer.toLowerCase())));
    // UUIDv8 from a domain-separated settlement identity; retries collide at the
    // deployed primary key even across isolates. It is independent of the HMAC key.
    const idBytes = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(
      `risk-oracle/query-event/v1/${settlement.network}/${settlement.transaction.toLowerCase()}`)));
    idBytes[6] = (idBytes[6] & 0x0f) | 0x80;
    idBytes[8] = (idBytes[8] & 0x3f) | 0x80;
    const id = hex(idBytes.slice(0, 16).buffer);
    const abort = new AbortController();
    timer = setTimeout(() => abort.abort(), 3_000);
    const response = await fetch(new URL(QUERY_EVENT_PATH, config.url).href, {
      method: "POST", redirect: "manual", signal: abort.signal,
      headers: { "Content-Type": "application/json", apikey: config.key,
        Authorization: `Bearer ${config.jwt}`, "Cache-Control": "no-store" },
      body: JSON.stringify({
        p_event_id: `${id.slice(0,8)}-${id.slice(8,12)}-${id.slice(12,16)}-${id.slice(16,20)}-${id.slice(20)}`,
        p_chain: assessment.chain, p_protocol: assessment.protocol, p_asset: assessment.asset,
        p_assessment_id: assessment.assessment_id, p_methodology_version: assessment.methodology_version,
        p_score: assessment.score, p_risk_level: assessment.risk_level, p_freshness_status: assessment.freshness_status,
        p_payer_identifier: payer, p_payment_reference: settlement.transaction,
        p_payment_amount_atomic: settlement.amount, p_payment_network: settlement.network,
        p_latency_ms: Math.min(2_147_483_647, Math.max(0, Math.round(latencyMs))),
      }),
    });
    // No response data is needed, including duplicate-key or other error bodies.
    void response.body?.cancel().catch(() => undefined);
  } catch {
    // Best effort only: never expose/log the exception or fail the paid response.
  } finally { if (timer !== undefined) clearTimeout(timer); }
}
