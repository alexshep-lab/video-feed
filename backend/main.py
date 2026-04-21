from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
# Windows/asyncio logs ConnectionResetError when the browser aborts a video
# Range request (seek, navigate away) — harmless, just noisy.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import __release_date__, __version__
from .config import get_settings
from .database import Base, SessionLocal, active_database_url, engine
from .models import LibraryFolder
from .routers import (
    libraries,
    maintenance,
    settings as settings_router,
    stats,
    streaming,
    tags,
    transcode,
    videos,
)
from .services.compressor import start_compress_worker, stop_compress_worker
from .services.converter import start_convert_worker, stop_convert_worker
from .services.palette import start_palette_worker, stop_palette_worker
from .services.scanner import scan_library
from .services.transcoder import start_worker, stop_worker


settings = get_settings()


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _migrate_videos_table() -> None:
    """Add columns introduced after the initial schema. SQLite-only, idempotent.

    SQLAlchemy's ``create_all`` will not modify existing tables, so when we add
    new columns we need to ALTER TABLE manually. We probe the existing schema
    via ``PRAGMA table_info`` and only ADD what is missing.
    """
    expected = [
        ("convert_status", "VARCHAR(32) NOT NULL DEFAULT 'none'"),
        ("convert_progress", "FLOAT NOT NULL DEFAULT 0.0"),
        ("converted_path", "VARCHAR(2048)"),
        ("palette_error", "TEXT"),
        ("palette_failed_at", "DATETIME"),
    ]
    with engine.begin() as connection:
        existing_cols = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(videos)").fetchall()
        }
        for col_name, col_def in expected:
            # Defensive: the pair comes from a hardcoded literal above, but if
            # someone edits this list later a bad identifier shouldn't become
            # an injection vector.
            if not _IDENT_RE.match(col_name):
                raise ValueError(f"Refusing to migrate with invalid identifier: {col_name!r}")
            if col_name not in existing_cols:
                logging.getLogger(__name__).info("Migrating videos table: adding column %s", col_name)
                connection.exec_driver_sql(f"ALTER TABLE videos ADD COLUMN {col_name} {col_def}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    _migrate_videos_table()

    with SessionLocal() as session:
        # Bootstrap: if no LibraryFolder rows exist, seed from config (.env / defaults)
        folder_count = session.scalar(select(LibraryFolder.id).limit(1))
        if folder_count is None:
            for lib_path in settings.library_dirs:
                resolved = str(lib_path.resolve())
                existing = session.scalar(
                    select(LibraryFolder).where(LibraryFolder.path == resolved)
                )
                if not existing:
                    session.add(LibraryFolder(
                        path=resolved,
                        display_name=lib_path.name,
                        enabled=True,
                    ))
            session.commit()

    # Start background workers (will idle until the user explicitly enqueues jobs).
    # We do NOT auto-requeue leftover conversions here — control belongs to the
    # user via the maintenance page / POST /api/maintenance/convert/all.
    start_worker()
    start_compress_worker()
    start_convert_worker()
    start_palette_worker()

    # Reset rows stuck in 'processing' from a previous shutdown so they can be
    # picked up again by a manual /convert/all call (filter is none/pending/failed).
    with SessionLocal() as session:
        from .models import Video as _Video
        from sqlalchemy import select as _select
        stuck = session.scalars(
            _select(_Video).where(
                _Video.deleted_at.is_(None),
                _Video.convert_status == "processing",
            )
        ).all()
        for v in stuck:
            v.convert_status = "failed"
            v.convert_progress = 0.0
        if stuck:
            session.commit()
            logging.getLogger(__name__).info(
                "Reset %d conversion(s) stuck in 'processing' to 'failed'", len(stuck),
            )

    yield
    await stop_worker()
    await stop_compress_worker()
    await stop_convert_worker()
    await stop_palette_worker()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    debug=settings.debug,
    lifespan=lifespan,
)
# CORS: allow_origins="*" + allow_credentials=True is an unsafe combination,
# so we either whitelist specific origins (credentials allowed) or use "*"
# with credentials explicitly disabled. The default origin list covers the
# FastAPI port and the Vite dev server on localhost.
_cors_origins = settings.cors_origins
_wildcard_cors = _cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _wildcard_cors,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(videos.router, prefix=settings.api_prefix)
app.include_router(streaming.router, prefix=settings.api_prefix)
app.include_router(transcode.router, prefix=settings.api_prefix)
app.include_router(settings_router.router, prefix=settings.api_prefix)
app.include_router(tags.router, prefix=settings.api_prefix)
app.include_router(libraries.router, prefix=settings.api_prefix)
app.include_router(stats.router, prefix=settings.api_prefix)
app.include_router(maintenance.router, prefix=settings.api_prefix)

STATIC_DIR = settings.root_dir / "frontend_static"
STATIC_INDEX = STATIC_DIR / "index.html"

_assets_dir = STATIC_DIR / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


@app.get("/health")
def healthcheck() -> dict[str, str]:
    database_mode = "memory" if ":memory:" in active_database_url else "file"
    return {
        "status": "ok",
        "database": database_mode,
        "version": __version__,
        "release_date": __release_date__,
    }


@app.get("/api/version")
def version_endpoint() -> dict[str, str]:
    return {
        "version": __version__,
        "release_date": __release_date__,
        "name": settings.app_name,
    }


@app.get("/{full_path:path}")
def frontend_app(full_path: str):
    # Unknown api/health paths must 404 — don't shadow them with the SPA
    # index.html, which would break clients (and mask routing typos).
    if full_path.startswith("api/") or full_path == "health":
        raise HTTPException(status_code=404, detail="Not Found")
    if not Path(STATIC_INDEX).exists():
        return {"detail": "Frontend not built"}
    # index.html must never be cached: it's the only file that points at the
    # current content-hashed asset bundles. If the browser keeps a stale copy,
    # users get the old UI even after a fresh `npm run build`. The hashed
    # assets under /assets/ can still be cached forever — their filenames change
    # whenever their content changes.
    return FileResponse(
        STATIC_INDEX,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
