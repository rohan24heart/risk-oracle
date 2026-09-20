import { x402ResourceServer } from "@x402/core/server";
import { decodePaymentSignatureHeader } from "@x402/core/http";
import { PaymentPayloadV2Schema } from "@x402/core/schemas";
import type { PaymentPayload, PaymentRequired, SettleResponse } from "@x402/core/types";
import { ExactEvmScheme } from "@x402/evm/exact/server";
import { USDC, validateAssessment, type Assessment } from "./contracts";
import { facilitator, paymentConfiguration, NETWORK, SETTLEMENT_TIMEOUT_MS, type PaymentEnv } from "./facilitator";

export type Settlement = SettleResponse & { payer: string };
type PurchaseResult = { kind: "required"; challenge: PaymentRequired }
  | { kind: "unavailable"; assessment: Assessment }
  | { kind: "paid"; assessment: Assessment; settlement: Settlement };
const usable = (a: Assessment) => a.status === "OK" || a.status === "DEGRADED";

export async function purchase(request: Request, env: PaymentEnv, assessment: Assessment): Promise<PurchaseResult> {
  const transport = facilitator(env, paymentConfiguration(env));
  // Fetch outside initialize: its built-in warning must never receive provider data.
  const supported = await transport.getSupported();
  const server = new x402ResourceServer({ ...transport, getSupported: async () => supported })
    .register(NETWORK, new ExactEvmScheme());
  await server.initialize();
  const requirements = await server.buildPaymentRequirements({
    scheme: "exact", network: NETWORK, price: "$0.01", payTo: env.X402_PAY_TO,
    maxTimeoutSeconds: 300,
  });
  const terms = requirements[0];
  if (requirements.length !== 1 || terms.amount !== "10000" || terms.asset.toLowerCase() !== USDC) {
    throw new Error("Payment terms unavailable");
  }
  assessment = validateAssessment(assessment);
  if (!usable(assessment)) return { kind: "unavailable", assessment };
  const challenge = await server.createPaymentRequiredResponse(requirements, {
    url: request.url, description: "Precomputed Aave V3 Base USDC risk assessment", mimeType: "application/json",
  });
  const required = (): PurchaseResult => ({ kind: "required", challenge });
  const header = request.headers.get("PAYMENT-SIGNATURE");
  if (!header || header.length > 16_384) return required();
  let payload: PaymentPayload;
  try {
    const parsed = PaymentPayloadV2Schema.parse(decodePaymentSignatureHeader(header));
    if (parsed.accepted.network !== NETWORK) return required();
    // No optional buyer metadata/extensions are needed for this exact-payment route.
    payload = { x402Version: 2, accepted: { ...parsed.accepted, network: NETWORK, extra: parsed.accepted.extra ?? {} }, payload: parsed.payload };
    if (!server.findMatchingRequirements(requirements, payload)) return required();
  } catch { return required(); }
  const verified = await server.verifyPayment(payload, terms);
  if (!verified.isValid || !verified.payer) return required();

  // Payment verification can take time. Recheck before charging, and require
  // enough validity for the bounded settlement call. Never extend a deadline.
  const admission = validateAssessment(assessment, Date.now() + SETTLEMENT_TIMEOUT_MS);
  if (!usable(admission)) throw new Error("Insufficient settlement validity");
  assessment = validateAssessment(assessment);
  const result = await server.settlePayment(payload, terms);
  if (!result.success) return required();
  if (result.network !== NETWORK || !/^0x[0-9a-fA-F]{64}$/.test(result.transaction)
    || (result.amount !== undefined && result.amount !== terms.amount)
    || (result.payer !== undefined && result.payer.toLowerCase() !== verified.payer.toLowerCase())) {
    throw new Error("Settlement unconfirmed");
  }
  // Preserve delivery-time downgrades if freshness changed during settlement.
  assessment = validateAssessment(assessment);
  if (!usable(assessment)) throw new Error("Assessment expired during settlement");
  // Only these receipt fields can reach the buyer or telemetry; never extensions/errors.
  return { kind: "paid", assessment, settlement: {
    success: true, network: NETWORK, transaction: result.transaction.toLowerCase(),
    payer: verified.payer.toLowerCase(), amount: terms.amount,
  } };
}
