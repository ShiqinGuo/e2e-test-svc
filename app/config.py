from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="E2E_", extra="ignore")
    data_dir: Path = ROOT / "data"
    database_url: str = "postgresql+asyncpg://e2e@127.0.0.1:55432/e2e"
    database_schema: str | None = None
    base_url: str = "http://localhost:4100"
    app_url: str = "http://localhost:5173"
    trusted_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4100",
        "http://127.0.0.1:4100",
    ]
    cookie_secure: bool = False
    session_lifetime: int = Field(default=604800, gt=0)
    recording_lifetime: int = Field(default=1800, gt=0)
    max_concurrent_runs: int = Field(default=2, gt=0)
    max_recordings_per_user: int = Field(default=2, gt=0)
    auth_rate_limit: int = Field(default=30, gt=0)
    node_binary: str = "node"
