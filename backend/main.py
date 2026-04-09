from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

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
from .services.scanner import scan_library
from .services.transcoder import start_worker, stop_worker


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)

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

    # Start background workers (will idle until jobs are queued)
    start_worker()
    start_compress_worker()
    yield
    await stop_worker()
    await stop_compress_worker()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    return {"status": "ok", "database": database_mode}


@app.get("/{full_path:path}")
def frontend_app(full_path: str):
    if full_path.startswith("api/") or full_path == "health":
        return {"detail": "Not Found"}
    if not Path(STATIC_INDEX).exists():
        return {"detail": "Frontend not built"}
    return FileResponse(STATIC_INDEX)
