"""Application settings and local filesystem path helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for SalesPulseBI."""

    model_config = SettingsConfigDict(
        env_prefix="SALESPULSEBI_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Path.home() / ".salespulsebi"
    host: str = "127.0.0.1"
    port: int = 8420
    default_currency: str = "USD"
    max_upload_mb: int = 250

    @property
    def db_path(self) -> Path:
        return self.data_dir / "metadata.db"

    @property
    def projects_root(self) -> Path:
        return self.data_dir / "projects"

    @property
    def demo_sources_dir(self) -> Path:
        return self.data_dir / "demo_sources"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        path = self.projects_root / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def project_raw_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "raw"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def project_parquet_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "dataset.parquet"

    def project_quality_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "quality_report.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached runtime settings instance."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
