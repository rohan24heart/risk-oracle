import "dotenv/config";
import { privateKeyToAccount } from "viem/accounts";
import { x402Client } from "@x402/core/client";
import { wrapFetchWithPayment } from "@x402/fetch";
import { registerExactEvmScheme } from "@x402/evm/exact/client";

const API =
  "https://risk-oracle-api.rohan-rajnikanth.workers.dev/v1/risk-check";

const EXPECTED = {
  network: "eip155:8453",
  scheme: "exact",
  amount: "10000",
  asset: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
  payTo: "0x4453fdec07a4a4dafe3399d942c6827ec1d28cfd",
};

const body = {
  chain: "base",
  protocol: "aave-v3",
  asset: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
};

const privateKey = process.env.PAYER_PRIVATE_KEY;

if (!privateKey) {
  throw new Error("PAYER_PRIVATE_KEY is missing from .env");
}

const account = privateKeyToAccount(privateKey);

// 1. Ask the API what payment it requires, without signing anything.
const challenge = await fetch(API, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

if (challenge.status !== 402) {
  throw new Error(`Expected HTTP 402, got ${challenge.status}`);
}

const encoded = challenge.headers.get("payment-required");

if (!encoded) {
  throw new Error("402 response did not include PAYMENT-REQUIRED");
}

const paymentRequired = JSON.parse(
  Buffer.from(encoded, "base64").toString("utf8"),
);

const requirement = paymentRequired.accepts?.find(
  (r) =>
    r.network === EXPECTED.network &&
    r.scheme === EXPECTED.scheme &&
    r.amount === EXPECTED.amount &&
    r.asset?.toLowerCase() === EXPECTED.asset &&
    r.payTo?.toLowerCase() === EXPECTED.payTo,
);

if (!requirement) {
  throw new Error(
    "Payment policy rejected the API challenge. Nothing was signed.",
  );
}

console.log("Payment challenge approved by local policy.");
console.log("Payer:", account.address);
console.log("Amount: $0.01 USDC");
console.log("Network: Base");

// 2. Give the x402 client permission to sign the approved payment.
const client = new x402Client();

registerExactEvmScheme(client, {
  signer: account,
});

const paidFetch = wrapFetchWithPayment(fetch, client);

// 3. Make the same request. x402 handles 402 -> sign -> retry automatically.
const response = await paidFetch(API, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const text = await response.text();

console.log(`HTTP ${response.status}`);
console.log(text);

if (!response.ok) {
  process.exitCode = 1;
}
