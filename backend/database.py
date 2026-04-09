from __future__ import annotations

from collections.abc import Generator
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


settings = get_settings()
active_database_url = settings.database_url


def _can_use_filesystem_sqlite() -> bool:
    try:
        database_path = settings.database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.execute("create table if not exists __healthcheck (id integer primary key)")
        connection.commit()
        connection.close()
        return True
    except Exception:
        return False


class Base(DeclarativeBase):
    pass


def _enable_wal(dbapi_conn, connection_record):
    """Enable WAL mode for concurrent reads during writes."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


if _can_use_filesystem_sqlite():
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        future=True,
    )
    from sqlalchemy import event
    event.listen(engine, "connect", _enable_wal)
else:
    active_database_url = "sqlite+pysqlite:///:memory:"
    engine = create_engine(
        active_database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
