from pathlib import Path

import pytest

from risk_oracle.config import load_settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def test_base_rpc_url_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE_RPC_URL", "https://rpc.example.test")
    assert load_settings().base_rpc_url == "https://rpc.example.test"


def test_base_rpc_url_missing() -> None:
    assert load_settings().base_rpc_url is None


def test_base_rpc_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE_RPC_URL", "")
    assert load_settings().base_rpc_url is None


def test_base_rpc_url_from_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("BASE_RPC_URL=https://local.example.test\n")
    assert load_settings().base_rpc_url == "https://local.example.test"


@pytest.mark.parametrize("value", ["https://environment.example.test", ""])
def test_environment_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    (tmp_path / ".env").write_text("BASE_RPC_URL=https://local.example.test\n")
    monkeypatch.setenv("BASE_RPC_URL", value)
    assert load_settings().base_rpc_url == (value or None)


def test_empty_dotenv_url(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("BASE_RPC_URL=\n")
    assert load_settings().base_rpc_url is None


@pytest.mark.parametrize("source", ["environment", "dotenv", "missing"])
def test_supabase_configuration(monkeypatch, tmp_path, source):
    if source == "environment":
        monkeypatch.setenv("SUPABASE_URL", "https://config.example.test")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "synthetic-config-key")
    elif source == "dotenv":
        (tmp_path / ".env").write_text("SUPABASE_URL=https://config.example.test\nSUPABASE_SERVICE_KEY=synthetic-config-key\n")
    settings = load_settings()
    assert settings.supabase_url == (None if source == "missing" else "https://config.example.test")
    assert settings.supabase_service_key == (None if source == "missing" else "synthetic-config-key")
    assert "config.example.test" not in repr(settings)
    assert "synthetic-config-key" not in repr(settings)


def test_supabase_environment_overrides_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("SUPABASE_URL=https://config.example.test\nSUPABASE_SERVICE_KEY=synthetic-config-key\n")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "")
    settings = load_settings()
    assert settings.supabase_url is None and settings.supabase_service_key is None
