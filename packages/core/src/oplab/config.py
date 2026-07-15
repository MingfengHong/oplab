from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Oplab"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./data/oplab.db"
    checkpoint_database_url: str = "./data/checkpoints.sqlite"

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-mini"

    openalex_mailto: str | None = None
    retrieval_limit: int = Field(default=8, ge=1, le=25)
    max_source_bytes: int = Field(default=5_000_000, ge=1_024)
    api_cors_origins: str = "http://localhost:3000"

    oplab_artifact_root: Path = Path("./artifacts")
    oplab_upload_root: Path = Path("./uploads")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    def ensure_directories(self) -> None:
        Path("./data").mkdir(parents=True, exist_ok=True)
        self.oplab_artifact_root.mkdir(parents=True, exist_ok=True)
        self.oplab_upload_root.mkdir(parents=True, exist_ok=True)
        Path(self.checkpoint_database_url).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
