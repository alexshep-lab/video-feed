# VideoFeed

> Self-hosted home video streaming server. FastAPI backend, React/Vite
> frontend, FFmpeg (with optional NVENC/NVDEC) for everything heavy,
> SQLite as the index.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688)
![React](https://img.shields.io/badge/React-18-61DAFB)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-alpha-orange)
![Version](https://img.shields.io/badge/version-0.2.0-purple)

Latest release: **v0.2.0** — *2026-04-21* · see [CHANGELOG.md](CHANGELOG.md).

> ⚠️ **No auth, trusted-network only.** Read [SECURITY.md](SECURITY.md)
> before exposing the port anywhere.

---

## What it does

A local index over one or more video folders that turns them into a
browsable library with thumbnails, previews, palette contact sheets,
and in-browser playback. Everything runs on your machine — no cloud,
no external calls.

### Highlights

- **Adjustable infinite-scroll grid** with per-card hover previews
  (8 seek-frames) and duration overlay.
- **Rich filters**: full-text search, multi-tag (OR/AND toggle),
  category, library, codec, duration range, orientation, favorite.
- **Sort**: newest · oldest · title · duration · size · most-viewed ·
  last-watched · shuffle.
- **Watch page**: native MP4/MKV/MOV via HTTP `Range`, or HLS
  (`hls.js`) when a variant ladder exists. Hotkeys for everything
  (`F`, `Space`, `←/→`, `↑/↓`). 16-frame contact sheet below.
- **Review flow**: auto-advance through unconfirmed videos —
  Confirm / Hard-Delete jumps to the next match without going back
  to the library.
- **Recommendations** by shared tags + same library + similar
  duration.
- **Multi-folder scan** with move reconciliation (rename + move
  detection) and auto-registration of nested folders.
- **Soft delete, Windows Recycle Bin delete, permanent hard delete**.

### Pipeline & maintenance

The **Maintenance** page bundles every batch job:

- **FHD compression**: downscale >FHD footage to max 1920×1080 H.264 +
  AAC with `+faststart`, originals archived (configurable path). Uses
  NVENC when available, orientation-aware (portrait 1080×1920 doesn't
  get flagged).
- **Browser conversion** (WMV/AVI/FLV/… → MP4) with **fast-path remux**
  for H.264-in-AVI and full re-encode (NVENC + NVDEC, CPU-decode
  fallback) for everything else. Two-worker NVENC saturation on
  Turing-class GPUs.
- **Replace converted originals**: move converted MP4 into library,
  WMV/AVI → Recycle Bin, DB row flattened.
- **Duplicates**: partial-fingerprint (SHA-1 of size + head + tail
  64KB) for exact dups, perceptual-hash for visually-similar dups.
- **Contact sheets**: 16-frame palette per video via multi-seek xstack
  (single-frame fallback for corrupted NAL streams).
- **Tag normalization**: strip count suffixes `(66)`, site suffixes
  `.com`, screen-pack tails `_scr`, merge duplicates, drop service
  folders (`screens`, `incoming`, …).
- **Tag dedup**: fingerprint clusters + fuzzy `difflib` clusters
  (cutoff 0.88), editable canonical per cluster.
- **Filename tag extraction**: `stunningmatures_g603_Emilia&Arthur`
  → `stunningmatures`, `emilia`, `arthur`; folder-based abbreviation
  expansion (`stm` in `StunningMatures/` → `stunningmatures`).
- **Screenshot-folder cleanup**: bulk-recycle `Screens/`,
  `_SCREENSHOTS/`, `*_scr/` directories inside registered libraries.
- **Orphans / missing files / short-video purge**: safe Recycle Bin
  deletes with NULL/locked-file skips.

### Stats

Totals, most-viewed, watch-time, daily-activity charts, favorites,
popular tags, and a **Collection Pipeline** panel: confirmed vs
unconfirmed, ready-to-review count, palette coverage, conversion
queue breakdown.

---

## Quick start

```powershell
# 1. Clone
git clone https://github.com/alexshep-lab/video-feed.git
cd video-feed

# 2. Install backend deps (Python 3.11+)
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt

# 3. Point at your library folders
copy .env.example .env
# Edit .env — semicolon-separated paths on Windows

# 4. (Optional) Build frontend — a prebuilt bundle lives in frontend_static/
#    when you pull a release; rebuild only if you changed the React code.
#    `npm run build` writes directly into ../frontend_static (configured in
#    vite.config.ts), so no extra copy step is needed.
cd frontend
npm install
npm run build
cd ..

# 5. Run
python run.py
```

Open <http://127.0.0.1:7999>.

### Prerequisites

- **Python 3.11+** (tested on 3.14).
- **FFmpeg + ffprobe** on `PATH`. Without them, videos still index
  (metadata will be empty) but thumbnails / palettes / HLS won't
  render — a generated SVG placeholder is served instead.
- **Node 18+** if you want to rebuild the frontend.
- **Optional: NVIDIA GPU** with a driver supporting NVENC/NVDEC for
  hardware transcode. Detected automatically.

### Configuration

All via environment variables (or `.env` at the project root).

| Variable | Default | Notes |
|---|---|---|
| `VIDEOFEED_LIBRARY_DIRS_RAW` | `./videos` | Semicolon-separated absolute paths to scan. |
| `VIDEOFEED_CONVERTED_DIR_RAW` | `<media>/converted` | Where WMV/AVI→MP4 output lands. Put on a big drive. |
| `VIDEOFEED_ENCODER_MODE` | `auto` | `auto` \| `cpu` \| `nvenc`. |
| `VIDEOFEED_CORS_ORIGINS_RAW` | loopback | Semicolon-separated. `*` → wildcard (credentials off). |
| `VIDEOFEED_DEBUG` | `false` | FastAPI debug mode. |

### Run the tests

```powershell
pip install -r backend/requirements-dev.txt
python -m pytest tests/ -v
```

---

## Architecture

```
video_feed/
├── backend/
│   ├── main.py            # FastAPI app + lifespan + SQLite migration
│   ├── config.py          # Pydantic settings (env-driven)
│   ├── database.py        # SQLAlchemy engine + WAL mode + in-mem fallback
│   ├── models.py          # Video, Tag, LibraryFolder, WatchProgress, WatchEvent
│   ├── routers/           # videos, streaming, transcode, tags, libraries,
│   │                      # settings, stats, maintenance
│   └── services/
│       ├── scanner.py     # library walk + move reconciliation
│       ├── metadata.py    # ffprobe wrapper
│       ├── thumbnail.py   # thumbnail / contact sheet / preview frames
│       ├── transcoder.py  # HLS variant ladder worker
│       ├── compressor.py  # FHD downscale worker (merge-on-collision)
│       ├── converter.py   # WMV/AVI → MP4 (NVENC + remux fast-path)
│       ├── palette.py     # contact-sheet batch worker
│       ├── duplicates.py  # partial-fingerprint + phash
│       ├── encoder.py     # NVENC/NVDEC detection + ffmpeg args
│       ├── fileops.py     # Windows Recycle Bin integration
│       ├── tag_normalize.py  # service-folder blacklist + canonicalization
│       ├── tag_dedup.py      # fingerprint + fuzzy clustering
│       ├── tag_extract.py    # filename → tag extraction
│       └── screen_cleanup.py # screenshot-folder recycle
├── frontend/              # Vite + React + Tailwind SPA
├── frontend_static/       # built SPA (FastAPI serves it)
├── tests/                 # pytest suite
├── docs/                  # design / changelog notes
├── data/                  # SQLite DB (default %LOCALAPPDATA%\VideoFeed\data)
├── media/                 # thumbnails, contact_sheets, hls, converted
└── run.py
```

## API reference

Prefix `/api`. See `backend/routers/` for exhaustive parameters.

| Endpoint | Notes |
|---|---|
| `GET /videos` | List with all filters; repeatable `tags=` with `tag_mode=any`/`all`. |
| `GET /videos/count` · `/next?after={id}` · `/random` · `/filters` | Counts, auto-advance, random, filter dict. |
| `GET /videos/{id}` · `PATCH` · `DELETE` | Detail, inline edit, soft/hard/recycle delete. |
| `GET /videos/{id}/recommendations` | Shared tags + library + duration scoring. |
| `POST /videos/{id}/move` · `/restore` · `POST /videos/bulk-action` | Move, undelete, bulk ops. |
| `GET /stream/{id}/raw` · `hls/...` · `thumbnail` · `contact-sheet` · `preview-frame/{i}` | Streaming endpoints. |
| `POST /transcode/scan` · `GET /transcode/scan/progress` | Rescan libraries (optional `force_metadata`). |
| `POST /transcode/start/{id}` · `start-all` · `queue` | HLS transcoding. |
| `GET /maintenance/duplicates/exact` · `perceptual` · `POST .../compute-hashes` | Duplicate detection. |
| `POST /maintenance/compress/{...}` · `GET .../status` · `.../archive` · `POST .../archive/purge` | FHD compression. |
| `POST /maintenance/convert/{all,queue,stop,{id}}` · `GET .../status` · `.../candidates` · `.../converted-originals` · `POST .../replace` | WMV/AVI → MP4. |
| `POST /maintenance/palettes/{generate-all,generate/{id},stop}` · `GET .../status` · `.../missing-count` | Contact sheets. |
| `GET /maintenance/orphans` · `/missing-files` · `/short-videos` + `POST .../purge` | Cleanup. |
| `GET /maintenance/tags/normalize-preview` · `POST .../normalize` | Tag normalization. |
| `GET /maintenance/tags/similar` · `POST .../tags/merge` | Fuzzy dedup. |
| `GET /maintenance/tags/extract-preview` · `POST .../tags/extract` | Filename tag extraction. |
| `GET /maintenance/library/screen-folders` · `POST .../purge` | Screenshot folder cleanup. |
| `GET /maintenance/encoder` | Active encoder + NVENC availability. |
| `GET /api/version` · `GET /health` | Version info + liveness. |
| `GET /stats` | Overview + pipeline stats. |

## Notes

- Scanner reconciles moved files (name + size match) and soft-deletes
  rows whose files disappeared. Files that vanish between listing and
  stat (network-share races) are skipped silently.
- WatchPage hotkeys are bound on `window` (capture phase) so they
  work regardless of which UI element has focus.
- On RTX 2080 (Turing) NVDEC doesn't support MPEG-4 ASP / MJPEG /
  some old WMV — these auto-fall-back to CPU decode (NVENC encode
  still applies).
- If file-based SQLite isn't writable (permissions / read-only FS),
  the backend falls back to an in-memory DB so the app still starts
  — library state is lost on restart in that mode.

## Roadmap

- Docker image + compose file for one-command launch
- Optional HTTP auth layer (basic / token) for LAN deployments
- More theming options in the UI
- Orphan derived-asset cleanup

## License

MIT — see [LICENSE](LICENSE).
