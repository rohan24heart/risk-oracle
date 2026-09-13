# Risk Oracle primary-source knowledge base

This document separates payment/discovery infrastructure, legal asset claims, lending mechanics and oracle behavior before a risk methodology is selected. It records primary-source facts, their limits and candidate observations. It does not assign weights, select score thresholds, estimate loss probabilities or propose a final scoring formula.

The review cutoff is **11 September 2026**. A statute, draft standard, documentation, source code and deployed contract are different kinds of evidence. Published-code findings below apply to the identified revision; this research has not established which revision is deployed on Base.

## Review coverage and preservation

| Source family | Successfully reviewed | Preserved evidence and boundaries |
| --- | --- | --- |
| x402 | Original 6 May 2025 whitepaper; current v2 core; HTTP transport; EVM exact scheme; current Bazaar documentation. | Original PDF and complete source files in [payment sources](sources/payment/manifest.json). Current v2 supplies the technical reference; the whitepaper supplies historical intent. Not every network binding or extension was reviewed. [^1][^2][^3][^4][^5] |
| ERC-8004 | Full EIP: abstract, motivation, all three registries/interfaces, rationale, security and copyright. | Full [CC0 original](sources/payment/erc-8004.md). Official status: **Draft**, created 13 August 2025. Deployment security is a separate question. [^6] |
| GENIUS Act | Full enacted Public Law 119-27, sections 1-20, including amendments and timing. | Official 48-page PDF, GPO HTML and extracted text in [legal sources](sources/legal/). Selected current agency materials checked; comprehensive implementing-rule/issuer-approval review remains outstanding. [^7][^8][^9] |
| Aave V3 | Official collateral, borrowing, liquidation, configuration, interest, oracle and isolation documentation; relevant code and version-change documents. | [Detailed notes](aave-notes.md); 16 original files, licenses and pinned revisions in [Aave archive](sources/aave/manifest.json). No live Base state collected. |
| Chainlink | Price feeds, API, heartbeat/deviation, selecting feeds, developer responsibilities, sourcing, historical rounds and L2 uptime. | [Detailed notes](oracle-notes.md); official Markdown and MIT license in [Chainlink archive](sources/chainlink/manifest.json). Relevant sections reviewed; not an audit of every OCR implementation/feed. |

Archive manifests preserve exact URLs, retrieval timestamps and SHA-256 hashes. Aave revisions are immutable; moving-branch payment/Chainlink snapshots are identified by their retrieved content. Archiving a source does not imply all linked dependencies were reviewed. Source code is retained for research under upstream licenses, not installed as application dependencies.

## A. Payment/discovery architecture

### Payment negotiation and settlement

The original whitepaper proposes pay-per-use API/resource access through HTTP 402 and signed payments. Its speed, fees and finality examples are historical claims, not universal guarantees. Its early payload examples predate v2 and should not be treated as the current executable specification. [^1]

Current x402 v2 separates data **types**, scheme/network **logic**, and transport **representation**. Parties are the resource server, client and facilitator; the server can operate its own verification/settlement infrastructure. Budget management and sessions are outside the core scope. These describe service architecture rather than collateral safety. [^2] (scope; sections 1-3)

HTTP uses base64 JSON in `PAYMENT-REQUIRED` on the 402 response, `PAYMENT-SIGNATURE` on the retry and `PAYMENT-RESPONSE` for settlement. The business response body remains an application concern. **Inference:** future payment handling could wrap a risk service while preserving its business schema. No implementation choice is made here. [^3] (Payment Required Signaling, Header Summary, Response Body)

Payment requirements include `scheme`, CAIP-2 `network`, atomic-unit string `amount`, `asset`, `payTo`, `maxTimeoutSeconds` and optional `extra`; resource metadata is separate. The payer identifies the accepted requirements. Base Mainnet is `eip155:8453`, distinct from Sepolia `eip155:84532`. Token symbol alone is insufficient identity; amounts need token precision. A facilitator's actual schemes/networks/extensions are advertised through `/supported`. [^2] (sections 5, 7.3, 11)

| Core payment flow | Defined order | Boundary |
| --- | --- | --- |
| `authorization` | verify, resource execution, settle, respond | Read-only verification neither reserves funds nor proves settlement. |
| `upfront` | settle, resource execution, respond | Commitment precedes resource execution. |
| `escrow` | settle deposit, resource execution, settle final charge, respond | Deposit and final charge have different semantics. |

The selected scheme/asset-transfer method determines supported flows. Non-default flows must be advertised; at least verification or settlement precedes execution. A `settlement_pending` error is nonterminal: a transaction was broadcast without established confirmation. Its hash/network permit reconciliation before retrying. A timeout does not prove nonpayment. [^2] (sections 6.1, 7.1-7.2, 9)

EVM `exact` currently describes EIP-3009, Permit2 and ERC-7710 transfer methods. EIP-3009 binds payer, recipient, amount, validity window and nonce, with token/network/domain and execution checks. Permit2 adds allowance and proxy/witness dependencies; gas-free initial approval is conditional. ERC-7710 introduces delegation-manager/caveat semantics and documented simulation/execution races and gas risks. Their safeguards cannot be inferred merely from `exact`. [^4] (sections 1-3, Implementer Notes)

### Discovery, identity and evidence

Bazaar discovery describes resources, schemas and payment requirements. Listing or payment history does not validate a service's financial analysis. A current-source discrepancy remains: core section 7.2.1 describes facilitator `EXTENSION-RESPONSES` as a sidechannel not forwarded to buyers, while guide text discusses client-facing handling. Pin the SDK/release and resolve that behavior before integration. [^2][^5]

ERC-8004 says payments are orthogonal. Its identity, reputation and validation registries do not define financial risk scoring. Global agent identity includes registry namespace, chain, contract address and ERC-721 token ID. Ownership can transfer; metadata/endpoints can change. Optional domain verification establishes an endpoint-domain association, not competence. `x402Support`, `active` and `supportedTrust` are declarations. [^6] (Motivation; Identity Registry)

The reserved `agentWallet` requires EIP-712/1271 control proof when changed and clears on transfer. Feedback uses signed fixed-point `int128` values with 0-18 decimals and application-defined tags. The owner/operator cannot directly review itself, but Sybil manipulation remains explicitly possible. Revocation, reviewer identity, units, tags and emitted evidence must be retained. A payment can corroborate interaction without proving an unbiased review. [^6] (On-chain Metadata; Reputation Registry)

Validation commits to request/evidence hashes; the nominated validator returns a 0-100 value that can later change. Incentives/slashing belong to the selected validation protocol. A value of 100 is not a 100% safety probability. Registration cannot guarantee functional or non-malicious capabilities. [^6] (Validation Registry; Security Considerations)

**Boundary:** payment and agent-service reliability can be observed separately. They should not directly change an asset-risk score without independently justified, validated relationships.

## B. Regulatory/RWA implications

### Enacted law and implementation timing

The controlling original is **Public Law 119-27, 139 Stat. 419-466**, approved **18 July 2025**, not an earlier bill draft. General effectiveness is the earlier of 18 months after enactment (**18 January 2027**) or 120 days after primary federal payment-stablecoin regulators issue final implementing regulations. Section 3(b)(1) separately begins its specified provider offer/sale restriction after three years (**18 July 2028**). A rulemaking deadline is not proof that final rules exist. [^7] (sections 3(b)(1), 13, 20)

Treasury published a proposed issuance/offer/sale rule on 18 August 2026; the OCC on 19 August described its final rule as forthcoming by November. These establish ongoing implementation, not a complete proof that no qualifying final rule exists anywhere. This review has not established that section 20's earlier trigger occurred. A complete dated regulator/Federal Register check remains necessary before legal applicability conclusions. [^8][^9]

### Important statutory details

All provisions below remain subject to statutory timing and applicable implementing rules.

| Topic | Retained detail | Provision in the full law [^7] |
| --- | --- | --- |
| Scope | Payment/settlement use plus an issuer's fixed-monetary-value redemption obligation and stable-value representation. Exclusions include national currency, deposits including tokenized deposits, and securities, with qualifications. | Section 2(22) |
| Issuers | Domestic permitted pathways include approved insured-depository subsidiaries, federal qualified issuers and state qualified issuers. Eligibility concerns the actual legal entity. | Sections 2(11), 2(23), 3(a), 5 |
| Reserves | Identifiable reserves must cover outstanding issuance at least 1:1. Eligible assets include cash/Fed balances, specified deposits, short Treasuries, conditioned repo/reverse repo, qualifying government funds and approved similarly liquid federal assets. | Section 4(a)(1)(A) |
| Maturity/tokenization | Treasury eligibility includes remaining or original maturity of at most 93 days. Repo rules specify overnight maturity and collateral/counterparty conditions. Tokenized reserves are limited to enumerated classes and remain subject to law. | Section 4(a)(1)(A)(iii)-(viii) |
| Reuse | Pledging/rehypothecation/reuse is generally prohibited, with specified margin, custody and redemption-liquidity exceptions. | Section 4(a)(2) |
| Redemption | Timely-redemption procedures and fees must be disclosed; fee changes need at least seven days' notice. Specified regulators control discretionary limitations. No universal instant onchain redemption SLA is supplied. | Section 4(a)(1)(B) |
| Disclosure/examination | Monthly reserve composition includes issuance, amount, average tenor and custody geography; accounting-firm examination and CEO/CFO certification are specified. The conditional annual financial-statement/audit regime above $50 billion is distinct. | Sections 4(a)(1)(C), 4(a)(3), 4(a)(10) |
| Prudential rules | Capital, concentration/diversification, liquidity, interest-rate and operational controls are tailored through regulation; the Act does not supply every numerical limit. | Section 4(a)(4) |
| AML/orders | BSA/sanctions duties and technological capability to comply with lawful orders matter. Orders may require freezing, seizure, burning or transfer prevention. | Sections 2(16), 4(a)(5)-(6), 8-9 |
| Yield | The prohibition concerns permitted/foreign issuers paying interest or yield solely for holding, using or retaining their stablecoin. It is not textually a blanket ban on every separate lending arrangement. | Section 4(a)(11) |
| State/federal | The state option has a $10 billion issuance threshold and substantial-similarity requirements; transition timing and waivers qualify it. Exceeding the threshold alone does not prove illegality. | Sections 4(c)-(d), 6-7 |
| Custody | Customer-property protection, segregation and accounting have specified omnibus/deposit exceptions. Customer-priority qualifications and the self-custody software/hardware exclusion matter. | Section 10 |
| Insolvency | Holders have ratable priority in required reserves. Reserves are excluded from the estate but remain subject to the stay. Deficient-reserve claims have special estate priority only to the extent compliance required additional reserves. The best-efforts 14-day distribution-order provision is not guaranteed full repayment. | Section 11(a), (c)-(e); 11 USC 362, 507, 541 amendments |
| Insurance | Stablecoins are not US-guaranteed, FDIC deposit insured or NCUA share insured. Reserve deposits at an insured bank are a separate matter. | Sections 4(a)(9), 4(e) |
| Foreign issuers | Comparability, OCC registration, US liquidity reserves unless permitted otherwise, and jurisdiction conditions qualify the foreign exception. Determinations/registrations can be rescinded. | Sections 3(b)(2), 18 |
| Classification | Securities/commodity exclusions concern stablecoins issued by permitted issuers; they do not exempt every RWA. Bank tokenized-deposit authority is preserved separately. | Sections 2(22), 16-17 |

**Original drafting issue:** section 4(a)(3)(A) refers to a report under (1)(D), while the published reserve disclosure provision appears in (1)(C). Preserve the text without silently correcting it; collect an authoritative correction/interpretation. [^7]

Specific protocol, self-custody and transaction exclusions in sections 2(7), 3(h) and 10(e) are not blanket exemptions from all financial laws for an API, facilitator or interface. Sections 12-15 concern interoperability/rulemaking/studies; section 19 changes government financial disclosure. None supplies a DeFi score. [^7]

**RWA inference:** a fund share, Treasury token, stablecoin, tokenized deposit and aToken may represent different legal claims. Reserve coverage does not establish a wrapper holder's direct issuer-redemption right or a liquidatable par price. Legal entity, beneficial ownership, custodians, transfer restrictions and exit route require instrument-specific primary agreements and evidence. [^7] (sections 2, 4, 10-11, 17)

## C. Protocol risk mechanics

### Collateral, debt and valuation

Supplying mints aTokens; collateral eligibility additionally depends on user flags and effective reserve/mode settings. Credit delegation can separate the transaction sender from the debt-bearing account. New borrowing depends on capacity, liquidity, permissions and caps. Withdrawability is not established by aToken balance alone. LTV limits borrowing; liquidation threshold determines liquidation eligibility. [^10][^11]

Aave health factor conceptually divides liquidation-threshold-weighted collateral value by debt value. This is an existing protocol calculation, not the future project score. The pinned `GenericLogic` uses actual oracle prices, token precision, enabled collateral and effective eMode, with integer rounding. Its no-debt result is `uint256.max`, a sentinel rather than an enormous ordinary observation. A rounded displayed average threshold may not reproduce exact arithmetic. [^14]

The project's existing risk request identifies chain, protocol and asset but no borrower account. **Inference:** position-specific health factor cannot be inferred from those fields alone. Reserve, asset, market and borrower-position analysis are different units; no unit or API change is selected here.

### Liquidation and liquidity

The pinned validation requires HF strictly below one, plus reserve/user/debt/grace-period conditions. A breach does not guarantee successful execution or a willing liquidator. In the pinned liquidation logic, the 50% restriction applies when selected collateral and debt each meet 2,000e8 base-value thresholds and HF exceeds 0.95e18; it is measured against aggregate debt. Otherwise full selected debt can be repaid subject to collateral and other constraints. Where selected debt and collateral both remain, 1,000e8 dust limits apply. These constants and currency assumptions are revision-specific, not portable rules for all deployments. [^15]

V3.3 introduced position-wide close-factor treatment and reserve-deficit accounting for newly collateral-exhausted bad debt. Burning debt tokens recognizes a deficit; it does not recover assets. Gas, bonus, protocol fees, executable market depth, transaction inclusion and rounding affect liquidation economics. The generic Pool page's 50% description cannot replace later implementation rules. [^10][^16]

Interest behavior depends on the deployed strategy. Official V3 documentation describes slopes around optimal usage; variable rates and liquidity rates are not fixed returns. Actual versus virtual reserve accounting matters. Zero supply/borrow caps mean uncapped in documented configuration, not zero activity. Active, frozen and paused states have different effects; a freeze constrains new supply/borrowing rather than itself proving insolvency. [^11][^12]

### Isolation, eMode and version differences

Legacy isolation permits one isolated collateral asset, restricted borrow assets and a debt ceiling. V3.6 decouples non-default eMode permissions from ordinary reserve settings. V3.7 source materials remove legacy isolation/siloed borrowing and introduce a distinct isolated-eMode mechanism. Deprecated views may return compatibility zeros. One isolation boolean or default LTV cannot describe all versions. [^17][^18]

In V3.7 isolated eMode, some already-enabled out-of-category collateral can retain liquidation-threshold contribution while having zero borrowing LTV. V3.7 also removes prior sequencer-sentinel gating, while per-reserve liquidation grace checks remain. Neither a source branch nor legacy docs establish actual Base protection. Pin implementation, source revision and executed governance state at a fixed block. [^18][^19]

Detailed arithmetic, CAPO parameters, close-factor boundaries, source conflicts and additional references are retained in [Aave notes](aave-notes.md).

## D. Oracle risk mechanics

### Dependencies and fallback

The path can be Aave reserve -> AaveOracle -> adapter -> multiple price/ratio providers. Pinned AaveOracle accepts positive `latestAnswer()` values. Missing/nonpositive sources invoke fallback, but positive stale values do not, and an upstream revert is not caught. The base-currency branch returns the configured base unit. Neither a fallback address nor a positive price proves freshness or independent failover. [^20]

Aave's stable adapter takes the lower of a positive upstream answer and its upper cap; it does not floor a depeg at one dollar. Ratio-based adapters bound growth using snapshots and elapsed time. Their maximum snapshot age at parameter update is not automatic expiry during price reads. Inspect each adapter and all upstream timestamp policies. [^21]

### Price-feed observations and integrity

Use the actual proxy, asset/quote semantics and decimals. `latestRoundData()` returns signed answer and round timestamps; `latestAnswer()` omits freshness context. Current API documentation marks `answeredInRound` deprecated, so it should not become a universal standalone modern freshness test. Aggregator type and implementation remain relevant. [^23]

Heartbeat and deviation trigger updates; they do not guarantee continuous streaming or exact-time onchain delivery. Record answer age, configured thresholds, congestion and application tolerance. Thresholds differ by network/pair/wrapped asset. Unchanged price, delayed update and stale-positive answer are distinct states. [^22][^24]

Chainlink distinguishes market-integrity risk from application-code risk. Multiple nodes do not remove correlated source failures or make thin markets manipulation-proof. Official sourcing differs between multi-vendor market prices, authoritative reserve/NAV publishers and onchain exchange rates. A fresh redemption ratio can differ from executable secondary-market value. [^25][^26]

The selecting-feeds guide describes independent references, bounds, monitoring, circuit breakers and fallback/pause policies; these are not universal automatic failover guarantees. General `minAnswer`/`maxAnswer` fields are unused on most feeds. Separately, a bounded market-price feed can suppress an above-cap write and keep the previous answer. This differs from Aave's adapter returning a clamped price. Market hours and deprecation also affect availability. [^22][^27]

An underlying-asset feed may stay accurate when a wrapped/bridged asset loses convertibility. Reserve or NAV publication does not alone establish complete liabilities, ownership rights or accessible redemption. Those require legal and economic evidence beyond oracle transmission. [^26][^27]

### L2 and historical context

Chainlink lists Base sequencer uptime support. The answer is 0 for up and 1 for down; `startedAt` identifies the status transition. Recovery grace belongs to the consumer; the sample's one-hour constant is not a Base Aave parameter. Feed availability does not prove the protocol consults it, particularly given Aave version changes. [^19][^28]

Proxy historical round IDs encode aggregator phases. Preserve changes instead of treating a proxy's entire history as one configuration. **Research recommendation:** retain chain, block number/hash/time, source/adapter/proxy/aggregator, raw data, precision, revision and retrieval status. [^29]

The current local health endpoint calls `eth_blockNumber`. **Inference from implementation:** it checks whether its configured endpoint returns a parsable block number. It does not independently verify chain identity, finality, oracle freshness, protocol solvency or provider agreement. Official Base documentation identifies `eth_chainId` for network identity, with Mainnet 8453. No extra live request was made for this research. [^30]

Full oracle details and boundaries are in [oracle notes](oracle-notes.md).

## E. Facts that could become measurable risk signals

These are candidate observations, not selected features or a score. Record missingness, provenance and uncertainty separately from value. Every observation needs its entity, chain, deployment version, time and units. Proposed uses below are analytical inferences from the cited mechanisms.

| Candidate observation | Primary evidence required | Interpretation limits |
| --- | --- | --- |
| Answer age and update delays | Raw rounds, timestamps, heartbeat/deviation history. [^22][^23][^29] | Separate congestion, bounded feeds, market hours and outages; one global cutoff is unsupported. |
| Price divergence | Actual protocol output and independent contemporaneous executable references. [^20][^27] | Match asset representation, quote units, trade size and time; divergence alone does not identify fault. |
| Oracle dependency/concentration | Reserve/adapter graph, publishers, source/owner changes. [^20][^21][^26] | Multiple operators may share one underlying source. |
| Cap/fallback engagement | Verified code, configured bounds and transition history. [^20][^21] | Engagement is a transformation, not proof of attack; a configured fallback may not work. |
| Liquidation/exit liquidity | Primary DEX pool, trade/orderbook, slippage and gas data. [^16][^27] | TVL or volume alone is not executable depth. |
| Utilization/withdrawal headroom | Reserve balances, debt, indexes, interest strategy/accounting. [^11][^12] | Utilization does not directly establish insolvency probability. |
| Position liquidation distance | Account, enabled collateral, debt, prices, effective mode and exact HF. [^14][^15] | Requires a position; no-debt sentinel is categorical. |
| Liquidatable size/delay | Gates, close factor, dust, bonus, fee, transaction outcomes and proceeds. [^15][^16] | Eligibility differs from profitable, successfully included execution. |
| Deficits and recoveries | Deficit records, coverage and repayment movements. [^16] | Reduced debt-token supply may mean write-off rather than recovery. |
| Controls and changes | Caps, pause/freeze, modes, roles, executed governance and block. [^11][^18] | Zero/false may mean deprecated, uncapped or unavailable. |
| L2 availability/recovery | Uptime, finality, actual consumer gating/grace policies. [^19][^28][^30] | Distinguish RPC, sequencer, unsafe head and stale price. |
| Reserve backing/composition | Original issuer/accountant reports, issuance and custody evidence. [^7] section 4 | Required compliance is not observed compliance; use matching dates/valuations. |
| Redemption access/latency | Binding terms, eligibility, fees, notices and observed redemptions. [^7] section 4(a)(1)(B) | Wrappers/secondary holders may lack direct issuer access. |
| Legal/operational restrictions | Actual issuer approvals, orders, custody agreements and token controls. [^7] sections 4, 10-11, 18 | Primarily legal context/eligibility until a risk relationship is justified. |
| Payment/service reliability | Settlement outcomes, independent uptime and validator evidence. [^2][^6] | Keep separate from collateral/protocol safety. |

Before validating predictive relationships, collect original outcomes including failures, configuration changes and survivorship gaps. Preserve what was knowable at each historical time. Do not count one price/liquidity event in multiple dimensions without examining overlap. These are research-quality requirements; no coefficients, score cutoffs or preferred feature set are chosen.

## F. Things that should NOT be used as scoring signals

| Unsupported shortcut | Reason |
| --- | --- |
| x402 adoption, API price, payment volume or Bazaar rank as financial safety | Measures access/demand/settlement, not correctness or asset quality. [^2][^5] |
| ERC-8004 registration, trust flags, unfiltered reviews or validator 100 as guaranteed trust | Transferable identity, declarative metadata, Sybil risks and external validator semantics. [^6] |
| GENIUS label, US location or Treasury marketing as zero risk | Scope, eligibility, facts and timing require proof; the token is not government insured. [^7] |
| Treating all RWA tokens, stablecoins and tokenized deposits alike | Legal rights, transfer restrictions and redemption mechanisms differ. [^7] sections 2, 16-17 |
| Aave/Chainlink/Alchemy brand or audit count as safety | Names and counts do not establish implementation-matched coverage or sound inputs. [^20][^25] |
| A successful RPC/health request or larger block number as protocol safety | Does not prove identity, finality, oracle freshness or solvency. [^30]; local provider implementation |
| HF above one, high APY/TVL or eMode membership as unconditional safety | Conditional valuation, variable rates and liquidation economics remain. [^12][^14][^16][^18] |
| Upper caps or stable prices as downside insurance | Caps differ; redemption and wrapped-asset value can deteriorate independently. [^21][^27] |
| Fallback address, interface or min/max fields as working protection | Actual behavior can accept stale prices, suppress writes or propagate reverts. [^20][^22][^27] |
| Missing data converted to zero risk; no-debt sentinel as ordinary HF | Missing, inapplicable, stale, failed and zero are different states. [^14][^23] |
| Current branch/default documentation as historical deployed state | Contracts/parameters change; official pages contain version conflicts. [^15][^18][^19] |
| Social sentiment, promotion or unsupported model confidence as measured risk | No reviewed primary evidence validates them as predictors. |

These exclude unsupported shortcuts; they do not claim contextual information can never matter in a well-defined, independently validated model.

## Important missing material and next primary sources

1. **Base deployment and asset universe.** Collect an immutable [Aave Base address-book revision](https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Base.sol), verified proxy/implementation code and ABIs, reserve parameters, ACL/stewards, and executed upgrade/governance records. Resolve V3.3/V3.6/V3.7 differences before interpreting protections. No live deployment snapshot has been collected.
2. **Actual oracle graph and history.** Collect each deployed feed/adapter's verified code, [official Chainlink entries](https://data.chain.link/feeds), heartbeat/deviation changes, source methodology, ratio providers, fallback settings and notices. Full aggregator/OCR audits and feed-specific assumptions remain missing.
3. **Economic outcomes and security evidence.** Collect original logs for positions/liquidations/deficits/pauses, primary pool/orderbook/trade data and original incident reports. Obtain full implementation-matched audits and remediation records through [Aave security](https://aave.com/security) and the [price-feed security directory](https://github.com/aave-dao/aave-price-feeds/tree/00d0f14b0734dc6faf41960bb9023c0b742a944b/security). Documentation does not calibrate loss probabilities.
4. **Legal implementation register.** Collect all relevant final rules/effective dates, interpretations, issuer approvals, state substantial-similarity determinations, foreign comparability/registration lists and orders from Treasury/OCC/FDIC/Federal Reserve/NCUA/FinCEN as applicable. Resolve the section 4 cross-reference and section 20 trigger authoritatively. Selected proposed-rule evidence here is not a complete register. [^7][^8][^9]
5. **Instrument-specific originals.** For each proposed stablecoin/RWA, obtain binding redemption terms, latest accountant/reserve reports, legal entity and custody/trust agreements, prospectus/offering document, financial statements, token administration/blacklist/upgrade code and bridge/staking redemption terms. [Circle transparency](https://www.circle.com/transparency) is a starting index for USDC reports, not an instrument-level conclusion. For tokenized securities, collect applicable original SEC filings and distinguish staff statements from binding rules.
6. **Base/OP Stack security assumptions.** Collect finality, sequencer, forced-inclusion, bridge/withdrawal, upgrade-key and outage documents plus the selected RPC provider's data behavior. Start with [Base RPC documentation](https://docs.base.org/base-chain/api-reference/rpc-overview); reachability does not establish these assumptions.
7. **Payment/agent implementation evidence.** Pin the intended x402 SDK/release, selected schemes/extensions, supporting EIPs, deployment contracts, audits and facilitator policies. Resolve guide/core differences. For ERC-8004 collect the intended deployed registry source/audit and reviewer/validator trust models. The draft standard does not establish production security.

The reviewed originals provide a foundation for data collection. Deployment state, instrument-level rights, historical outcomes and implementing legal material remain the principal gaps before a defensible methodology can be designed.

## Sources

Undated web documentation was retrieved on 11 September 2026. Section names in the body locate the relevant provision. Detailed Aave/oracle notes retain additional references. Archived originals retain their own licensing terms.

[^1]: Coinbase Developer Platform / x402; Erik Reppel, Ronnie Caspers, Kevin Leffew, Danny Organ, Dan Kim and Nemil Dalal. [x402: An open standard for internet-native payments](https://www.x402.org/x402-whitepaper.pdf), 6 May 2025; complete 15-page PDF, particularly sections 3, 8-10 and historical claims in sections 2, 6-7.
[^2]: x402 Foundation. [X402 Protocol Specification v2](https://github.com/x402-foundation/x402/blob/main/specs/x402-specification-v2.md), complete core specification; version-history v2 entry 9 December 2025; moving source snapshot retrieved 11 September 2026.
[^3]: x402 Foundation. [Transport: HTTP](https://github.com/x402-foundation/x402/blob/main/specs/transports-v2/http.md), complete current v2 transport.
[^4]: x402 Foundation. [Scheme: exact on EVM](https://github.com/x402-foundation/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md), complete current scheme including EIP-3009, Permit2, ERC-7710 and annex.
[^5]: x402 documentation. [Bazaar extension](https://docs.x402.org/extensions/bazaar), discovery, schemas and extension-response sections; original Markdown response archived.
[^6]: Marco De Rossi, Davide Crapis, Jordan Ellis and Erik Reppel. [ERC-8004: Trustless Agents](https://eips.ethereum.org/EIPS/eip-8004), Draft, created 13 August 2025; full text including Security Considerations; CC0.
[^7]: United States Congress / Government Publishing Office. [Public Law 119-27, GENIUS Act, 139 Stat. 419](https://www.govinfo.gov/content/pkg/PLAW-119publ27/pdf/PLAW-119publ27.pdf), 18 July 2025. [Complete official HTML](https://www.govinfo.gov/content/pkg/PLAW-119publ27/html/PLAW-119publ27.htm). Full sections 1-20, including amended statutory text.
[^8]: US Department of the Treasury. [Federal Register document 2026-16796](https://www.govinfo.gov/content/pkg/FR-2026-08-18/pdf/2026-16796.pdf), 18 August 2026, proposed issuance/offer/sale implementation; used for proposal status. The full 24-page PDF is archived; exhaustive implementing-rule analysis remains outstanding.
[^9]: Office of the Comptroller of the Currency. [Comptroller Gould Discusses Digital Asset Innovation, GENIUS Next Steps](https://www.occ.gov/news-issuances/news-releases/2026/nr-occ-2026-69.html), news release 2026-69, 19 August 2026; paragraph on forthcoming final rule.
[^10]: Aave. [Pool](https://aave.com/docs/aave-v3/smart-contracts/pool), supply, withdraw, borrow, collateral, liquidation and account views; mixed-version documentation.
[^11]: Aave. [Pool Configurator](https://aave.com/docs/aave-v3/smart-contracts/pool-configurator), collateral, caps, flags, modes and roles.
[^12]: Aave. [Interest Rate Strategy](https://aave.com/docs/aave-v3/smart-contracts/interest-rate-strategy), rate calculation and reserve inputs.
[^13]: Aave. [Oracles](https://aave.com/docs/aave-v3/smart-contracts/oracles) and [Health Factor & Liquidations](https://aave.com/help/borrowing/liquidations), read with implementation/version qualifications.
[^14]: Aave DAO. [GenericLogic.sol](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/src/contracts/protocol/libraries/logic/GenericLogic.sol), account valuation and borrowing capacity; immutable Origin revision from 9 September 2026.
[^15]: Aave DAO. [ValidationLogic.sol](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/src/contracts/protocol/libraries/logic/ValidationLogic.sol) and [LiquidationLogic.sol](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/src/contracts/protocol/libraries/logic/LiquidationLogic.sol); pinned validation gates, constants, sizes, dust and fees.
[^16]: Aave DAO. [V3.3 features](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/docs/3.3/Aave-v3.3-features.md), Bad Debt Management and Liquidation Logic Changes.
[^17]: Aave. [Isolation Mode](https://aave.com/help/supplying/isolation-mode), legacy mechanism explanation.
[^18]: Aave DAO. [V3.6 features](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/docs/3.6/Aave-v3.6-features.md), [V3.7 mode removal](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/docs/3.7/mode-removal.md), [V3.7 isolated eMode](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/docs/3.7/isolated-emode.md). Published version behavior is not deployment proof.
[^19]: Aave DAO. [V3.7 PriceOracleSentinel removal](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/docs/3.7/sentinel-removal.md), checked against pinned ValidationLogic.
[^20]: Aave DAO. [AaveOracle.sol](https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/src/contracts/misc/AaveOracle.sol), full source, especially getAssetPrice, source/fallback getters and setters.
[^21]: Aave DAO. [Price-feed repository](https://github.com/aave-dao/aave-price-feeds/blob/00d0f14b0734dc6faf41960bb9023c0b742a944b/README.md), [PriceCapAdapterStable.sol](https://github.com/aave-dao/aave-price-feeds/blob/00d0f14b0734dc6faf41960bb9023c0b742a944b/src/contracts/PriceCapAdapterStable.sol), [PriceCapAdapterBase.sol](https://github.com/aave-dao/aave-price-feeds/blob/00d0f14b0734dc6faf41960bb9023c0b742a944b/src/contracts/PriceCapAdapterBase.sol); immutable revision from 4 September 2026.
[^22]: Chainlink. [Data Feeds](https://docs.chain.link/data-feeds), components, upgrades, monitoring, bounds and timestamp checks.
[^23]: Chainlink. [Data Feeds API Reference](https://docs.chain.link/data-feeds/api-reference), AggregatorV3Interface and aggregator fields.
[^24]: Chainlink. [Decentralized Data Model](https://docs.chain.link/architecture-overview/architecture-decentralized-model), aggregation and update triggers.
[^25]: Chainlink. [Developer Responsibilities](https://docs.chain.link/data-feeds/developer-responsibilities), market integrity and application-code risks.
[^26]: Chainlink. [Data Sources](https://docs.chain.link/data-feeds/data-sources), sourcing models by product type.
[^27]: Chainlink. [Selecting Quality Data Feeds](https://docs.chain.link/data-feeds/selecting-data-feeds), categories, market hours, single-source risk, bounded prices, exchange rates, wrapped assets, shutdown and mitigations; full MIT-licensed original preserved.
[^28]: Chainlink. [L2 Sequencer Uptime Feeds](https://docs.chain.link/data-feeds/l2-sequencer-feeds), Base address and consumer semantics; [official example](https://github.com/smartcontractkit/documentation/blob/main/public/samples/DataFeeds/DataConsumerWithSequencerCheck.sol).
[^29]: Chainlink. [Getting Historical Data](https://docs.chain.link/data-feeds/historical-data), proxy phases and historical rounds.
[^30]: Base. [eth_chainId](https://docs.base.org/base-chain/api-reference/ethereum-json-rpc-api/eth_chainId), [RPC Overview](https://docs.base.org/base-chain/api-reference/rpc-overview); deeper finality/security documentation remains to be collected.
