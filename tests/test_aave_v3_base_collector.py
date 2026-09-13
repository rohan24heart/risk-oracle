"""HTTP is mocked for every test; no endpoint or secret is loaded."""
from datetime import datetime, timezone
import json

import httpx
import pytest
from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

from risk_oracle.config import Settings
from risk_oracle.models import ReserveSnapshot
from risk_oracle.providers import alchemy, aave_v3_base
from risk_oracle.providers.aave_v3_base import AaveV3BaseCollector, EMODES_TYPE, RESERVE_TYPE, FIELDS

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
BLOCK_HASH = "0x" + "11" * 32


def encode(values):
    return "0x" + "".join(f"{value:064x}" for value in values)


@pytest.fixture
def rpc(monkeypatch):
    state = dict(calls=[], chain="0x2105", header=dict(
        number="0x7b", hash=BLOCK_HASH, parentHash="0x" + "22" * 32,
        timestamp=hex(int(NOW.timestamp()) - 10)),
        raw=encode([6, 7500, 8000, 10500, 1000, 1, 1, 0, 1, 0]))
    state["by_contract"] = {
        ("0x" + "11" * 20, "totalSupply()"): (["uint256"], (100_000_000 * 10**6,)),
        ("0x" + "22" * 20, "totalSupply()"): (["uint256"], (60_000_000 * 10**6,)),
    }
    state["extra"] = {
        "getInterestRateStrategyAddress(address)": (["address"], ("0x" + "99" * 20,)),
        "getOptimalUsageRatio(address)": (["uint256"], (9 * 10**26,)),
        "totalSupply()": (["uint256"], (0,)),
        "balanceOf(address)": (["uint256"], (39_000_000 * 10**6,)),
        "getVirtualUnderlyingBalance(address)": (["uint256"], (40_000_000 * 10**6,)),
        "scaledTotalSupply()": (["uint256"], (100_000_000 * 10**6,)),
        "getReserveNormalizedIncome(address)": (["uint256"], (10**27,)),
        "getReserveCaps(address)": (["uint256", "uint256"], (120000000, 150000000)),
        "getDebtCeiling(address)": (["uint256"], (0,)),
        "getDebtCeilingDecimals()": (["uint256"], (2,)),
        "getPaused(address)": (["bool"], (False,)),
        "getReserveData(address)": ([RESERVE_TYPE], (((0,), 0, 0, 0, 0, 0, 0, 4,
            "0x" + "11" * 20, "0x" + "00" * 20, "0x" + "22" * 20, "0x" + "33" * 20, 0, 0, 0),)),
        "getEModes(address)": ([EMODES_TYPE], ([(3, (7500, 8000, 10500, 1, True, "synthetic", 16, 0))],)),
        "getIsEModeCategoryIsolated(uint8)": (["bool"], (True,)),
        "getPriceOracle()": (["address"], ("0x" + "44" * 20,)),
        "getSourceOfAsset(address)": (["address"], ("0x" + "55" * 20,)),
        "ASSET_TO_USD_AGGREGATOR()": (["address"], ("0x" + "66" * 20,)),
        "aggregator()": (["address"], ("0x" + "77" * 20,)),
        "typeAndVersion()": (["string"], ("AccessControlledOffchainAggregator 3.0.0",)),
    }
    state["extra"].update({
        "decimals()": (["uint8"], (8,)),
        "latestRoundData()": (["uint80", "int256", "uint256", "uint256", "uint80"],
                               (1, 99990000, int(NOW.timestamp()) - 60, int(NOW.timestamp()) - 60, 1)),
        "latestAnswer()": (["int256"], (99990000,)),
        "getPriceCap()": (["int256"], (104000000,)),
    })
    metadata = aave_v3_base._load_oracle_metadata()
    metadata["retrieved_at"] = NOW.isoformat()
    state["metadata"] = metadata
    monkeypatch.setattr(aave_v3_base, "_load_oracle_metadata", lambda: metadata)
    state["code"] = {}
    state["fail_methods"] = set()
    monkeypatch.setattr(alchemy, "load_settings", lambda: Settings(base_rpc_url="https://synthetic.test"))

    def post(url, *, json, timeout):
        assert url == "https://synthetic.test"
        assert timeout == 10.0
        state["calls"].append(json)
        method = json["method"]
        if "exception" in state:
            raise state["exception"]
        if method == "eth_call" and state.get("rpc_error"):
            payload = dict(jsonrpc="2.0", id=1, error={"message": "sensitive provider detail"})
        else:
            result = state["chain"] if method == "eth_chainId" else state["header"]
            if method == "eth_getCode":
                result = state["code"].get(json["params"][0], "0x")
            if method == "eth_call":
                selector = json["params"][0]["data"][2:10]
                if selector == "3e150141":
                    result = state["raw"]
                else:
                    signature = next(name for name in state["extra"] if keccak(name.encode())[:4].hex() == selector)
                    if signature in state["fail_methods"]:
                        return httpx.Response(200, json=dict(jsonrpc="2.0", id=1,
                            error={"message": "private detail"}), request=httpx.Request("POST", url))
                    types, values = state["by_contract"].get((json["params"][0]["to"], signature), state["extra"][signature])
                    result = "0x" + abi_encode(types, values).hex()
            payload = dict(jsonrpc="2.0", id=1, result=result)
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(alchemy.httpx, "post", post)
    return state


def collect():
    return AaveV3BaseCollector(clock=lambda: NOW).collect(run_id="test-run", code_revision="test-revision")


def test_collects_exact_four_fields_at_one_hash(rpc):
    snapshot = collect()
    assert {name: item.value for name, item in snapshot.observations.items() if name in FIELDS} == {
        "ltv": 7500, "liquidation_threshold": 8000, "liquidation_bonus": 10500, "reserve_factor": 1000,
    }
    assert snapshot.subject.asset == "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    assert snapshot.subject.pool == "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"
    assert [(call["method"], call["params"]) for call in rpc["calls"][:3]] == [
        ("eth_chainId", []), ("eth_getBlockByNumber", ["safe", False]),
        ("eth_call", [{"to": "0x0f43731eb8d45a581f4a36dd74f5f358bc90c73a",
                       "data": "0x3e150141000000000000000000000000833589fcd6edb6e08f4c7c32d4f71b54bda02913"},
                      {"blockHash": BLOCK_HASH, "requireCanonical": True}]),
    ]
    record = snapshot.evidence[0]
    assert record.raw_result == rpc["raw"]
    assert record.source.contract == rpc["calls"][2]["params"][0]["to"]
    assert record.source.call_signature == "getReserveConfigurationData(address)"
    assert record.collected_at == NOW and record.run_id == "test-run"
    assert snapshot.block.timestamp.timestamp() == NOW.timestamp() - 10
    assert snapshot.block.finality == "safe"
    for field in FIELDS:
        observation = snapshot.observations[field]
        assert observation.block == snapshot.block == record.source.block
        assert observation.collected_at == NOW and observation.source_updated_at is None
        assert observation.unit.name == "basis_points" and observation.unit.decimals == 4
        assert observation.evidence_ids == (record.id,)
    # Snapshot acquisition age is separate from input validity and scoring.
    assert snapshot.acquisition_status == "partial"
    assert snapshot.freshness.status == "FRESH"
    wire = snapshot.model_dump_json()
    assert "synthetic.test" not in wire
    assert ReserveSnapshot.model_validate_json(wire) == snapshot


@pytest.mark.parametrize("value", [0, 2**256 - 1])
def test_zero_and_exact_uint256_are_not_transformed(rpc, value):
    rpc["raw"] = encode([6, value, value, value, value, 0, 0, 0, 0, 0])
    snapshot = collect()
    assert all(item.value == value and item.state == "present" for name, item in snapshot.observations.items() if name in FIELDS)
    wire = json.loads(snapshot.model_dump_json())
    assert all(item["value"] == str(value) for name, item in wire["observations"].items() if name in FIELDS)


@pytest.mark.parametrize("raw", [None, "0x", "0x00", "0x" + "gg" * 320,
                                encode([6, 7500, 8000, 10500, 1000, 1, 1, 0, 1, 0, 0]),
                                encode([18, 7500, 8000, 10500, 1000, 1, 1, 0, 1, 0]),
                                encode([6, 7500, 8000, 10500, 1000, 2, 1, 0, 1, 0])])
def test_invalid_abi_never_becomes_zero(rpc, raw):
    rpc["raw"] = raw
    with pytest.raises(RuntimeError, match="Aave returned"):
        collect()


def test_wrong_chain_stops_before_contract_call(rpc):
    rpc["chain"] = "0x1"
    with pytest.raises(RuntimeError, match="Expected Base Mainnet"):
        collect()
    assert len(rpc["calls"]) == 1


@pytest.mark.parametrize("header", [None, {}, {"number": "0x7b", "hash": None,
    "parentHash": "0x" + "22" * 32, "timestamp": "0x1"}])
def test_missing_block_reference_stops_before_contract_call(rpc, header):
    rpc["header"] = header
    with pytest.raises(RuntimeError):
        collect()
    assert len(rpc["calls"]) == 2


def test_hash_pinned_rpc_failure_has_no_live_fallback(rpc):
    rpc["rpc_error"] = True
    with pytest.raises(RuntimeError, match="JSON-RPC error for eth_call") as error:
        collect()
    assert "sensitive" not in str(error.value)
    assert len(rpc["calls"]) == 3


def test_transport_error_is_sanitized(rpc):
    rpc["exception"] = httpx.ReadTimeout("secret URL")
    with pytest.raises(RuntimeError, match="connection, timeout, or URL error") as error:
        collect()
    assert "secret" not in str(error.value)


def test_extended_fields_units_and_provenance(rpc):
    snapshot = collect()
    observed = snapshot.observations
    assert {key: observed[key].value for key in (
        "supply_cap", "borrow_cap", "debt_ceiling", "borrowing_enabled", "stable_rate_borrowing_enabled",
        "reserve_active", "reserve_frozen", "reserve_paused")} == dict(
            supply_cap=150000000, borrow_cap=120000000, debt_ceiling=0, borrowing_enabled=True,
            stable_rate_borrowing_enabled=False, reserve_active=True, reserve_frozen=False, reserve_paused=False)
    assert observed["supply_cap"].unit.name == "USDC_whole_tokens"
    assert observed["supply_cap"].unit.decimals == 0
    assert observed["debt_ceiling"].unit.decimals == 2
    assert "Deprecated" in observed["debt_ceiling"].reason
    assert "Deprecated" in observed["stable_rate_borrowing_enabled"].reason
    for name in ("isolation_mode_enabled", "borrowable_in_isolation", "emode_category"):
        assert observed[name].value is None and observed[name].state == "not_applicable"
    records = {record.id: record for record in snapshot.evidence}
    for item in observed.values():
        assert item.block == snapshot.block or item.block is None
        assert item.collected_at == NOW
        assert item.unit.name
        for reference in item.evidence_ids:
            assert getattr(records[reference].source, "block", snapshot.block) == snapshot.block
    for request in rpc["calls"][2:]:
        assert request["params"][1] == {"blockHash": BLOCK_HASH, "requireCanonical": True}


@pytest.mark.parametrize("value", [0, 2**256 - 1])
def test_caps_and_debt_preserve_raw_integers(rpc, value):
    rpc["extra"]["getReserveCaps(address)"] = (["uint256", "uint256"], (value, value))
    rpc["extra"]["getDebtCeiling(address)"] = (["uint256"], (value,))
    snapshot = collect()
    restored = ReserveSnapshot.model_validate_json(snapshot.model_dump_json())
    for field in ("supply_cap", "borrow_cap", "debt_ceiling"):
        assert restored.observations[field].value == value
        assert restored.observations[field].state == "present"


def test_emode_membership_is_not_a_legacy_single_category(rpc):
    snapshot = collect()
    obs = snapshot.observations
    assert obs["emode_3_category"].value == 3
    assert obs["emode_3_borrowable"].value is True
    assert obs["emode_3_collateral"].value is False
    assert obs["emode_3_isolated"].value is True
    assert "not exhaustive" in obs["emode_3_category"].reason


def test_emode_isolation_failure_does_not_default_to_false(rpc):
    rpc["fail_methods"].add("getIsEModeCategoryIsolated(uint8)")
    result = collect().observations["emode_3_isolated"]
    assert result.state == "missing" and result.value is None


def test_adapter_and_chainlink_compatible_proxy_keep_distinct_addresses(rpc):
    snapshot = collect()
    obs = snapshot.observations
    for name, byte in (("aave_price_oracle", "44"), ("oracle_source", "55"),
                       ("oracle_upstream_feed", "66"), ("oracle_feed_proxy", "66"),
                       ("oracle_feed_aggregator", "77")):
        assert obs[name].value == "0x" + byte * 20
    assert obs["oracle_source_interface"].value == "aave_stable_cap_adapter_compatible_unverified"
    assert obs["oracle_feed_type_and_version"].value == "AccessControlledOffchainAggregator 3.0.0"
    assert obs["oracle_source_is_chainlink"].value is None
    assert obs["oracle_source_is_chainlink"].freshness.status == "UNKNOWN"


def test_direct_proxy_interface_is_not_proof_of_chainlink_identity(rpc):
    rpc["fail_methods"].add("ASSET_TO_USD_AGGREGATOR()")
    obs = collect().observations
    assert obs["oracle_feed_proxy"].value == obs["oracle_source"].value
    assert obs["oracle_source_interface"].value == "aggregator_proxy_compatible_unverified"
    assert obs["oracle_source_is_chainlink"].value is None


def test_unknown_source_preserves_address_and_failed_probe_evidence(rpc):
    rpc["fail_methods"].update({"ASSET_TO_USD_AGGREGATOR()", "aggregator()"})
    snapshot = collect()
    assert snapshot.observations["oracle_source"].value == "0x" + "55" * 20
    assert snapshot.observations["oracle_source_interface"].state == "unsupported"
    failed = [record for record in snapshot.evidence if record.outcome == "failure"]
    assert len(failed) == 2
    assert all(record.raw_result is None and record.error_code == "RPC_READ_FAILED" for record in failed)
    assert "private detail" not in snapshot.model_dump_json()


def test_zero_primary_source_does_not_probe_zero_address(rpc):
    rpc["extra"]["getSourceOfAsset(address)"] = (["address"], ("0x" + "00" * 20,))
    snapshot = collect()
    assert snapshot.observations["oracle_source"].value == "0x" + "00" * 20
    assert snapshot.observations["oracle_source_interface"].state == "not_applicable"
    assert all(call["params"][0]["to"] != "0x" + "00" * 20 for call in rpc["calls"][2:])


def test_required_new_read_failure_never_becomes_zero(rpc):
    rpc["fail_methods"].add("getReserveCaps(address)")
    with pytest.raises(RuntimeError, match="JSON-RPC error"):
        collect()


def verified_metadata(rpc):
    from hashlib import sha256
    metadata = rpc["metadata"]
    adapter = "0x" + "55" * 20
    proxy = "0x" + "66" * 20
    metadata["adapter"].update(address=adapter, runtime_sha256="0x" + sha256(bytes.fromhex("6001")).hexdigest())
    metadata["proxy"].update(address=proxy, runtime_sha256="0x" + sha256(bytes.fromhex("6002")).hexdigest())
    metadata["registry"]["entries"] = [dict(name="USDC / USD", path="synthetic-usdc-usd", proxyAddress=proxy,
        decimals=8, heartbeat=86400, threshold="0.3", docs=dict(blockchainName="Base"))]
    rpc["code"].update({adapter: "0x6001", proxy: "0x6002"})


def test_verified_adapter_feed_metadata_and_distinct_timestamps(rpc):
    verified_metadata(rpc)
    snapshot = collect()
    obs = snapshot.observations
    assert obs["oracle_source_interface"].value == "aave_price_cap_adapter_stable"
    assert obs["oracle_source_is_chainlink"].value is False
    assert obs["oracle_feed_is_chainlink"].value is True
    assert obs["oracle_feed_proxy_type"].value == "Chainlink EACAggregatorProxy"
    assert obs["oracle_source_updated_at"].value is None
    assert obs["oracle_source_updated_at"].state == "not_applicable"
    assert obs["oracle_feed_decimals"].value == 8
    assert obs["oracle_feed_latest_answer"].value == 99990000
    assert obs["oracle_feed_latest_answer"].unit.decimals == 8
    assert obs["oracle_feed_latest_answer"].source_updated_at.timestamp() == NOW.timestamp() - 60
    assert obs["oracle_feed_updated_at"].value == int(NOW.timestamp()) - 60
    assert obs["oracle_feed_heartbeat"].value == 86400
    assert obs["oracle_feed_deviation_threshold"].value == 300000
    assert obs["oracle_feed_deviation_threshold"].unit.decimals == 6
    assert obs["oracle_feed_heartbeat"].block is None
    assert obs["base_sequencer_monitoring_relevant"].value is True
    assert ReserveSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_changed_runtime_cannot_be_called_verified_adapter(rpc):
    verified_metadata(rpc)
    rpc["code"]["0x" + "55" * 20] = "0x6003"
    obs = collect().observations
    assert obs["oracle_source_is_chainlink"].value is None
    assert obs["oracle_source_interface"].value.endswith("unverified")


@pytest.mark.parametrize("change", ["address", "network", "decimals"])
def test_official_metadata_requires_exact_feed_match(rpc, change):
    verified_metadata(rpc)
    entry = rpc["metadata"]["registry"]["entries"][0]
    if change == "address":
        entry["proxyAddress"] = "0x" + "88" * 20
    elif change == "network":
        entry["docs"]["blockchainName"] = "Ethereum"
    else:
        entry["decimals"] = 18
    obs = collect().observations
    assert obs["oracle_feed_is_chainlink"].value is None
    assert obs["oracle_feed_heartbeat"].value is None
    assert obs["oracle_feed_deviation_threshold"].value is None


@pytest.mark.parametrize("answer", [0, -1, -(2**255)])
def test_raw_answers_are_lossless_and_never_guessed(rpc, answer):
    rpc["extra"]["latestRoundData()"] = (["uint80", "int256", "uint256", "uint256", "uint80"],
        (1, answer, int(NOW.timestamp()) - 60, int(NOW.timestamp()) - 60, 1))
    obs = ReserveSnapshot.model_validate_json(collect().model_dump_json()).observations
    assert obs["oracle_feed_latest_answer"].value == answer


def test_missing_round_data_preserves_unknown(rpc):
    rpc["fail_methods"].add("latestRoundData()")
    obs = collect().observations
    assert obs["oracle_feed_latest_answer"].value is None
    assert obs["oracle_feed_updated_at"].value is None
    assert obs["oracle_feed_updated_at"].source_updated_at is None


def test_invalid_update_time_is_not_promoted_to_fresh_timestamp(rpc):
    rpc["extra"]["latestRoundData()"] = (["uint80", "int256", "uint256", "uint256", "uint80"], (1, 100, 0, 0, 1))
    obs = collect().observations
    assert obs["oracle_feed_updated_at"].value == 0
    assert obs["oracle_feed_latest_answer"].source_updated_at is None
    assert obs["oracle_feed_latest_answer"].freshness.status == "UNKNOWN"


def test_reserve_balances_ratios_and_derived_evidence(rpc):
    verified_metadata(rpc)
    types, values = rpc["extra"]["getReserveData(address)"]
    reserve = list(values[0])
    reserve[12] = 5_000_000 * 10**6
    rpc["extra"]["getReserveData(address)"] = (types, (tuple(reserve),))
    snapshot = collect()
    obs = snapshot.observations
    expected = dict(total_supplied=100_000_000 * 10**6, variable_debt=60_000_000 * 10**6,
                    total_borrowed=60_000_000 * 10**6, available_liquidity=39_000_000 * 10**6,
                    virtual_available_liquidity=40_000_000 * 10**6, utilization_rate=6 * 10**26,
                    supply_cap_total_supplied=105_000_000 * 10**6,
                    supply_cap_utilization=7 * 10**26, borrow_cap_utilization=5 * 10**26,
                    optimal_utilization=9 * 10**26, oracle_age_seconds=50, oracle_age_to_heartbeat=(50 * 10**27 + 43200)//86400)
    records = {record.id: record for record in snapshot.evidence}
    for field, value in expected.items():
        item = obs[field]
        assert item.value == value and item.state == "present"
        assert item.block == snapshot.block and item.collected_at == NOW
        record = records[item.evidence_ids[0]]
        assert record.source.block == snapshot.block
        if field in ("utilization_rate", "supply_cap_utilization", "oracle_age_to_heartbeat"):
            assert item.unit.decimals == 27
            assert record.transformation and record.parent_evidence_ids
            assert all(parent in records for parent in record.parent_evidence_ids)
    assert obs["available_liquidity"].unit.decimals == 6
    assert ReserveSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


@pytest.mark.parametrize("debt", [0, 2**256 - 1])
def test_reserve_state_zero_and_large_integer_roundtrip(rpc, debt):
    rpc["by_contract"][("0x" + "22" * 20, "totalSupply()")] = (["uint256"], (debt,))
    snapshot = ReserveSnapshot.model_validate_json(collect().model_dump_json())
    assert snapshot.observations["variable_debt"].value == debt
    assert snapshot.observations["total_borrowed"].value == debt
    assert snapshot.observations["variable_debt"].state == "present"
    if debt == 0:
        assert snapshot.observations["utilization_rate"].value == 0
        assert snapshot.observations["borrow_cap_utilization"].value == 0


@pytest.mark.parametrize("method, fields", [
    ("totalSupply()", ("total_supplied", "variable_debt", "total_borrowed", "utilization_rate", "borrow_cap_utilization")),
    ("getVirtualUnderlyingBalance(address)", ("virtual_available_liquidity", "utilization_rate")),
    ("scaledTotalSupply()", ("scaled_total_supplied", "supply_cap_total_supplied", "supply_cap_utilization")),
    ("balanceOf(address)", ("available_liquidity",)),
    ("getInterestRateStrategyAddress(address)", ("interest_rate_strategy", "optimal_utilization")),
    ("getOptimalUsageRatio(address)", ("optimal_utilization",)),
    ("latestRoundData()", ("oracle_age_seconds", "oracle_age_to_heartbeat")),
])
def test_missing_reserve_inputs_never_become_zero(rpc, method, fields):
    rpc["fail_methods"].add(method)
    obs = collect().observations
    for field in fields:
        assert obs[field].value is None and obs[field].state == "missing"
        assert obs[field].freshness.status == "UNKNOWN"


def test_uncapped_and_empty_reserve_ratios_are_not_zero(rpc):
    rpc["extra"]["getReserveCaps(address)"] = (["uint256", "uint256"], (0, 0))
    rpc["extra"]["getVirtualUnderlyingBalance(address)"] = (["uint256"], (0,))
    rpc["by_contract"][("0x" + "22" * 20, "totalSupply()")] = (["uint256"], (0,))
    obs = collect().observations
    for field in ("supply_cap_utilization", "borrow_cap_utilization"):
        assert obs[field].value is None and obs[field].state == "not_applicable"
    assert obs["utilization_rate"].value is None and obs["utilization_rate"].state == "invalid"
    assert obs["oracle_age_seconds"].value == 50
    assert obs["oracle_age_to_heartbeat"].value is None


@pytest.mark.parametrize("timestamp", [0, int(NOW.timestamp()) + 1])
def test_oracle_age_rejects_invalid_timestamps(rpc, timestamp):
    verified_metadata(rpc)
    rpc["extra"]["latestRoundData()"] = (["uint80", "int256", "uint256", "uint256", "uint80"],
                                        (1, 100, timestamp, timestamp, 1))
    obs = collect().observations
    assert obs["oracle_age_seconds"].state == "invalid"
    assert obs["oracle_age_seconds"].value is None
    assert obs["oracle_age_to_heartbeat"].value is None


def test_supply_cap_accounting_floors_index_product(rpc):
    rpc["extra"]["scaledTotalSupply()"] = (["uint256"], (3,))
    rpc["extra"]["getReserveNormalizedIncome(address)"] = (["uint256"], (15 * 10**26,))
    assert collect().observations["supply_cap_total_supplied"].value == 4


def test_current_strategy_is_resolved_instead_of_deprecated_reserve_field(rpc):
    snapshot = collect()
    assert snapshot.observations["interest_rate_strategy"].value == "0x" + "99" * 20
    record = next(item for item in snapshot.evidence
                  if item.id == snapshot.observations["optimal_utilization"].evidence_ids[0])
    assert record.source.contract == "0x" + "99" * 20
    assert record.source.call_signature == "getOptimalUsageRatio(address)"
