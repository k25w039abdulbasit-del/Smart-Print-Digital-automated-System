"""Alembic is the source of truth in production: it must produce exactly the schema the models declare."""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]


def test_migrations_match_models(tmp_path, monkeypatch):
    from smartprint.server import models  # noqa: F401
    from smartprint.server.config import get_settings
    from smartprint.server.db import Base
    url = f"sqlite:///{tmp_path}/m.db"
    monkeypatch.setenv("SMARTPRINT_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        command.upgrade(cfg, "head")
        with create_engine(url).connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
            assert compare_metadata(ctx, Base.metadata) == [], "models and migrations have drifted - add a migration"
    finally:
        get_settings.cache_clear()
