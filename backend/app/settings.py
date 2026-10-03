from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://cattrack:cattrack@localhost:5432/cattrack"
    anthropic_api_key: str | None = None
    claude_fast_model: str = "claude-sonnet-5-5"
    claude_reasoning_model: str = "claude-opus-5-5"
    config_dir: Path = BACKEND_DIR / "config"

    # fastembed BAAI/bge-small-en-v1.5 produces 384-dim vectors
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384


@lru_cache
def get_settings() -> Settings:
    return Settings()
