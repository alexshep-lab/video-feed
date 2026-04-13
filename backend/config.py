from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
APPDATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(ROOT_DIR / ".local"))) / "VideoFeed"
ENV_FILE = ROOT_DIR / ".env"


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
    database_path: Path = Field(default=APPDATA_ROOT / "data" / "videofeed.db")
    ffprobe_binary: str = "ffprobe"
    ffmpeg_binary: str = "ffmpeg"
    big_archive_dir: Path = Field(default=Path(r"L:\Prvt\big"))
    # Encoder selection: "auto" (use NVENC if available), "cpu" (force libx264), "nvenc" (force h264_nvenc)
    encoder_mode: str = "auto"

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
        return self.media_dir / "converted"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


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
