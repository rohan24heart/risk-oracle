import { createAuthHeader } from "@coinbase/x402";
import type { FacilitatorClient } from "@x402/core/server";
import { z } from "zod";
import { readJson } from "./supabase";

export interface PaymentEnv {
  X402_PAY_TO: string;
  X402_FACILITATOR_URL: string;
  CDP_API_KEY_ID: string;
  CDP_API_KEY_SECRET: string;
  QUERY_PAYER_HMAC_KEY: string;
}
export const NETWORK = "eip155:8453";
export const SETTLEMENT_TIMEOUT_MS = 15_000;
export const address = z.string().regex(/^0x[0-9a-fA-F]{40}$/);
const verifySchema = z.object({ isValid: z.boolean(), payer: address.optional() });
const settleSchema = z.object({
  success: z.boolean(), transaction: z.string(), network: z.literal(NETWORK),
  payer: address.optional(), amount: z.string().optional(),
});
const supportedSchema = z.object({
  kinds: z.array(z.object({ x402Version: z.number(), scheme: z.string(), network: z.string() })),
});

export function paymentConfiguration(env: PaymentEnv): URL {
  address.parse(env.X402_PAY_TO);
  if (/^0x0{40}$/i.test(env.X402_PAY_TO) || !/^[0-9a-fA-F]{64}$/.test(env.QUERY_PAYER_HMAC_KEY)
    || !env.CDP_API_KEY_ID || !env.CDP_API_KEY_SECRET) throw new Error("Payment configuration unavailable");
  const url = new URL(env.X402_FACILITATOR_URL);
  // CDP credentials are only ever used with CDP's production x402 endpoint.
  if (url.href.replace(/\/$/, "") !== "https://api.cdp.coinbase.com/platform/v2/x402") {
    throw new Error("Payment configuration unavailable");
  }
  return url;
}

// HTTP transport only. The external facilitator performs all cryptographic
// verification and on-chain settlement. No RPC client or wallet lives here.
// The SDK's default transport follows redirects and logs extension responses;
// this adapter instead bounds reads and returns only allowlisted fields.
export function facilitator(env: PaymentEnv, url: URL): FacilitatorClient {
  async function call(path: "supported" | "verify" | "settle", body?: unknown): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), path === "settle" ? SETTLEMENT_TIMEOUT_MS : 5_000);
    try {
      const endpoint = new URL(`${url.href.replace(/\/$/, "")}/${path}`);
      const method = path === "supported" ? "GET" : "POST";
      const authorization = await createAuthHeader(env.CDP_API_KEY_ID, env.CDP_API_KEY_SECRET,
        method, endpoint.host, endpoint.pathname);
      const response = await fetch(endpoint.href, {
        method, redirect: "manual", signal: controller.signal,
        headers: { Authorization: authorization, "Content-Type": "application/json", Accept: "application/json" },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (response.status !== 200 || response.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
        void response.body?.cancel().catch(() => undefined);
        throw new Error("Facilitator unavailable");
      }
      const data = await readJson(response, 65_536);
      if (controller.signal.aborted) throw new Error("Facilitator unavailable");
      return data;
    } catch {
      // Never allow SDK diagnostics to interpolate transport/provider error data.
      throw new Error("Facilitator unavailable");
    } finally { clearTimeout(timer); }
  }
  return {
    async getSupported() {
      const result = supportedSchema.parse(await call("supported"));
      return { kinds: result.kinds.filter(k => k.x402Version === 2 && k.scheme === "exact" && k.network === NETWORK)
        .map(() => ({ x402Version: 2, scheme: "exact", network: NETWORK })), extensions: [], signers: {} };
    },
    async verify(paymentPayload, paymentRequirements) {
      return verifySchema.parse(await call("verify", { x402Version: 2, paymentPayload, paymentRequirements }));
    },
    async settle(paymentPayload, paymentRequirements) {
      return settleSchema.parse(await call("settle", { x402Version: 2, paymentPayload, paymentRequirements }));
    },
  };
}
