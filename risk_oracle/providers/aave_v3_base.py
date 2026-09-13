"""Native USDC reserve configuration, state and oracle inputs only; nothing runs at import time.

Addresses: masterdoc.pdf (MASTER_RISK_ORACLE_EVIDENCE_PACK), sections
7.1/7.2, pages 14/17. The pack retrieved the address book on 2026-09-11:
https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Base.sol
That citation is a moving branch, not a verified runtime implementation hash.

ABI return order checked against the official source at:
https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/src/contracts/helpers/AaveProtocolDataProvider.sol

All four integers are basis points (denominator 10,000). Liquidation bonus
includes the 10,000 principal component; it is never subtracted or converted
into a floating-point percentage here. Snapshot freshness uses the separate acquisition-age policy. Observation/feed
validity remains separate and does not imply a score.
"""
from collections.abc import Callable
from datetime import datetime, timezone
from importlib.resources import files
from decimal import Decimal
from hashlib import sha256
import json
import re

from pydantic import ValidationError
from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_hash.auto import keccak

from risk_oracle.models import (
    BlockRef, EvidenceRecord, Freshness, Observation, ReserveSnapshot, ReserveSubject,
)
from risk_oracle.providers.alchemy import AlchemyProvider
from risk_oracle.snapshot_builder import build_reserve_snapshot
from risk_oracle.freshness_policy import apply_snapshot_freshness

# Only addresses explicitly recorded in the master deployment section.
POOL = "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"
DATA_PROVIDER = "0x0f43731eb8d45a581f4a36dd74f5f358bc90c73a"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
ADDRESSES_PROVIDER = "0xe20fcbdbffc4dd138ce8b2e6fbb6cb49777ad64d"
UI_DATA_PROVIDER = "0x0c6bc4a12039788be08f87e87cff87fedbd1d386"
ZERO_ADDRESS = "0x" + "00" * 20
SOURCE_ROOT = "https://github.com/aave-dao/aave-v3-origin/blob/8305565ae342f1773c42cd2e4593f175fe5968a0/"
EMODES_TYPE = "(uint8,(uint16,uint16,uint16,uint128,bool,string,uint128,uint128))[]"
RESERVE_TYPE = "((uint256),uint128,uint128,uint128,uint128,uint128,uint40,uint16,address,address,address,address,uint128,uint128,uint128)"
SIGNATURE = "getReserveConfigurationData(address)"
CALL_DATA = "0x3e150141" + USDC[2:].rjust(64, "0")
FIELDS = ("ltv", "liquidation_threshold", "liquidation_bonus", "reserve_factor")
ABI = [{"type": "function", "name": "getReserveConfigurationData", "stateMutability": "view",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [{"name": name, "type": "uint256" if index < 5 else "bool"}
                    for index, name in enumerate(("decimals", "ltv", "liquidationThreshold",
                        "liquidationBonus", "reserveFactor", "usageAsCollateralEnabled",
                        "borrowingEnabled", "stableBorrowRateEnabled", "isActive", "isFrozen"))]}]


def _digest(value: object) -> str:
    return "0x" + sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


MANIFEST = dict(chain_id=8453, pool=POOL, data_provider=DATA_PROVIDER, asset=USDC,
                addresses_provider=ADDRESSES_PROVIDER, ui_data_provider=UI_DATA_PROVIDER,
                evidence_pack="docs/research/masterdoc.pdf#section-7",
                evidence_pack_sha256="598f4a6a5a7255d8281f0269ab6e1ea46376f5c1048a5baa01744c4f485f6a05",
                address_book="https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Base.sol",
                address_book_retrieved="2026-09-11", address_book_commit=None)


def _quantity(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value):
        raise RuntimeError("Base RPC returned an invalid hexadecimal quantity.")
    return int(value, 16)


def _load_oracle_metadata():
    return json.loads(files("risk_oracle.providers").joinpath("base_usdc_oracle_metadata.json").read_text())


class AaveV3BaseCollector:
    def __init__(self, provider: AlchemyProvider | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.provider = provider if provider is not None else AlchemyProvider()
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def collect(self, *, run_id: str, code_revision: str) -> ReserveSnapshot:
        """Collect USDC inputs at one safe block. Required reads fail closed.

        No latest/number-only fallback is attempted if hash-pinned eth_call is
        unsupported. No snapshot is returned on malformed/missing RPC results.
        """
        if _quantity(self.provider.rpc("eth_chainId", [])) != 8453:
            raise RuntimeError("Expected Base Mainnet (chain ID 8453).")
        header = self.provider.rpc("eth_getBlockByNumber", ["safe", False])
        if not isinstance(header, dict):
            raise RuntimeError("Base RPC returned no valid safe block.")
        try:
            block = BlockRef(
                chain_id=8453, number=_quantity(header.get("number")),
                hash=header.get("hash"), parent_hash=header.get("parentHash"),
                timestamp=datetime.fromtimestamp(_quantity(header.get("timestamp")), timezone.utc),
                finality="safe", canonicality_checked_at=self.clock(),
            )
        except (ValidationError, ValueError, OverflowError, OSError):
            raise RuntimeError("Base RPC returned an invalid safe block header.") from None
        metadata = _load_oracle_metadata()
        evidence = []
        observations = {}

        def unknown(at):
            return Freshness(status="UNKNOWN", evaluated_at=at, fresh_until=None, expires_at=None,
                             reasons=("freshness_policy_not_configured",))

        def read(contract, signature, outputs, args=(), parents=(), optional=False, rpc_method="eth_call"):
            input_types = signature[signature.index("(") + 1:-1].split(",") if args else []
            data = "0x" + (keccak(signature.encode())[:4] + encode(input_types, args)).hex() if rpc_method == "eth_call" else "0x"
            raw = None
            values = None
            error = None
            try:
                raw = self.provider.rpc(rpc_method, [
                    {"to": contract, "data": data} if rpc_method == "eth_call" else contract,
                    {"blockHash": block.hash, "requireCanonical": True},
                ])
                if not isinstance(raw, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2}){1,65536}", raw):
                    raise ValueError("invalid ABI bytes")
                values = decode(outputs, bytes.fromhex(raw[2:])) if rpc_method == "eth_call" else (raw,)
                if rpc_method == "eth_call" and encode(outputs, values).hex() != raw[2:].lower():
                    raise ValueError("noncanonical ABI")
            except RuntimeError:
                if not optional:
                    raise
                error = "RPC_READ_FAILED"
            except (DecodingError, ValueError, OverflowError):
                if not optional:
                    raise RuntimeError(f"Aave returned invalid ABI data for {signature}.") from None
                error = "INVALID_ABI_DATA"
            at = self.clock()
            record = EvidenceRecord(
                id="pending", content_hash="0x" + "00" * 32,
                canonicalization_version="sha256-sorted-json-excluding-id-and-content_hash-v1",
                source=dict(kind="onchain_rpc", provider_id="base_rpc", block=block,
                            contract=contract, method=rpc_method, call_signature=signature,
                            call_data=data, abi_hash=_digest(dict(signature=signature, outputs=outputs)),
                            decoder_version="eth-abi-v5-eip1898-requireCanonical"),
                collected_at=at, source_updated_at=None, run_id=run_id,
                code_revision=code_revision, normalizer_version="aave-usdc-inputs-v3",
                parent_evidence_ids=parents, transformation=None,
                outcome="failure" if error else "success", raw_result=None if error else raw,
                error_code=error,
            )
            payload = record.model_dump(mode="json")
            digest = _digest({key: value for key, value in payload.items()
                              if key not in ("id", "content_hash")})
            record = EvidenceRecord(**{**payload, "id": digest, "content_hash": digest})
            evidence.append(record)
            return (None if error else values), record

        def add(field, value, record, *, datatype="uint256", unit="integer", decimals=0,
                state="present", reason=None, parents=(), source_updated_at=None, onchain=True, quote_currency=None):
            observations[field] = Observation(
                id=f"{record.id}:{field}", field=field, state=state, datatype=datatype,
                value=value, unit=dict(name=unit, decimals=decimals, quote_currency=quote_currency),
                block=block if onchain else None, collected_at=record.collected_at, source_updated_at=source_updated_at,
                freshness=unknown(record.collected_at), evidence_ids=(record.id, *parents), reason=reason,
            )

        def derive(field, dependencies, calculate, formula, *, unit="ratio", decimals=27):
            """Integer-only normalization; failed dependencies remain explicit UNKNOWNs.

            A derived record retains its source identity, with the original RPC
            bytes in its parent records and its exact transformation separately.
            """
            inputs = [observations[name] for name in dependencies]
            parents = tuple(dict.fromkeys(ref for item in inputs for ref in item.evidence_ids))
            anchor = next(record for record in evidence if record.id == parents[0])
            values = [item.value for item in inputs]
            value, state, reason = None, "missing", "Required input unavailable; no inferred value."
            if all(item.state == "present" for item in inputs):
                value, state, reason = calculate(*values)
                if value is not None and not 0 <= value < 2**256:
                    value, state, reason = None, "invalid", "Derived result outside uint256 range."
            payload = anchor.model_dump(mode="json")
            payload.update(collected_at=self.clock().isoformat(), parent_evidence_ids=parents,
                           transformation=formula, outcome="success", error_code=None,
                           normalizer_version="aave-usdc-inputs-v3",
                           raw_result=json.dumps(dict(field=field, inputs={name: item.model_dump(mode="json")
                               for name, item in zip(dependencies, inputs)},
                               value=None if value is None else str(value), state=state), sort_keys=True))
            digest = _digest({key: value for key, value in payload.items() if key not in ("id", "content_hash")})
            record = EvidenceRecord(**{**payload, "id": digest, "content_hash": digest})
            evidence.append(record)
            add(field, value, record, unit=unit, decimals=decimals, state=state, reason=reason)

        def ratio(numerator, denominator, *, uncapped=False):
            if denominator == 0:
                return None, "not_applicable" if uncapped else "invalid", (
                    "Zero cap means uncapped." if uncapped else "Zero denominator; utilization is undefined.")
            return (numerator * 10**27 + denominator // 2) // denominator, "present", None

        words, config = read(DATA_PROVIDER, SIGNATURE, ["uint256"] * 5 + ["bool"] * 5, (USDC,))
        if words[0] != 6:
            raise RuntimeError("Aave returned an invalid native USDC configuration tuple.")
        for field, value in zip(FIELDS, words[1:5], strict=True):
            add(field, value, config, unit="basis_points", decimals=4)
        for field, value in (("borrowing_enabled", words[6]), ("stable_rate_borrowing_enabled", words[7]),
                             ("reserve_active", words[8]), ("reserve_frozen", words[9])):
            add(field, value, config, datatype="boolean", unit="boolean",
                reason="Deprecated compatibility getter since v3.2; not an available rate mode."
                if field == "stable_rate_borrowing_enabled" else None)

        caps, cap_record = read(DATA_PROVIDER, "getReserveCaps(address)", ["uint256", "uint256"], (USDC,))
        for field, value in zip(("borrow_cap", "supply_cap"), caps, strict=True):
            add(field, value, cap_record, unit="USDC_whole_tokens",
                reason="Zero means uncapped; raw cap is in whole tokens, not six-decimal atomic units.")
        debt, debt_record = read(DATA_PROVIDER, "getDebtCeiling(address)", ["uint256"], (USDC,))
        debt_scale, scale_record = read(DATA_PROVIDER, "getDebtCeilingDecimals()", ["uint256"])
        if debt_scale[0] > 255:
            raise RuntimeError("Aave returned invalid debt ceiling decimals.")
        add("debt_ceiling", debt[0], debt_record, unit="USD", decimals=debt_scale[0],
            parents=(scale_record.id,), reason="Deprecated compatibility getter; legacy isolation removed in v3.7.")
        paused, pause_record = read(DATA_PROVIDER, "getPaused(address)", ["bool"], (USDC,))
        add("reserve_paused", paused[0], pause_record, datatype="boolean", unit="boolean")
        for field in ("isolation_mode_enabled", "borrowable_in_isolation"):
            add(field, None, debt_record, datatype="boolean", unit="boolean", state="not_applicable",
                reason="Legacy isolation removed in the documented Base v3.7 deployment; not inferred from zero. "
                       + SOURCE_ROOT + "docs/3.7/mode-removal.md")

        # A reserve can participate in several current eModes; its old category bit is deprecated.
        reserve, reserve_record = read(POOL, "getReserveData(address)", [RESERVE_TYPE], (USDC,))
        # Resolve token addresses from this same block; never use deprecated
        # reserveData.interestRateStrategyAddress as the current strategy.
        a_token, variable_token = reserve[0][8], reserve[0][10]
        if ZERO_ADDRESS in (a_token, variable_token):
            raise RuntimeError("USDC reserve returned a zero token address.")
        add("a_token_address", a_token, reserve_record, datatype="text", unit="address")
        add("variable_debt_token_address", variable_token, reserve_record, datatype="text", unit="address")
        for field, contract, signature, args, unit, decimals in (
            ("total_supplied", a_token, "totalSupply()", (), "USDC", 6),
            ("variable_debt", variable_token, "totalSupply()", (), "USDC", 6),
            ("available_liquidity", USDC, "balanceOf(address)", (a_token,), "USDC", 6),
            ("virtual_available_liquidity", POOL, "getVirtualUnderlyingBalance(address)", (USDC,), "USDC", 6),
            ("scaled_total_supplied", a_token, "scaledTotalSupply()", (), "scaled_USDC", 6),
            ("liquidity_index", POOL, "getReserveNormalizedIncome(address)", (USDC,), "ray", 27),
        ):
            result, record = read(contract, signature, ["uint256"], args,
                                  parents=(reserve_record.id,), optional=True)
            add(field, result[0] if result is not None else None, record, unit=unit, decimals=decimals,
                state="present" if result is not None else "missing",
                reason=None if result is not None else "Reserve state read failed; UNKNOWN, not zero.")
        strategy, strategy_record = read(DATA_PROVIDER, "getInterestRateStrategyAddress(address)", ["address"],
                                         (USDC,), parents=(reserve_record.id,), optional=True)
        usable_strategy = strategy is not None and strategy[0] != ZERO_ADDRESS
        add("interest_rate_strategy", strategy[0] if usable_strategy else None, strategy_record,
            datatype="text", unit="address", state="present" if usable_strategy else "missing",
            reason=None if usable_strategy else "Current strategy unavailable; deprecated reserve field not substituted.")
        optimal, optimal_record = (None, strategy_record)
        if usable_strategy:
            optimal, optimal_record = read(strategy[0], "getOptimalUsageRatio(address)", ["uint256"], (USDC,),
                                            parents=(strategy_record.id,), optional=True)
        add("optimal_utilization", optimal[0] if optimal is not None else None, optimal_record,
            unit="ray", decimals=27, state="present" if optimal is not None else "missing",
            reason="Strategy utilization kink; not a score or safety guarantee. " + SOURCE_ROOT
                   + "src/contracts/misc/DefaultReserveInterestRateStrategyV2.sol"
                   if optimal is not None else "Strategy interface/read unavailable; no assumed utilization kink.")
        add("scaled_accrued_to_treasury", reserve[0][12], reserve_record, unit="scaled_USDC", decimals=6)
        derive("total_borrowed", ("variable_debt",), lambda debt: (debt, "present", None),
               "total_borrowed = variable_debt; stable debt removed in documented v3.7 deployment. "
               + SOURCE_ROOT + "docs/3.7/mode-removal.md", unit="USDC", decimals=6)
        derive("utilization_rate", ("variable_debt", "virtual_available_liquidity"),
               lambda debt, cash: ratio(debt, debt + cash),
               "rayDiv half-up: (debt * 10**27 + (debt + virtual_cash)//2)//(debt + virtual_cash). "
               + SOURCE_ROOT + "src/contracts/misc/DefaultReserveInterestRateStrategyV2.sol")
        derive("supply_cap_total_supplied", ("scaled_total_supplied", "scaled_accrued_to_treasury", "liquidity_index"),
               lambda supplied, treasury, index: ((supplied + treasury) * index // 10**27, "present", None)
               if index >= 10**27 else (None, "invalid", "Liquidity index below one ray."),
               "floor((scaled_supply + scaled_treasury) * normalized_income / 10**27). "
               + SOURCE_ROOT + "src/contracts/protocol/libraries/logic/ValidationLogic.sol; "
               + SOURCE_ROOT + "src/contracts/protocol/libraries/helpers/TokenMath.sol",
               unit="USDC", decimals=6)
        for field, numerator, cap in (("supply_cap_utilization", "supply_cap_total_supplied", "supply_cap"),
                                      ("borrow_cap_utilization", "variable_debt", "borrow_cap")):
            derive(field, (numerator, cap), lambda total, limit: ratio(total, limit * 10**6, uncapped=True),
                   "rayDiv half-up: atomic_total / (whole_token_cap * 10**6); zero cap is uncapped, not zero utilization.")

        reserve_id = reserve[0][7]
        if reserve_id >= 128:
            raise RuntimeError("USDC reserve ID is outside the eMode bitmap.")
        categories, modes_record = read(UI_DATA_PROVIDER, "getEModes(address)", [EMODES_TYPE],
                                        (ADDRESSES_PROVIDER,))
        ids = [category[0] for category in categories[0]]
        if len(ids) != len(set(ids)) or 0 in ids:
            raise RuntimeError("Aave returned invalid eMode category IDs.")
        add("emode_category", None, modes_record, state="not_applicable", unit="category_id",
            reason="Single reserve eMode category is deprecated; use observed per-category membership below.")
        for category_id, category in categories[0]:
            collateral = bool(category[3] & (1 << reserve_id))
            borrowable = bool(category[6] & (1 << reserve_id))
            if not collateral and not borrowable:
                continue
            prefix = f"emode_{category_id}"
            coverage = "UI enumeration stops after three empty IDs; membership is observed, not exhaustive."
            add(prefix + "_category", category_id, modes_record, unit="category_id",
                parents=(reserve_record.id,), reason=coverage)
            for suffix, value in (("collateral", collateral), ("borrowable", borrowable)):
                add(prefix + "_" + suffix, value, modes_record, datatype="boolean", unit="boolean",
                    parents=(reserve_record.id,), reason=coverage)
            # UI helper catches failures and substitutes false; never trust that fallback as data.
            isolated, isolated_record = read(POOL, "getIsEModeCategoryIsolated(uint8)", ["bool"],
                                             (category_id,), parents=(modes_record.id,), optional=True)
            add(prefix + "_isolated", isolated[0] if isolated else None, isolated_record,
                datatype="boolean", unit="boolean", state="present" if isolated else "missing",
                reason=None if isolated else "Category isolation read failed; no default false.")

        oracle, oracle_record = read(ADDRESSES_PROVIDER, "getPriceOracle()", ["address"])
        if oracle[0] == ZERO_ADDRESS:
            raise RuntimeError("Aave has no configured price oracle.")
        add("aave_price_oracle", oracle[0], oracle_record, datatype="text", unit="address")
        source, source_record = read(oracle[0], "getSourceOfAsset(address)", ["address"], (USDC,),
                                     parents=(oracle_record.id,))
        add("oracle_source", source[0], source_record, datatype="text", unit="address",
            reason="Configured primary source, not proof this source supplied a price; fallback not evaluated.")
        # Interface evidence is not proof of Chainlink authorship or a verified runtime code hash.
        add("oracle_source_is_chainlink", None, source_record, datatype="boolean", unit="boolean",
            state="unsupported", reason="Master evidence does not verify source identity; compatible interfaces cannot prove Chainlink identity.")
        if source[0] != ZERO_ADDRESS:
            upstream, upstream_record = read(source[0], "ASSET_TO_USD_AGGREGATOR()", ["address"],
                                              parents=(source_record.id,), optional=True)
            target, parent = source[0], source_record
            if upstream and upstream[0] != ZERO_ADDRESS:
                add("oracle_upstream_feed", upstream[0], upstream_record, datatype="text", unit="address")
                add("oracle_source_interface", "aave_stable_cap_adapter_compatible_unverified", upstream_record,
                    datatype="text", unit="classification",
                    reason="Adapter getter observed; runtime implementation and Chainlink identity are unverified.")
                target, parent = upstream[0], upstream_record
            aggregator, aggregator_record = read(target, "aggregator()", ["address"],
                                                 parents=(parent.id,), optional=True)
            if aggregator and aggregator[0] != ZERO_ADDRESS:
                add("oracle_feed_proxy", target, aggregator_record, datatype="text", unit="address")
                add("oracle_feed_aggregator", aggregator[0], aggregator_record, datatype="text", unit="address")
                version, version_record = read(aggregator[0], "typeAndVersion()", ["string"],
                                               parents=(aggregator_record.id,), optional=True)
                add("oracle_feed_type_and_version", version[0] if version else None, version_record,
                    datatype="text", unit="text", state="present" if version else "missing",
                    reason="Self-reported type; not independent verification of Chainlink identity.")
                if "oracle_source_interface" not in observations:
                    add("oracle_source_interface", "aggregator_proxy_compatible_unverified", aggregator_record,
                        datatype="text", unit="classification", reason="aggregator() observed; identity unverified.")
            if "oracle_source_interface" not in observations:
                add("oracle_source_interface", None, aggregator_record, datatype="text", unit="classification",
                    state="unsupported", reason="No supported oracle interface identified; preserve the source address.")
        else:
            add("oracle_source_interface", None, source_record, datatype="text", unit="classification",
                state="not_applicable", reason="Zero primary source; Aave delegates pricing to its fallback oracle.")

        # Reviewed off-chain evidence is bundled, never fetched during collection.
        # Match exact network/address and runtime bytes before assigning a verified type.
        def document(reference, section, body, document_hash):
            payload = dict(
                id="pending", content_hash="0x" + "00" * 32,
                canonicalization_version="sha256-sorted-json-excluding-id-and-content_hash-v1",
                source=dict(kind="official_document", reference=reference, section=section,
                            revision=metadata["retrieved_at"], document_hash=document_hash),
                collected_at=metadata["retrieved_at"], source_updated_at=None, run_id=run_id,
                code_revision=code_revision, normalizer_version="reviewed-oracle-metadata-v1",
                parent_evidence_ids=(), transformation=None, outcome="success",
                raw_result=json.dumps(body, sort_keys=True), error_code=None,
            )
            record = EvidenceRecord(**payload)
            payload = record.model_dump(mode="json")
            digest = _digest({key: value for key, value in payload.items() if key not in ("id", "content_hash")})
            record = EvidenceRecord(**{**payload, "id": digest, "content_hash": digest})
            evidence.append(record)
            return record

        verified_adapter = False
        adapter = metadata["adapter"]
        if source[0] == adapter["address"] and adapter["is_verified"] and adapter["is_fully_verified"]:
            code, code_record = read(source[0], "eth_getCode", [], parents=(source_record.id,),
                                     optional=True, rpc_method="eth_getCode")
            verified_adapter = bool(code) and "0x" + sha256(bytes.fromhex(code[0][2:])).hexdigest() == adapter["runtime_sha256"]
            if verified_adapter:
                doc = document(adapter["reference"], "Verified PriceCapAdapterStable source/runtime",
                               adapter, adapter["source_sha256"])
                add("oracle_source_interface", "aave_price_cap_adapter_stable", code_record,
                    datatype="text", unit="classification", parents=(doc.id,))
                add("oracle_source_is_chainlink", False, code_record, datatype="boolean", unit="boolean",
                    parents=(doc.id,), reason="Verified Aave adapter; Chainlink identity belongs to the upstream feed.")
                # This adapter exposes no independent update timestamp.
                add("oracle_source_updated_at", None, code_record, unit="unix_seconds", state="not_applicable",
                    parents=(doc.id,), reason="Adapter has no own publication timestamp; upstream updatedAt is separate.")

        if source[0] != ZERO_ADDRESS:
            target = observations.get("oracle_upstream_feed", observations["oracle_source"]).value
            feed_parent = observations.get("oracle_upstream_feed", observations["oracle_source"]).evidence_ids[0]
            feed_decimals, decimals_record = read(target, "decimals()", ["uint8"], parents=(feed_parent,), optional=True)
            add("oracle_feed_decimals", feed_decimals[0] if feed_decimals else None, decimals_record,
                unit="decimal_places", state="present" if feed_decimals else "missing",
                reason=None if feed_decimals else "Feed decimals unavailable; no assumed scale.")
            round_data, round_record = read(target, "latestRoundData()", ["uint80", "int256", "uint256", "uint256", "uint80"],
                                            parents=(feed_parent,), optional=True)
            updated = None
            if round_data and 0 < round_data[3] <= int(block.timestamp.timestamp()):
                updated = datetime.fromtimestamp(round_data[3], timezone.utc)
            add("oracle_feed_updated_at", round_data[3] if round_data else None, round_record,
                unit="unix_seconds", state="present" if round_data else "missing", source_updated_at=updated,
                reason=None if updated else "Missing, zero, or future feed timestamp; cannot establish freshness.")
            valid = round_data is not None and feed_decimals is not None
            add("oracle_feed_latest_answer", round_data[1] if valid else None, round_record,
                datatype="int256", unit="USD", decimals=feed_decimals[0] if feed_decimals else 0,
                quote_currency="USD", state="present" if valid else "missing", source_updated_at=updated,
                parents=(decimals_record.id,), reason=None if valid else "Feed answer or scale unavailable; raw response remains in evidence.")
            source_decimals, source_decimals_record = read(source[0], "decimals()", ["uint8"],
                                                           parents=(source_record.id,), optional=True)
            source_answer, source_answer_record = read(source[0], "latestAnswer()", ["int256"],
                                                       parents=(source_record.id,), optional=True)
            # Avoid duplicate evidence IDs if the source itself was the feed.
            evidence[:] = list({item.id: item for item in evidence}.values())
            add("oracle_source_decimals", source_decimals[0] if source_decimals else None, source_decimals_record,
                unit="decimal_places", state="present" if source_decimals else "missing",
                reason=None if source_decimals else "Source decimals unavailable.")
            valid_source = source_decimals is not None and source_answer is not None
            add("oracle_source_latest_answer", source_answer[0] if valid_source else None, source_answer_record,
                datatype="int256", unit="USD", decimals=source_decimals[0] if source_decimals else 0,
                quote_currency="USD", state="present" if valid_source else "missing",
                parents=(source_decimals_record.id,), reason="Source output is distinct from the uncapped upstream answer.")
            if verified_adapter:
                cap, cap_record = read(source[0], "getPriceCap()", ["int256"], parents=(source_record.id,), optional=True)
                add("oracle_source_price_cap", cap[0] if cap and source_decimals else None, cap_record,
                    datatype="int256", unit="USD", decimals=source_decimals[0] if source_decimals else 0,
                    quote_currency="USD", parents=(source_decimals_record.id,),
                    state="present" if cap and source_decimals else "missing", reason="Adapter price cap, not a feed heartbeat or price guarantee.")

            registry = metadata["registry"]
            matching = [entry for entry in registry["entries"] if target.lower() in
                        (str(entry.get("proxyAddress", "")).lower(), str(entry.get("secondaryProxyAddress", "")).lower())
                        and entry.get("docs", {}).get("blockchainName") == "Base"]
            matched = len(matching) == 1 and feed_decimals and matching[0].get("decimals") == feed_decimals[0]
            if matched:
                entry = matching[0]
                proxy = metadata.get("proxy", {})
                if target.lower() == proxy.get("address") and proxy.get("is_verified"):
                    proxy_code, proxy_code_record = read(target, "eth_getCode", [], parents=(feed_parent,),
                                                         optional=True, rpc_method="eth_getCode")
                    if proxy_code and "0x" + sha256(bytes.fromhex(proxy_code[0][2:])).hexdigest() == proxy["runtime_sha256"]:
                        proxy_doc = document(proxy["reference"], "Verified EACAggregatorProxy runtime", proxy, proxy["source_sha256"])
                        add("oracle_feed_proxy_type", "Chainlink EACAggregatorProxy", proxy_code_record,
                            datatype="text", unit="classification", parents=(proxy_doc.id,))
                doc = document(registry["reference"], "Base Mainnet USDC/USD: " + entry["path"], entry,
                               registry["document_sha256"])
                add("oracle_feed_is_chainlink", True, decimals_record, datatype="boolean", unit="boolean",
                    parents=(doc.id,), reason="Exact live upstream proxy address matches Chainlink's official Base registry; identity as documented at retrieval.")
                add("oracle_feed_heartbeat", entry["heartbeat"], doc, unit="seconds", onchain=False,
                    reason="Official documented heartbeat at metadata retrieval; not a publication SLA.")
                # Directory threshold is percent. Store integral millionths of one percent.
                deviation = Decimal(str(entry["threshold"])) * 1000000
                if deviation != deviation.to_integral_value():
                    raise RuntimeError("Unsupported deviation precision in reviewed metadata.")
                add("oracle_feed_deviation_threshold", int(deviation), doc, unit="percent", decimals=6,
                    onchain=False, reason="Official documented deviation percentage at metadata retrieval.")
            else:
                for field, datatype, unit in (("oracle_feed_is_chainlink", "boolean", "boolean"),
                                              ("oracle_feed_heartbeat", "uint256", "seconds"),
                                              ("oracle_feed_deviation_threshold", "uint256", "percent")):
                    add(field, None, decimals_record, datatype=datatype, unit=unit, state="unsupported",
                        reason="No exact official Base feed address/decimals match; UNKNOWN.")

        if "oracle_feed_updated_at" in observations:
            derive("oracle_age_seconds", ("oracle_feed_updated_at",),
                   lambda updated: (int(block.timestamp.timestamp()) - updated, "present", None)
                   if 0 < updated <= int(block.timestamp.timestamp())
                   else (None, "invalid", "Zero or future oracle timestamp; age UNKNOWN."),
                   "Pinned block timestamp minus upstream latestRoundData.updatedAt; not adapter publication time.",
                   unit="seconds", decimals=0)
            derive("oracle_age_to_heartbeat", ("oracle_age_seconds", "oracle_feed_heartbeat"),
                   lambda age, heartbeat: ratio(age, heartbeat),
                   "rayDiv half-up: oracle_age_seconds / officially documented heartbeat_seconds; no guessed heartbeat.")
        else:
            for field, unit, decimals in (("oracle_age_seconds", "seconds", 0),
                                          ("oracle_age_to_heartbeat", "ratio", 27)):
                add(field, None, source_record, unit=unit, decimals=decimals, state="missing",
                    reason="No supported primary feed timestamp; fallback oracle not evaluated.")

        sequencer = metadata["sequencer"]
        seq_doc = document(sequencer["reference"], "Base sequencer monitoring", sequencer, sequencer["document_sha256"])
        add("base_sequencer_monitoring_relevant", True, seq_doc, datatype="boolean", unit="boolean",
            onchain=False, reason=sequencer["note"])
        add("base_sequencer_uptime_feed", sequencer["address"], seq_doc, datatype="text", unit="address", onchain=False)

        collected_at = self.clock()
        snapshot = build_reserve_snapshot(
            snapshot_id=_digest(dict(evidence=[item.id for item in evidence], manifest=_digest(MANIFEST))),
            subject=ReserveSubject(chain_id=8453, protocol="aave-v3", pool=POOL, asset=USDC),
            block=block, collected_at=collected_at, run_id=run_id, manifest_hash=_digest(MANIFEST),
            observations=observations, evidence=evidence,
            critical_fields=set(FIELDS) | {"supply_cap", "borrow_cap", "reserve_paused", "oracle_source"},
            freshness=unknown(collected_at),
        )

        return apply_snapshot_freshness(snapshot, calculated_at=collected_at)
