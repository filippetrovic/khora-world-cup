"""Application configuration for khora-world-cup.

Loads the repo-root ``.env`` then exposes a single ``Settings`` object plus a
``configure_khora_env`` helper that translates our settings into the ``KHORA_*``
environment variables the embedded khora backend reads at construction time.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the khora_wc package directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

# Populate os.environ from the repo-root .env before BaseSettings reads it.
# We do NOT override values already present in the real environment.
load_dotenv(ENV_PATH, override=False)


class Settings(BaseSettings):
    """Typed application settings.

    Values come from the process environment (which ``load_dotenv`` has already
    populated from the repo-root ``.env``). Extra/unknown env vars are ignored
    and matching is case-insensitive.
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        extra="ignore",
        case_sensitive=False,
    )

    # --- Secrets / external tokens -------------------------------------------
    openai_api_key: str
    data_football_token: str = ""
    newsdata_token: str = ""

    # --- Paths / namespace ----------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    namespace_label: str = "worldcup2026"

    # --- Model / embedding configuration -------------------------------------
    khora_llm_model: str = "gpt-4o-mini"
    khora_embedding_model: str = "text-embedding-3-small"
    khora_embedding_dimension: int = 1536
    answer_model: str = "gpt-4o-mini"

    # --- Derived path properties ---------------------------------------------
    @property
    def khora_db_path(self) -> Path:
        return self.data_dir / "khora" / "wc.db"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def failed_dir(self) -> Path:
        return self.data_dir / "failed"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    def inbox_for(self, source_type: str) -> Path:
        """Return the inbox subdirectory for a given source type."""
        return self.inbox_dir / source_type


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()


def configure_khora_env(settings: Settings) -> None:
    """Create data dirs and export the KHORA_* / OPENAI_API_KEY env vars.

    Must be called BEFORE constructing ``KhoraConfig`` / ``Khora`` so that the
    embedded ``sqlite_lance`` backend is selected and pointed at our db path.
    """
    # Ensure every directory the app writes to exists.
    for directory in (
        settings.data_dir,
        settings.khora_db_path.parent,
        settings.inbox_dir,
        settings.processed_dir,
        settings.failed_dir,
        settings.state_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    # OpenAI key (khora reads the var named by KHORA_LLM_API_KEY_ENV).
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key

    # Embedded sqlite + LanceDB backend selection.
    os.environ["KHORA_STORAGE_BACKEND"] = "sqlite_lance"
    os.environ["KHORA_STORAGE_SQLITE_LANCE_DB_PATH"] = str(settings.khora_db_path)
    os.environ["KHORA_STORAGE_SQLITE_LANCE_EMBEDDING_DIMENSION"] = str(
        settings.khora_embedding_dimension
    )

    # LLM / embedding model selection.
    os.environ["KHORA_LLM_MODEL"] = settings.khora_llm_model
    os.environ["KHORA_LLM_EMBEDDING_MODEL"] = settings.khora_embedding_model
    os.environ["KHORA_LLM_API_KEY_ENV"] = "OPENAI_API_KEY"
