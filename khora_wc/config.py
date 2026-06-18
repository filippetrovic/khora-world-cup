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
    api_football_token: str = ""  # api-sports.io v3 (per-match lineups)

    # --- Paths / namespace ----------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    namespace_label: str = "worldcup2026"

    # --- Backend selection ----------------------------------------------------
    # "postgres" = Docker Postgres+pgvector (relational+vector) + Neo4j (graph).
    # "embedded" = the in-process sqlite_lance store (data/khora/wc.db).
    # Reads env KHORA_BACKEND; defaults to the Postgres/Neo4j stack.
    khora_backend: str = "postgres"

    # Postgres (relational + pgvector). Defaults match compose.yaml. The URL is
    # handed to khora as-is; khora's session layer rewrites the scheme to
    # postgresql+asyncpg:// for the async driver.
    # 127.0.0.1 (not "localhost"): localhost resolves to both ::1 (IPv6) and
    # 127.0.0.1, but the Docker port maps IPv4 only — connections that resolve to
    # ::1 are refused (Errno 61), which under ingest concurrency intermittently
    # failed graph/relational writes. Pinning IPv4 makes every connection land.
    pg_url: str = "postgresql://khora:khora_dev@127.0.0.1:5432/khora"

    # Neo4j graph backend. Defaults match compose.yaml. IPv4-pinned (see pg_url).
    neo4j_url: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "khora_dev"
    neo4j_database: str = "neo4j"

    # --- Model / embedding configuration -------------------------------------
    khora_llm_model: str = "gpt-4o-mini"
    khora_embedding_model: str = "text-embedding-3-small"
    khora_embedding_dimension: int = 1536
    # Read-side answer agent model. Override per-environment with ANSWER_MODEL in
    # .env (case-insensitive); the committed default stays fast/cheap.
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

    Must be called BEFORE constructing ``KhoraConfig`` / ``Khora``. Selects the
    storage backend from ``settings.khora_backend``:

    * ``"postgres"`` (default) — Postgres+pgvector for relational+vector storage
      plus Neo4j for the graph. Sets ``KHORA_STORAGE_BACKEND=postgres``,
      ``KHORA_DATABASE_URL`` (relational, also used by the migration runner),
      the pgvector URL/dimension, and the Neo4j graph URL/credentials. The
      sqlite_lance env vars are deliberately NOT set.
    * ``"embedded"`` — the in-process ``sqlite_lance`` store at
      ``data/khora/wc.db`` (the original fallback backend).
    """
    # Ensure every directory the app writes to exists (both backends use these
    # for inbox/processed/state; only embedded uses the khora db dir).
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

    if settings.khora_backend == "embedded":
        # Embedded sqlite + LanceDB backend selection.
        os.environ["KHORA_STORAGE_BACKEND"] = "sqlite_lance"
        os.environ["KHORA_STORAGE_SQLITE_LANCE_DB_PATH"] = str(settings.khora_db_path)
        os.environ["KHORA_STORAGE_SQLITE_LANCE_EMBEDDING_DIMENSION"] = str(
            settings.khora_embedding_dimension
        )
    else:
        # Postgres+pgvector (relational+vector) + Neo4j (graph) backend.
        os.environ["KHORA_STORAGE_BACKEND"] = "postgres"
        # Relational Postgres URL — shortcut consumed both by the store and by
        # the Alembic migration runner (run_migrations=True).
        os.environ["KHORA_DATABASE_URL"] = settings.pg_url
        # pgvector: point it at the same Postgres and pin dimension to 1536
        # (khora's Postgres path hard-requires 1536 — Vector(1536) columns).
        os.environ["KHORA_STORAGE_VECTOR_BACKEND"] = "pgvector"
        os.environ["KHORA_STORAGE_VECTOR_URL"] = settings.pg_url
        os.environ["KHORA_STORAGE_VECTOR_EMBEDDING_DIMENSION"] = "1536"
        # Neo4j graph backend.
        os.environ["KHORA_STORAGE_GRAPH_BACKEND"] = "neo4j"
        os.environ["KHORA_STORAGE_GRAPH_URL"] = settings.neo4j_url
        os.environ["KHORA_STORAGE_GRAPH_USER"] = settings.neo4j_user
        os.environ["KHORA_STORAGE_GRAPH_PASSWORD"] = settings.neo4j_password
        os.environ["KHORA_STORAGE_GRAPH_DATABASE"] = settings.neo4j_database

    # LLM / embedding model selection.
    os.environ["KHORA_LLM_MODEL"] = settings.khora_llm_model
    os.environ["KHORA_LLM_EMBEDDING_MODEL"] = settings.khora_embedding_model
    os.environ["KHORA_LLM_API_KEY_ENV"] = "OPENAI_API_KEY"
