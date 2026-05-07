from __future__ import annotations

from functools import lru_cache
import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def _resource_root() -> Path:
    """Where read-only bundled resources (frontend_static, etc.) live.

    In a PyInstaller bundle this is `sys._MEIPASS` (the extraction temp dir);
    in source mode it is the repo root.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _install_root() -> Path:
    """Directory of the running exe (or repo root in source mode).

    Use this for files the user is meant to edit alongside the install —
    chiefly an optional `.env` next to the exe.
    """
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _resource_root()
APPDATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(ROOT_DIR / ".local"))) / "VideoFeed"
# In source: repo/.env. In a frozen bundle: an optional .env next to the
# exe (user-editable, persists across runs); the bundled _MEIPASS copy is
# wiped on every launch and would never see user edits.
ENV_FILE = _install_root() / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIDEOFEED_",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "VideoFeed"
    api_prefix: str = "/api"
    debug: bool = False

    root_dir: Path = ROOT_DIR
    videos_dir: Path = Field(default=ROOT_DIR / "videos")
    library_dirs_raw: str | None = None
    data_dir: Path = Field(default=APPDATA_ROOT / "data")
    media_dir: Path = Field(default=APPDATA_ROOT / "media")
    # Optional override for converted MP4 output (WMV/AVI -> MP4). If unset,
    # defaults to `media_dir / "converted"`. Split out because converted
    # videos can easily run to hundreds of GB and often need to live on a
    # different drive than the other (small) derived assets.
    converted_dir_raw: str | None = None
    database_path: Path = Field(default=APPDATA_ROOT / "data" / "videofeed.db")
    ffprobe_binary: str = "ffprobe"
    ffmpeg_binary: str = "ffmpeg"
    # Where FHD-compression moves the pre-compression originals. Defaults
    # under APPDATA so a fresh install has a valid path; point at a bigger
    # drive via VIDEOFEED_BIG_ARCHIVE_DIR once you start compressing.
    big_archive_dir: Path = Field(default=APPDATA_ROOT / "big")
    # Encoder selection: "auto" (use NVENC if available), "cpu" (force libx264), "nvenc" (force h264_nvenc)
    encoder_mode: str = "auto"
    # CORS: semicolon-separated list of allowed origins. Empty / unset =
    # loopback only (safe default). Set to "*" to allow any origin (only
    # for trusted local networks — the server has no auth).
    cors_origins_raw: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        if not self.cors_origins_raw:
            return [
                "http://localhost:7999",
                "http://127.0.0.1:7999",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        return [o.strip() for o in self.cors_origins_raw.split(";") if o.strip()]

    @property
    def library_dirs(self) -> list[Path]:
        """Fallback library dirs from .env (used during initial bootstrap before DB exists)."""
        if not self.library_dirs_raw:
            return [self.videos_dir]

        normalized = self.library_dirs_raw.replace("\n", os.pathsep)
        candidates = [item.strip() for item in normalized.split(os.pathsep)]
        result: list[Path] = []
        for item in candidates:
            if not item:
                continue
            path = Path(item).expanduser()
            if not path.is_absolute():
                path = (self.root_dir / path).resolve()
            result.append(path)
        return result or [self.videos_dir]

    @property
    def thumbnails_dir(self) -> Path:
        return self.media_dir / "thumbnails"

    @property
    def preview_frames_dir(self) -> Path:
        return self.media_dir / "preview_frames"

    @property
    def hls_dir(self) -> Path:
        return self.media_dir / "hls"

    @property
    def converted_dir(self) -> Path:
        if self.converted_dir_raw:
            return Path(self.converted_dir_raw).expanduser()
        return self.media_dir / "converted"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def static_dir(self) -> Path:
        """Built frontend bundle (read-only, ships with the app)."""
        return ROOT_DIR / "frontend_static"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    for directory in (settings.data_dir, settings.media_dir, settings.hls_dir, settings.converted_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return settings


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
