"""Minimal Base JSON-RPC access using the configured endpoint."""

import re

import httpx

from risk_oracle.config import load_settings


class AlchemyProvider:
    def rpc(self, method: str, params: list) -> object:
        """Send a JSON-RPC read using the existing endpoint and safe errors."""
        rpc_url = load_settings().base_rpc_url
        if not rpc_url:
            raise RuntimeError("BASE_RPC_URL is not configured.")

        try:
            response = httpx.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Base RPC request failed with HTTP status {exc.response.status_code}."
            ) from None
        except (httpx.RequestError, httpx.InvalidURL):
            raise RuntimeError("Base RPC request failed: connection, timeout, or URL error.") from None

        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("Base RPC returned invalid JSON.") from None

        if not isinstance(payload, dict):
            raise RuntimeError("Base RPC returned an invalid JSON-RPC response.")
        if "error" in payload:
            raise RuntimeError(f"Base RPC returned a JSON-RPC error for {method}.")
        if payload.get("jsonrpc") != "2.0" or payload.get("id") != 1:
            raise RuntimeError("Base RPC returned an invalid JSON-RPC response.")
        if "result" not in payload:
            raise RuntimeError("Base RPC returned an invalid JSON-RPC response.")
        return payload["result"]

    def get_latest_block_number(self) -> int:
        """Fetch the latest block number, raising RuntimeError on RPC failure."""
        result = self.rpc("eth_blockNumber", [])
        if not isinstance(result, str) or re.fullmatch(r"0x[0-9a-fA-F]+", result) is None:
            raise RuntimeError("Base RPC returned an invalid hexadecimal block number.")
        return int(result, 16)
