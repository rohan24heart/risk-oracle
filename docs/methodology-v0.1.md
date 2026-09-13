# Methodology v0.1: native USDC on Aave V3 Base

This is an experimental reserve-condition index, not a loss probability, investment
safety rating, or autonomous allocation signal. Higher values mean more observed
stress/restriction. Rules and weights are public policy choices, not Aave or
Chainlink endorsements or calibrated economic probabilities.

`risk_oracle.scoring_v01.assess_snapshot(snapshot, calculated_at=...)` accepts an
existing shared ReserveSnapshot and returns PrecomputedAssessment with
methodology_version `v0.1`. It does not acquire data, persist results, or call the
legacy mock scorer. The existing risk_engine.calculate_risk / MockProvider flow
is legacy local/demo infrastructure; it is not the v0.1 live assessment path.

## Inputs and failure semantics

All contract observations and their RPC evidence must share the snapshot's full
safe/finalized BlockRef. The scorer revalidates the snapshot, verifies required
observation states, datatypes, units and evidence outcomes, and rejects future
acquisition/canonicality timestamps. Unsupported source types, invalid input units,
missing evidence/data, and undefined ratios produce an UNKNOWN factor. Valid zero
values remain zero. An entirely absent factor has empty observation/evidence
references, never invented references. Known factors retain references to every
consumed observation and transitive evidence, plus a versioned rule and explanation.
The assessment retains all snapshot evidence IDs and hashes the full snapshot.

Any UNKNOWN factor means null overall score/confidence and risk_level unknown.
Weights are never redistributed. Observation UNKNOWN freshness emitted by the
collector's unconfigured per-observation freshness policy is not itself missing
data: the scorer separately checks state, evidence and the explicit time rules.

All arithmetic remains exact rational/integer until output. Factor scores are
serialized as floats, but aggregation uses unrounded rational factor values. Final
score is rounded half-up to two decimals before classification. Policy and snapshot
hashes plus explicit calculation time identify a deterministic assessment UUID.

## Four factors

### Oracle risk (0-100)

**Measures feed freshness/integrity and verified adapter behavior, NOT independent
economic accuracy of USDC's market price.** No independent price comparison is
available. A depeg is not automatically an oracle failure.

Required: Aave oracle/source addresses, verified stable-cap adapter identity,
upstream/proxy/aggregator addresses, verified Chainlink proxy type and registry
identity, feed/source decimals and answers, adapter cap, feed updatedAt, officially
documented heartbeat, and pinned block timestamp. The upstream and proxy must
match. Unknown identity/unsupported semantics or unavailable required inputs mean
UNKNOWN. Nonpositive heartbeat, negative cap, zero/future update timestamp or
invalid scale mean UNKNOWN.

For complete inputs, a nonpositive feed/source answer or contradiction of the
verified adapter output yields 100. The documented output for positive feed values
is min(feed,cap); for nonpositive feed values it is zero. Exact units are compared.
A correct cap application is not an anomaly. Fallback behavior remains UNKNOWN
regardless of primary anomalies.

Otherwise, a = block timestamp - updatedAt, H = heartbeat:

    O = 100 * clip((a-H)/H), clip(x) = min(1,max(0,x))

Age <= H gives 0; 1.5H gives 50; >=2H gives 100. Heartbeat is an update trigger,
not a delivery SLA; the ramp is a v0.1 policy choice. Deviation threshold is
retained as context, not a peg-error tolerance or scoring input.

Sources:
- https://docs.chain.link/data-feeds#monitoring-data-feeds
- https://github.com/aave-dao/aave-price-feeds/blob/00d0f14b0734dc6faf41960bb9023c0b742a944b/src/contracts/PriceCapAdapterStable.sol

### Liquidity/utilization risk (0-100)

Required: total_supplied S, variable_debt D, actual available_liquidity C,
virtual_available_liquidity V (all atomic USDC, six decimals), and
optimal_utilization K (ray, converted to a fraction). Require S>0, D+V>0, 0<K<1;
otherwise UNKNOWN. All required missing values mean UNKNOWN.

    U = D/(D+V)
    Ucash = clip(1-C/S)
    Ustar = max(U,Ucash)
    L = 50*Ustar/K                         if Ustar <= K
        50+50*(Ustar-K)/(1-K)              otherwise

Zero debt is valid. Zero actual cash with supplied claims yields maximum cash
pressure. Cash is never inferred as supply minus debt. The configured kink is a
protocol-specific reference; assigning it score 50 is a policy choice. Taking max
avoids summing related pressures. Cash coverage does not guarantee user withdrawal.

Sources:
- https://aave.com/docs/aave-v3/smart-contracts/interest-rate-strategy
- https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/src/contracts/misc/DefaultReserveInterestRateStrategyV2.sol

### Collateral/configuration risk (0-100)

Required: ltv l, liquidation_threshold t, liquidation_bonus multiplier b, divided
by 10,000 (10,500 -> 1.05). Missing input means UNKNOWN.

- l=t=b=0: score 0, explicitly default collateral disabled.
- Otherwise invalid 0<=l<=t<=1 or b<=1, or t*b>=1: score 100.
- Otherwise l=t>0: score 50.
- Otherwise score 0.

Explain default buffer t-l and simplified headroom 1-t*b without extra scoring.
These are a default configuration-consistency screen, NOT borrower exposure.
USDC collateral settings do not describe other collateral backing USDC loans.
Default disabled collateral receives no implied credit for borrower solvency.

Sources:
- https://aave.com/docs/aave-v3/smart-contracts/pool-configurator
- https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/src/contracts/protocol/libraries/logic/GenericLogic.sol

### Operational/restriction risk (0-100)

Required: reserve_active, reserve_paused, reserve_frozen, borrowing_enabled,
supply_cap and borrow_cap; numerator required for each nonzero cap. Supply
numerator is supply_cap_total_supplied (including accrued treasury); borrow
numerator is variable_debt. Compare atomic amounts against whole-token cap*10^6.
Use raw exact values, not a rounded cap-utilization ratio.

Maximum applicable severity:
- paused or inactive: 100
- frozen: 50
- ordinary borrowing disabled or either nonzero cap reached/exceeded: 25
- otherwise: 0

Zero cap is uncapped, excluded without safety credit. Missing required inputs mean
UNKNOWN even when another known restriction is observed. Caps are not loan-loss
estimates; freezes may be protective. Ordinary permissions do not establish all
eMode permissions. Deprecated stable borrowing/isolation fields are not scored.

Sources:
- https://aave.com/docs/aave-v3/smart-contracts/pool-configurator
- https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/docs/3.6/Aave-v3.6-features.md

## Aggregation and confidence

    W = (O+L+Q+R)/4
    score = max(W,75) if any factor equals 100, otherwise W

Equal weights and the critical floor are explicit uncalibrated policy choices.
Rounded score bands: [0,25) low; [25,50) moderate; [50,75) high; [75,100] critical.
UNKNOWN is null, never zero.

Confidence is separate from score: four covered domains out of seven fixed domains
(the four factors, sequencer/recovery, fallback behavior, borrower exposure).

    coverage = 4/7 when all factors complete
    confidence = coverage * freshness_multiplier

Age uses the earlier of acquisition time and block time, preventing old chain state
from becoming current through a recent RPC read. <=25m fresh (multiplier 1);
>25m to 45m degraded (3/4); >45m to 90m stale (0); >90m unknown (0).
For stale/unknown snapshots no current score is issued. The shared canonical
UNKNOWN assessment contract requires confidence=null as well, even though the
standalone coverage calculation yields zero. Factors remain explicitly tied to
historical snapshot observations. Prior historical assessments are not rewritten.

The three unobserved domains lower coverage without adding risk points. Recovery
is separate from feed publication and no effective fallback is assumed. This
coverage scale and freshness penalty are conventions, not statistical confidence.
Adding coverage requires an explicit methodology revision, not automatic inflation.

Source: https://docs.chain.link/data-feeds/l2-sequencer-feeds

## Limits and example

No independent executable price, borrower health/concentration, liquidation depth,
reserve deficit, sequencer state, or fallback evaluation. eMode coverage is partial.
Metadata can age, implementations can upgrade, and periodic samples can miss
incidents. Historical/incident validation is required before stronger safety claims.

Example: S=100M, D=85M, C=V=15M, K=90%, valid oracle within heartbeat, LTV=75%,
LT=80%, bonus=105%, active/unpaused/unfrozen/borrowing enabled, caps not reached:
O=0, L=47.222..., Q=0, R=0; score=11.81 (low); fresh confidence=4/7.
"Low" means low measured stress in this limited scope, not safe investment.
