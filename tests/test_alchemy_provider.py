import httpx
import pytest

from risk_oracle.config import Settings
from risk_oracle.providers import alchemy


@pytest.fixture(autouse=True)
def mock_rpc(monkeypatch: pytest.MonkeyPatch) -> dict:
    state = {"payload": {"jsonrpc": "2.0", "id": 1, "result": "0x123abc"}, "status": 200}
    monkeypatch.setattr(alchemy, "load_settings", lambda: Settings(base_rpc_url="https://rpc.example.test"))

    def post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        state["request"] = (url, json, timeout)
        if "exception" in state:
            raise state["exception"]
        kwargs = {"content": state["content"]} if "content" in state else {"json": state["payload"]}
        return httpx.Response(state["status"], request=httpx.Request("POST", url), **kwargs)

    monkeypatch.setattr(alchemy.httpx, "post", post)
    return state


@pytest.mark.parametrize("result, expected", [("0x123abc", 1194684), ("0x0", 0)])
def test_latest_block_number(mock_rpc: dict, result: str, expected: int) -> None:
    mock_rpc["payload"]["result"] = result
    assert alchemy.AlchemyProvider().get_latest_block_number() == expected
    assert mock_rpc["request"] == (
        "https://rpc.example.test",
        {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
        10.0,
    )


def test_missing_configuration(monkeypatch: pytest.MonkeyPatch, mock_rpc: dict) -> None:
    monkeypatch.setattr(alchemy, "load_settings", lambda: Settings())
    with pytest.raises(RuntimeError, match="BASE_RPC_URL is not configured"):
        alchemy.AlchemyProvider().get_latest_block_number()
    assert "request" not in mock_rpc


def test_http_failure(mock_rpc: dict) -> None:
    mock_rpc["status"] = 503
    with pytest.raises(RuntimeError, match="HTTP status 503"):
        alchemy.AlchemyProvider().get_latest_block_number()


@pytest.mark.parametrize("error", [httpx.ConnectError("failed"), httpx.ReadTimeout("timeout"), httpx.InvalidURL("invalid")])
def test_request_failure(mock_rpc: dict, error: Exception) -> None:
    mock_rpc["exception"] = error
    with pytest.raises(RuntimeError, match="Base RPC request failed"):
        alchemy.AlchemyProvider().get_latest_block_number()


def test_rpc_failure(mock_rpc: dict) -> None:
    mock_rpc["payload"] = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "Internal error"}}
    with pytest.raises(RuntimeError, match="JSON-RPC error"):
        alchemy.AlchemyProvider().get_latest_block_number()


def test_invalid_json(mock_rpc: dict) -> None:
    mock_rpc["content"] = b"not json"
    with pytest.raises(RuntimeError, match="invalid JSON"):
        alchemy.AlchemyProvider().get_latest_block_number()


@pytest.mark.parametrize("payload", [[], {"jsonrpc": "1.0", "id": 1}, {"jsonrpc": "2.0", "id": 2}])
def test_invalid_envelope(mock_rpc: dict, payload: object) -> None:
    mock_rpc["payload"] = payload
    with pytest.raises(RuntimeError, match="invalid JSON-RPC response"):
        alchemy.AlchemyProvider().get_latest_block_number()


@pytest.mark.parametrize("result", [None, 123, "123", "0x", "0xZZ", "-0x1"])
def test_invalid_block_number(mock_rpc: dict, result: object) -> None:
    mock_rpc["payload"]["result"] = result
    with pytest.raises(RuntimeError, match="invalid hexadecimal block number"):
        alchemy.AlchemyProvider().get_latest_block_number()
