"""Database engine and session handling.

SQLite for the pilot; set paths.database to a postgresql+psycopg:// URL for the
400-submission run and nothing else needs to change.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import ensure_directories, load_config
from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def database_url() -> str:
    """The database URL.

    DATABASE_URL wins when set, so a deployment can point at Postgres without
    editing config.yaml. `postgres://` is normalised to the driver-qualified
    form SQLAlchemy needs — several providers hand out the bare scheme.
    """
    env = os.environ.get("DATABASE_URL", "").strip()
    if env:
        if env.startswith("postgres://"):
            env = env.replace("postgres://", "postgresql+psycopg://", 1)
        elif env.startswith("postgresql://"):
            env = env.replace("postgresql://", "postgresql+psycopg://", 1)
        return env

    cfg = load_config()
    raw = str(cfg.require("paths.database"))
    if "://" in raw:
        return raw
    ensure_directories()
    return f"sqlite:///{cfg.path('paths.database')}"


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        url = database_url()
        connect_args = {"timeout": 30} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")      # concurrent reads during a run
                cur.execute("PRAGMA foreign_keys=ON")       # cascades must actually cascade
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


# Columns added after a database was first created. SQLite cannot do this
# through create_all, and a full migration tool is overkill for a schema this
# size — but silently missing a column would break the portal, so each is
# added explicitly and idempotently.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("evaluations", "remarks", "TEXT DEFAULT ''"),
]


def _add_missing_columns() -> None:
    from sqlalchemy import text

    engine = get_engine()
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            if existing and column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                )
                print(f"  [db] added {table}.{column}")


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    _add_missing_columns()


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def new_session() -> Session:
    """Caller-managed session, for the web layer's dependency injection."""
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory()
