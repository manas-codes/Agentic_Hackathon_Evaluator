"""Database engine and session handling.

SQLite for the local pilot. Set the DATABASE_URL environment variable (or
paths.database in config.yaml) to a Postgres URL for any hosted deployment -
the serverless filesystem is read-only, so a SQLite file there would be lost
on every cold start along with every approval and score amendment recorded in
it.

DATABASE_URL wins over config.yaml, because a host sets it as an environment
variable and must not need the file edited to take effect.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import ensure_directories, is_serverless, load_config
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

    if is_serverless():
        # Falling back to SQLite here is always wrong and always silent. The
        # bundle's filesystem is read-only apart from the temp directory, so
        # the app comes up on an empty database, bootstraps a fresh admin,
        # answers "incorrect username or password" to correct credentials, and
        # loses every approval on each cold start. Refuse instead: a clear
        # failure at startup costs minutes, this failure costs an afternoon.
        raise RuntimeError(
            "DATABASE_URL is not set. This deployment has a read-only "
            "filesystem, so there is no SQLite file to fall back to. Set "
            "DATABASE_URL to the Postgres connection string in the hosting "
            "platform's environment variables - for every environment, not "
            "just one - and redeploy. Environment variable changes do not "
            "take effect until the next deployment."
        )

    ensure_directories()
    return f"sqlite:///{cfg.path('paths.database')}"


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        url = database_url()
        connect_args = {"timeout": 30} if url.startswith("sqlite") else {}
        kwargs: dict = {"future": True, "connect_args": connect_args}
        if not url.startswith("sqlite"):
            # A serverless invocation is short-lived and may be frozen between
            # requests, so a pooled connection held across invocations is
            # already dead when it is reused. Let the connection close with the
            # request and rely on the provider's own pooler (the `-pooler` host
            # in a Neon connection string).
            kwargs["pool_pre_ping"] = True
            if is_serverless():
                kwargs["poolclass"] = NullPool
        _engine = create_engine(url, **kwargs)
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
