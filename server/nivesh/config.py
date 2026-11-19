import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = SERVER_DIR
REPO_DIR = SERVER_DIR.parent

# Provider API keys (OPENROUTER_API_KEY, ...) are read from os.environ, so load .env into it.
load_dotenv(SERVER_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NIVESH_", extra="ignore")

    data_dir: Path = REPO_DIR / "data"
    context_dir: Path = SERVER_DIR / "context"
    llm_config: Path = SERVER_DIR / "config" / "llm.yaml"
    # Prefer config/llm.local.yaml (gitignored, per machine) when it exists; tests turn this off.
    llm_local_override: bool = True
    # Extra local folders of Markdown notes for the copilot's knowledge search (JSON list in NIVESH_EXTRA_DOCS_DIRS).
    extra_docs_dirs: list[Path] = []

    # Clerk sign-in. Empty jwks url = auth off (local dev). Set it to https://<clerk-frontend-api>/.well-known/jwks.json.
    clerk_jwks_url: str = ""
    # Site origins a Clerk token may be issued for (token `azp`), e.g. ["https://ournivesh.duckdns.org"].
    authorized_parties: list[str] = []
    # Approved Clerk user ids. Accounts can also be approved in Clerk (public metadata role "user", exposed as the
    # `role` session-token claim). Anyone else gets 403 "access pending".
    allowed_user_ids: list[str] = []
    # Encrypts users' own model keys at rest (AES-GCM). 32 random bytes, urlsafe base64. Empty = feature off.
    keys_master_key: str = ""

    # Upstox Analytics Token (read-only market data). Read from UPSTOX_API_KEY or NIVESH_UPSTOX_API_KEY.
    upstox_api_key: str = ""

    @property
    def resolved_upstox_token(self) -> str:
        return self.upstox_api_key or os.environ.get("UPSTOX_API_KEY", "")

    # Per-user limits for signed-in users (the local user with sign-in off is not limited).
    user_copilot_per_day: int = 100
    user_sql_per_minute: int = 30
    user_key_tests_per_hour: int = 10

    query_timeout_s: float = 60.0
    max_result_rows: int = 500
    duckdb_memory_limit: str = "4GB"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


settings = Settings()
