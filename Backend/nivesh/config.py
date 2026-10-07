from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent

# Provider API keys (OPENROUTER_API_KEY, ...) are read from os.environ, so load .env into it.
load_dotenv(BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NIVESH_", extra="ignore")

    data_dir: Path = REPO_DIR / "data"
    context_dir: Path = BACKEND_DIR / "context"
    llm_config: Path = BACKEND_DIR / "config" / "llm.yaml"

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
