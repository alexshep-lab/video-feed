"""Encoder selection and FFmpeg argument builders.

Centralizes the choice between CPU (libx264) and NVIDIA NVENC (h264_nvenc).
Used by both the HLS transcoder and the compressor / WMV converter so the
encoder choice is consistent across the project.

Selection is driven by `settings.encoder_mode`:
    "auto"  — use NVENC if it appears in `ffmpeg -encoders`, else CPU
    "cpu"   — always libx264
    "nvenc" — always h264_nvenc (will fail at encode time if unavailable)
"""
from __future__ import annotations

import logging
import subprocess
from functools import lru_cache

from ..config import get_settings

logger = logging.getLogger("videofeed.encoder")


@lru_cache(maxsize=1)
def detect_cuda_decode_available() -> bool:
    """Check whether the local FFmpeg build exposes the 'cuda' hwaccel.

    NVDEC decode is independent of NVENC encode — a build can have one
    without the other. Detected via ``ffmpeg -hwaccels``.
    """
    settings = get_settings()
    try:
        completed = subprocess.run(
            [settings.ffmpeg_binary, "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("CUDA decode detection failed: %s", exc)
        return False

    if completed.returncode != 0:
        return False

    for line in completed.stdout.splitlines():
        if line.strip() == "cuda":
            logger.info("CUDA hardware decode available")
            return True
    return False


def build_hw_decode_args() -> list[str]:
    """Args to insert *before* ``-i`` to enable GPU decode (NVDEC) when available.

    Honors ``settings.encoder_mode``: forced ``cpu`` disables HW decode too,
    forced ``nvenc`` enables it unconditionally, ``auto`` probes once.
    """
    mode = (get_settings().encoder_mode or "auto").lower()
    if mode == "cpu":
        return []
    if mode == "nvenc":
        return ["-hwaccel", "cuda"]
    return ["-hwaccel", "cuda"] if detect_cuda_decode_available() else []


@lru_cache(maxsize=1)
def detect_nvenc_available() -> bool:
    """Check whether the local FFmpeg build exposes h264_nvenc.

    Cached for the process lifetime — if the user installs a new FFmpeg they
    need to restart the server. This avoids spawning ffmpeg on every encode.
    """
    settings = get_settings()
    try:
        completed = subprocess.run(
            [settings.ffmpeg_binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("NVENC detection failed: %s", exc)
        return False

    if completed.returncode != 0:
        return False

    for line in completed.stdout.splitlines():
        # `ffmpeg -encoders` lines look like " V....D h264_nvenc            NVIDIA NVENC H.264 encoder"
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "h264_nvenc":
            logger.info("NVENC detected: h264_nvenc available")
            return True
    return False


# libx264 preset name -> NVENC preset name (rough quality/speed mapping)
_NVENC_PRESET_MAP = {
    "ultrafast": "p1",
    "superfast": "p1",
    "veryfast": "p2",
    "faster": "p2",
    "fast": "p3",
    "medium": "p4",
    "slow": "p5",
    "slower": "p6",
    "veryslow": "p7",
}


def _map_preset_to_nvenc(preset: str | None) -> str:
    if preset is None:
        return "p5"
    if preset.startswith("p") and preset[1:].isdigit():
        return preset  # already nvenc-style
    return _NVENC_PRESET_MAP.get(preset.lower(), "p5")


def get_effective_encoder() -> str:
    """Return 'h264_nvenc' or 'libx264' based on settings + availability."""
    mode = (get_settings().encoder_mode or "auto").lower()
    if mode == "cpu":
        return "libx264"
    if mode == "nvenc":
        return "h264_nvenc"
    # auto
    return "h264_nvenc" if detect_nvenc_available() else "libx264"


def build_quality_video_args(crf_or_cq: int = 22, preset: str | None = None) -> list[str]:
    """Quality-target encode args (used by compressor / converter).

    Maps CRF (libx264) to CQ (NVENC) using the same numeric value, which is
    a reasonable approximation in the 18-28 range.
    """
    encoder = get_effective_encoder()
    if encoder == "h264_nvenc":
        # NVENC presets: p1 (fastest) .. p7 (slowest, best quality). p5/p6 ~ libx264 medium/slow.
        nv_preset = _map_preset_to_nvenc(preset)
        return [
            "-c:v", "h264_nvenc",
            "-preset", nv_preset,
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", str(crf_or_cq),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264",
        "-preset", preset or "slow",
        "-crf", str(crf_or_cq),
        "-pix_fmt", "yuv420p",
    ]


def build_bitrate_video_args(video_bitrate: str, preset: str | None = None) -> list[str]:
    """Constrained-bitrate encode args (used by HLS transcoder ladder)."""
    encoder = get_effective_encoder()
    bufsize = f"{int(video_bitrate.replace('k', '')) * 2}k"
    if encoder == "h264_nvenc":
        nv_preset = _map_preset_to_nvenc(preset)
        return [
            "-c:v", "h264_nvenc",
            "-preset", nv_preset,
            "-rc", "vbr",
            "-b:v", video_bitrate,
            "-maxrate", video_bitrate,
            "-bufsize", bufsize,
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264",
        "-preset", preset or "medium",
        "-crf", "23",
        "-b:v", video_bitrate,
        "-maxrate", video_bitrate,
        "-bufsize", bufsize,
        "-pix_fmt", "yuv420p",
    ]
