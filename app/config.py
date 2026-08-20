"""Central application settings.

Every tunable lives here rather than being scattered through the pipeline, so
the security posture of the app can be reviewed in one place. Values can be
overridden with ``EMAIL_AGENT_*`` environment variables or a local ``.env``
file, which keeps deployment concerns out of the code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MEGABYTE = 1024 * 1024

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class Settings(BaseSettings):
    """Runtime configuration with conservative, local-only defaults."""

    model_config = SettingsConfigDict(
        env_prefix="EMAIL_AGENT_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    # --- Local model -----------------------------------------------------
    # Loopback only: the pipeline must never be able to reach a remote host.
    ollama_host: str = "http://127.0.0.1:11434"
    model_name: str = "qwen3:4b"
    fallback_model_name: str = "llama3.2:3b"
    request_timeout_seconds: float = Field(default=180.0, gt=0)
    model_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    model_seed: int = 42
    model_context_tokens: int = Field(default=8192, gt=0)
    max_model_retries: int = Field(default=1, ge=0)

    # --- Attachment handling --------------------------------------------
    max_attachment_bytes: int = Field(default=10 * MEGABYTE, gt=0)
    max_total_attachment_bytes: int = Field(default=25 * MEGABYTE, gt=0)
    max_upload_bytes: int = Field(default=30 * MEGABYTE, gt=0)
    max_attachment_count: int = Field(default=20, gt=0)
    extraction_timeout_seconds: float = Field(default=20.0, gt=0)
    max_extracted_chars: int = Field(default=40_000, gt=0)

    # --- Identity --------------------------------------------------------
    # Which address in a thread is "me". Without it the model has to guess who
    # owes what, which it gets wrong on threads the user sent themselves.
    owner_address: str | None = None

    # --- Privacy ---------------------------------------------------------
    pii_backend: str = Field(default="regex", pattern="^(regex|presidio|none)$")

    # --- Storage ---------------------------------------------------------
    database_path: Path = Path("data/history.db")
    history_limit: int = Field(default=50, gt=0)

    @property
    def allowed_mime_types(self) -> frozenset[str]:
        """File types the pipeline is willing to open. Everything else is refused."""
        return frozenset({PDF_MIME, DOCX_MIME})

    @model_validator(mode="after")
    def _check_budgets(self) -> Settings:
        if self.max_total_attachment_bytes < self.max_attachment_bytes:
            raise ValueError(
                "max_total_attachment_bytes must be >= max_attachment_bytes; "
                "a single attachment can never be allowed to exceed the thread budget"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so configuration is read once and stays consistent for the lifetime
    of the process; tests construct :class:`Settings` directly instead.
    """
    return Settings()
