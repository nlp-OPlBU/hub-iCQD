from alembic import context

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_db_engine
import app.models  # noqa: F401 -- register every model before comparing metadata

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=get_settings().mysql_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # Tests may supply an isolated connection. CLI always uses the central MySQL config.
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_db_engine(get_settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
