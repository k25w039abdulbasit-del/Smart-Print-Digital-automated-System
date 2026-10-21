"""Runtime configuration (12-factor: everything comes from SMARTPRINT_* env vars)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMARTPRINT_", env_file=".env", extra="ignore")

    environment: Literal["dev", "prod"] = "dev"
    database_url: str = "sqlite:///./data/smartprint.db"
    data_dir: Path = Path("./data")

    secret_key: str = DEV_SECRET                 # signs staff tokens + print authorisations
    webhook_secret: str = "dev-webhook-secret"   # shared with the payment provider
    staff_token_ttl_s: int = 12 * 3600

    currency: str = "PKR"
    max_upload_mb: int = 50
    max_pages: int = 500
    convert_office_docs: bool = True             # docx/pptx/xlsx -> pdf via LibreOffice if present

    payment_provider: Literal["mock"] = "mock"
    payment_ttl_s: int = 600                     # online payment request lifetime
    file_retention_s: int = 600                  # customer files are deleted this long after the job ends
    draft_ttl_s: int = 3600                      # idle jobs are expired after this

    printer_backend: Literal["spool", "cups", "sumatra"] = "spool"
    sumatra_path: str = r"C:\Program Files\SumatraPDF\SumatraPDF.exe"
    default_printer: str = "Shop-Printer"
    xerox_backend: Literal["manual"] = "manual"

    worker_enabled: bool = True
    worker_poll_s: float = 0.5
    print_lease_s: int = 120                     # a PRINTING job without heartbeat is UNCERTAIN
    print_max_attempts: int = 3
    print_retry_backoff_s: float = 2.0
    janitor_interval_s: float = 15.0

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def spool_dir(self) -> Path:
        return self.data_dir / "spool"

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"

    def validate_for_runtime(self) -> None:
        if self.environment == "prod":
            if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
                raise RuntimeError("SMARTPRINT_SECRET_KEY must be set (>=32 chars) in prod")
            if self.webhook_secret == "dev-webhook-secret":
                raise RuntimeError("SMARTPRINT_WEBHOOK_SECRET must be set in prod")

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.files_dir, self.spool_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
