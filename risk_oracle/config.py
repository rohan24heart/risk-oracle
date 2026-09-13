"""Environment configuration for future RPC integration."""

import os
from dataclasses import dataclass, field

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    base_rpc_url: str | None = None
    supabase_url: str | None = field(default=None, repr=False)
    supabase_service_key: str | None = field(default=None, repr=False)


def load_settings() -> Settings:
    """Read .env from the working directory; environment variables take precedence.

    Missing or empty values leave the RPC URL unconfigured.
    """
    local_config = dotenv_values(".env")
    return Settings(
        base_rpc_url=os.environ.get("BASE_RPC_URL", local_config.get("BASE_RPC_URL"))
        or None,
        supabase_url=os.environ.get("SUPABASE_URL", local_config.get("SUPABASE_URL")) or None,
        supabase_service_key=os.environ.get("SUPABASE_SERVICE_KEY", local_config.get("SUPABASE_SERVICE_KEY")) or None,
    )
