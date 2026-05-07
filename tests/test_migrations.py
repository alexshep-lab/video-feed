"""Regression tests for the schema migration helper.

Older databases predate columns we added later. SQLAlchemy's create_all()
won't modify existing tables, so the inline ALTER TABLE migrations must run
on every startup. The high-impact case the reviewer caught was
``library_folders.is_incoming``: an old DB booted fine but
``GET /api/libraries`` then crashed with "no such column" the first time
the table was queried.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import create_engine


def _seed_old_library_folders_schema(db_path: Path) -> None:
    """Create a library_folders table without is_incoming, mimicking pre-fix DBs."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE library_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path VARCHAR(2048) UNIQUE NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            display_name VARCHAR(255),
            added_at DATETIME
        )
        """
    )
    conn.execute(
        "INSERT INTO library_folders (path, enabled, display_name) "
        "VALUES ('C:/old/library', 1, 'old')"
    )
    conn.commit()
    conn.close()


def test_migrate_tables_adds_is_incoming():
    """An old DB without is_incoming must be patched in place by _migrate_tables.

    Builds the DB with sqlite3 directly (bypassing the model), points a fresh
    engine at it, runs the migration, and asserts the column now exists with
    the right default.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "old.db"
        _seed_old_library_folders_schema(db_path)

        # Build an engine bound to the seeded file, then call the migration
        # helper against it directly. We rebind the module-level ``engine``
        # for the duration of the call so the helper picks up our throwaway
        # connection rather than the test-suite's shared one.
        from backend import main as main_module

        target_engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        original_engine = main_module.engine
        main_module.engine = target_engine
        try:
            main_module._migrate_tables()
        finally:
            main_module.engine = original_engine
            target_engine.dispose()

        # Verify directly via sqlite3 (independent of the SQLAlchemy mapping).
        conn = sqlite3.connect(db_path)
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(library_folders)").fetchall()}
        assert "is_incoming" in cols, f"is_incoming column missing: {list(cols)}"
        # Existing rows should default to 0 (i.e. not the incoming folder).
        rows = conn.execute(
            "SELECT path, is_incoming FROM library_folders"
        ).fetchall()
        conn.close()
        assert rows == [("C:/old/library", 0)]


def test_migrate_tables_is_idempotent():
    """Running the migration twice on an already-up-to-date schema is a no-op."""
    from backend import main as main_module

    # Run it twice against the test-suite's normal DB; second call must not raise.
    main_module._migrate_tables()
    main_module._migrate_tables()
