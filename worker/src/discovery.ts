import { declareDiscoveryExtension } from "@x402/extensions/bazaar";
import { USDC } from "./contracts";

const factorNames = [
  "oracle_risk",
  "liquidity_utilization_risk",
  "collateral_configuration_risk",
  "operational_restriction_risk",
] as const;

const outputExample = {
  assessment_id: "00000000-0000-4000-8000-000000000001",
  chain: "base",
  protocol: "aave-v3",
  asset: USDC,
  methodology_version: "v0.1",
  score: 50,
  risk_level: "moderate",
  confidence: 0.95,
  status: "OK",
  freshness_status: "fresh",
  reason: null,
  block_number: "35000000",
  calculated_at: "2026-01-01T00:00:00.000Z",
  fresh_until: "2026-01-01T00:05:00.000Z",
  expires_at: "2026-01-01T00:10:00.000Z",
  unknown_after: null,
  factors: [
    {
      factor: "oracle_risk",
      status: "evaluated",
      score: 50,
      explanation: "Example oracle-risk factor result.",
      rule_id: "example-oracle-rule",
    },
    {
      factor: "liquidity_utilization_risk",
      status: "evaluated",
      score: 50,
      explanation: "Example liquidity and utilization factor result.",
      rule_id: "example-liquidity-rule",
    },
    {
      factor: "collateral_configuration_risk",
      status: "evaluated",
      score: 50,
      explanation: "Example collateral-configuration factor result.",
      rule_id: "example-collateral-rule",
    },
    {
      factor: "operational_restriction_risk",
      status: "evaluated",
      score: 50,
      explanation: "Example operational-restriction factor result.",
      rule_id: "example-operational-rule",
    },
  ],
};

export function riskDiscoveryExtension(): Record<string, unknown> {
  return declareDiscoveryExtension({
    input: {
      chain: "base",
      protocol: "aave-v3",
      asset: USDC,
    },
    inputSchema: {
      type: "object",
      properties: {
        chain: {
          type: "string",
          const: "base",
          description:
            "Blockchain network. Risk API v0.1 currently supports Base only.",
        },
        protocol: {
          type: "string",
          const: "aave-v3",
          description:
            "Protocol. Risk API v0.1 currently supports Aave V3 only.",
        },
        asset: {
          type: "string",
          const: USDC,
          description: "Native USDC contract address on Base.",
        },
      },
      required: ["chain", "protocol", "asset"],
      additionalProperties: false,
    },
    bodyType: "json",
    output: {
      example: outputExample,
      schema: {
        type: "object",
        properties: {
          assessment_id: {
            type: "string",
            format: "uuid",
          },
          chain: {
            type: "string",
            const: "base",
          },
          protocol: {
            type: "string",
            const: "aave-v3",
          },
          asset: {
            type: "string",
            const: USDC,
          },
          methodology_version: {
            type: "string",
            const: "v0.1",
          },
          score: {
            type: "number",
            minimum: 0,
            maximum: 100,
          },
          risk_level: {
            type: "string",
            enum: ["low", "moderate", "high", "critical"],
          },
          confidence: {
            type: ["number", "null"],
            minimum: 0,
            maximum: 1,
          },
          status: {
            type: "string",
            enum: ["OK", "DEGRADED"],
          },
          freshness_status: {
            type: "string",
            enum: ["fresh", "degraded"],
          },
          reason: {
            type: "null",
          },
          block_number: {
            type: "string",
            pattern: "^(0|[1-9][0-9]{0,77})$",
          },
          calculated_at: {
            type: "string",
            format: "date-time",
          },
          fresh_until: {
            type: "string",
            format: "date-time",
          },
          expires_at: {
            type: "string",
            format: "date-time",
          },
          unknown_after: {
            type: ["string", "null"],
            format: "date-time",
          },
          factors: {
            type: "array",
            minItems: 4,
            maxItems: 4,
            items: {
              type: "object",
              properties: {
                factor: {
                  type: "string",
                  enum: [...factorNames],
                },
                status: {
                  type: "string",
                  const: "evaluated",
                },
                score: {
                  type: "number",
                  minimum: 0,
                  maximum: 100,
                },
                explanation: {
                  type: "string",
                },
                rule_id: {
                  type: "string",
                },
              },
              required: [
                "factor",
                "status",
                "score",
                "explanation",
                "rule_id",
              ],
              additionalProperties: false,
            },
          },
        },
        required: [
          "assessment_id",
          "chain",
          "protocol",
          "asset",
          "methodology_version",
          "score",
          "risk_level",
          "confidence",
          "status",
          "freshness_status",
          "reason",
          "block_number",
          "calculated_at",
          "fresh_until",
          "expires_at",
          "unknown_after",
          "factors",
        ],
        additionalProperties: false,
      },
    },
  });
}