from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import settings
from app.db.models import Base

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _add_missing_columns() -> None:
    """Minimal forward migration.

    `create_all` adds new tables but never new columns, so containers that were
    built before a column existed would crash on first query. Only additive
    changes are handled; anything more involved needs a real migration.
    """
    from sqlalchemy import inspect, text

    expected = {
        ("play_events", "seconds"): "INTEGER DEFAULT 0",
        ("playlists", "navidrome_id"): "VARCHAR(64)",
    }
    inspector = inspect(engine)

    with engine.begin() as connection:
        for (table, column), definition in expected.items():
            if not inspector.has_table(table):
                continue
            existing = {info["name"] for info in inspector.get_columns(table)}
            if column not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
