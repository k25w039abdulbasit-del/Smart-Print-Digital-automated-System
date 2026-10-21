from __future__ import annotations

from alembic import context

from smartprint.server import models  # noqa: F401  (register tables)
from smartprint.server.config import get_settings
from smartprint.server.db import Base, make_engine

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = make_engine(get_settings().database_url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
