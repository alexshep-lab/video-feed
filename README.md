# VideoFeed

Self-hosted local video streaming service. FastAPI backend + React frontend,
FFmpeg (with optional NVENC/NVDEC acceleration) for all heavy media work,
SQLite for the index.

## Run

```powershell
pip install -r backend/requirements.txt
python run.py
```

Open `http://127.0.0.1:7999` (or whichever port `run.py` exposes).

### Configure library folders

Copy `.env.example` to `.env` and point at your content:

```env
VIDEOFEED_LIBRARY_DIRS_RAW=G:\AlexShep_Labs_Projects\video_feed\videos;D:\MyVideoArchive
```

On Windows, separate folders with `;`. You can also add/remove libraries
at runtime from the UI (Library page).

`VIDEOFEED_CONVERTED_DIR_RAW` optionally overrides where WMV/AVI→MP4
conversion output lands. Defaults to `<media_dir>/converted`, but converted
MP4s can easily grow to hundreds of GB — move them to another drive with:

```env
VIDEOFEED_CONVERTED_DIR_RAW=L:\Prvt\Converted
```

If this path sits inside a registered library folder, the scanner skips it
automatically so your own converted MP4s aren't re-ingested as new videos.

### Optional: encoder mode

`VIDEOFEED_ENCODER_MODE` (`auto` / `cpu` / `nvenc`) picks the video encoder
for compression, HLS transcoding and WMV/AVI conversion:

- `auto` — use NVENC when detected, otherwise `libx264`
- `cpu` — force CPU (`libx264`), also disables NVDEC hardware decode
- `nvenc` — force NVIDIA NVENC unconditionally (errors if unavailable)

NVENC detection is cached — restart the server after installing a new FFmpeg.

### Frontend dev server (optional)

```powershell
cd frontend
npm install
npm run dev
```

For production you build with `npm run build` and copy `frontend/dist/` into
`frontend_static/` — this is what FastAPI serves. `index.html` is sent with
`Cache-Control: no-cache` so fresh builds pick up automatically.

## Features

### Library

- Infinite-scroll grid with adjustable tile size
- Thumbnails, hover preview (8 frames) and duration overlay on each card
- Filters: search, **multi-tag** (type-ahead search, click to toggle; default
  OR across selected tags, toggle to AND when ≥2 tags selected), category,
  library folder, codec, duration, vertical/landscape, favorite
- Sort by newest / oldest / title / duration / size / most viewed / last watched / shuffle
- Multi-folder scanning (`videos/` plus any external paths in `.env`);
  subfolders containing videos are auto-registered and enabled
- Soft delete (hide), Windows Recycle Bin move, or permanent hard delete

### Watch page

- Adaptive player: native MP4/WebM/MKV/MOV stream with HTTP `Range` support,
  or HLS via `hls.js` when a variant ladder exists
- For videos whose source needs conversion (WMV/AVI), the raw endpoint
  automatically serves the browser-friendly MP4 once conversion finishes
- 16-frame contact sheet ("frame palette") below the player
- Hotkey-free: tags editable inline, confirm / favorite / compress / convert
  available from the watch page
- Recommendations based on shared tags + same library + similar duration

### Review mode

Open any video from the Unconfirmed section of the library with review context
encoded in the URL. An amber banner appears at the top of the watch page.
**Confirm** or **Hard Delete** then jumps to the next matching video
automatically — no need to go back to the library between clicks.

A dedicated **Ready to review** checkbox in Unconfirmed mode filters to
videos that are actually playable right now:

- Native container, or conversion already `completed`
- Contact sheet (16-frame palette) already on disk

### Maintenance page

All long-running / batch operations live here. Each section has a status
panel (queue size, worker state, batch progress, per-file progress) and a
**Stop** button that drops the queue and kills the active ffmpeg if one is
running. None of the workers auto-start jobs — everything is user-triggered.

#### Duplicates

Exact duplicates via partial fingerprint (SHA-1 of file size + first 64 KB +
last 64 KB of the file). Fast on slow drives — video container headers fully
fit into that window, so for files of matching size the chance of false
positives is negligible.

Also a perceptual-hash (average-hash of the thumbnail) mode for visually
similar clips.

#### Compression (downscale to FHD)

Videos over a chosen min height get scaled to max 1920×1080, re-encoded to
H.264 + AAC with `+faststart`, and the original is archived to
`L:\Prvt\big\` (configurable). Uses NVENC when available.

- Candidate filter respects file existence (ghost rows are skipped)
- Collision-safe: if the target path already belongs to another DB row
  (e.g. a pre-existing FHD copy next to the 4K source), metadata and tags
  are merged into that row and the source is soft-deleted
- After success, the cached thumbnail / contact sheet for the video are
  invalidated and regenerated so they match the new content

#### Browser conversion (WMV / AVI → MP4)

Non-browser-playable containers are converted to H.264/AAC/MP4.

- **Fast path (remux)** for H.264-in-AVI: video stream is copied, only audio
  re-encoded. Seconds per file instead of minutes.
- **Full re-encode** with NVENC for real WMV3/VC-1/MPEG-4 ASP sources, with
  automatic CPU-decode fallback if NVDEC rejects the codec
- **Two parallel workers** (see `CONCURRENCY` in `converter.py`) saturate
  NVENC on Turing-class GPUs while leaving headroom for HLS/compression;
  remux jobs are GPU-free so they coexist cleanly with encode jobs
- **Encoder preset `p1`** (ultrafast NVENC) — batch path optimized for
  throughput on low-quality WMV sources where visual loss vs p3/p4 is not
  perceptible
- Original WMV/AVI is kept in place — conversion output goes to
  `media/converted/{video_id}.mp4` and `converted_path` is stored on the row
- Scanner auto-queues new WMV/AVI (no auto-queue on startup — jobs picked
  only when the user clicks)
- Candidate list is paginated (20 per page), sortable by H.264-first (remux
  wins surface first), smallest, largest, or name
- Multi-select checkboxes for custom batching ("Convert Selected")
- Hidden under a spoiler so the page stays light for huge libraries

#### Replace converted originals

After a WMV/AVI is successfully converted, the original source sits on disk
next to a much smaller MP4 (in `converted/`). This section batch-replaces
each pair:

- MP4 is moved from `converted/{uuid}.mp4` to the original's library folder
  as `<stem>.mp4` (in-drive rename, instant when same drive)
- Original WMV/AVI goes to the Windows Recycle Bin
- DB row is flattened: `original_path` now points at the in-library MP4,
  `converted_path` is cleared, `convert_status` becomes `skipped`
- If a pre-existing `.mp4` with the same stem is already there (not ours),
  that row is skipped to avoid collisions

Reclaimable-size counter shows how many bytes the WMV/AVI originals occupy
before you commit.

#### Video palettes (contact sheets)

Single-click batch generation of 16-frame contact sheets for videos that
don't yet have one.

- Uses the effective playable source (converted MP4 if available, else
  original) — faster decode and matches what the viewer will actually watch
- NVDEC decode when available, automatic CPU fallback
- Eagerly regenerated from compressor and converter workers after successful
  jobs so the next WatchPage open is instant

#### Encoder info

`GET /api/maintenance/encoder` reports the active video encoder
(`h264_nvenc` vs `libx264`) and whether NVENC is available in the local
FFmpeg build.

### Stats

Overview of total videos, size, duration, favorites, views, watch time,
plus most-viewed / most-watched / recent history / popular tags / favorites /
daily activity charts.

**Collection Pipeline** panel shows progress on preparing the library:
confirmed vs unconfirmed count, ready-to-review count, palette coverage
with a progress bar, and a per-status breakdown of the browser-conversion
queue (pending / processing / completed / failed / skipped / none).
Useful for tracking "how much review work is left" at a glance.

### Cleanup

Two admin endpoints for pruning dead rows without going through the UI:

- `GET /api/maintenance/missing-files` + `POST .../purge` — hard-delete DB
  rows whose source file no longer exists on disk. Derived assets
  (thumbs, palette, converted, preview frames) are cleaned too.
- `GET /api/maintenance/short-videos?max_seconds=420` + `POST .../purge` —
  preview / remove videos shorter than N seconds. Files go to Windows
  Recycle Bin (not `unlink`), rows with `duration IS NULL` are skipped,
  and locked files leave the DB row intact for a later retry.

## API overview

Prefix: `/api`

| Endpoint | Notes |
|---|---|
| `GET /videos` | List with all filters; `ready=true`, repeatable `tags=` with `tag_mode=any` (OR, default) or `tag_mode=all` (AND) |
| `GET /videos/count` | Matching count for the current filters |
| `GET /videos/next?after={id}` | Next video matching same filters — used by review auto-advance |
| `GET /videos/filters` | Available categories / codecs / tags / libraries |
| `GET /videos/random` | Single random non-deleted video |
| `GET /videos/{id}` / `PATCH` / `DELETE` | Detail, inline edit, soft/hard/recycle delete |
| `GET /videos/{id}/recommendations` | Scored by shared tags + library + duration |
| `POST /videos/{id}/move` | Move file to another library folder with tag re-application |
| `POST /videos/{id}/restore` | Undo soft delete |
| `POST /videos/bulk-action` | Bulk confirm/unconfirm/favorite/unfavorite/delete/restore |
| `GET /stream/{id}/raw` | Native streaming with `Range`; prefers converted file |
| `GET /stream/{id}/hls/{path}` | HLS master/variant/segments |
| `GET /stream/{id}/thumbnail` | Single thumbnail JPEG |
| `GET /stream/{id}/preview-frame/{i}` | One of 8 preview frames (for hover) |
| `GET /stream/{id}/contact-sheet` | 16-frame palette |
| `POST /transcode/scan` | Rescan all enabled libraries; `?force_metadata=true` to re-probe every file |
| `GET /transcode/scan/progress` | Live scan progress poll |
| `POST /transcode/start/{id}` / `start-all` / `queue` | HLS transcoding |
| `GET /maintenance/duplicates/exact` / `perceptual` | Find duplicates |
| `POST /maintenance/duplicates/compute-hashes` | Compute perceptual hashes |
| `GET /maintenance/compress/...` | Candidates list, status, stop |
| `POST /maintenance/compress/...` | Single / batch / oversized / stop |
| `GET /maintenance/convert/status` / `candidates` | Convert worker state + paginated list |
| `GET /maintenance/converted-originals` / `POST .../replace` | List + batch-replace: move MP4 into library, Recycle WMV/AVI |
| `POST /maintenance/convert/all` / `{id}` / `queue` / `stop` | Trigger / stop conversion |
| `GET /maintenance/palettes/status` / `missing-count` | Palette worker state |
| `POST /maintenance/palettes/generate-all` / `generate/{id}` / `stop` | Trigger / stop palette generation |
| `GET /maintenance/orphans` / `POST .../retry-all` / `{id}/retry` | Soft-deleted rows whose file is still on disk — retry Recycle Bin move |
| `GET /maintenance/missing-files` / `POST .../purge` | Active rows with no file on disk — hard-delete + clean derived assets |
| `GET /maintenance/short-videos` / `POST .../purge` | Preview / recycle videos with `duration <= max_seconds` (default 420). NULL durations skipped |
| `GET /maintenance/encoder` | Active encoder + NVENC availability |
| `GET /stats` | Overview + pipeline stats (confirmed, palette coverage, convert queue) |
| `GET /maintenance/debug/video/{id}` | Compare DB metadata to a fresh ffprobe |
| `POST /maintenance/debug/refresh-metadata/{id}` | Re-extract metadata for a single row |

## Notes

- Scanner walks libraries recursively, reconciles moved files (same name +
  size), and soft-deletes rows whose files disappeared. Files that vanish
  between directory listing and stat (network-share races, concurrent
  moves) are skipped silently — next scan picks them up.
- Palette generation has a single-frame fallback when multi-seek xstack
  fails on corrupted H.264 NAL streams, so one broken video doesn't block
  the batch.
- Server uses `timeout_graceful_shutdown=3` so Ctrl+C doesn't hang waiting
  for open Range streams from the browser.
- WatchPage keyboard shortcuts are bound globally (capture phase on
  `window`), not on the video element — they work regardless of which UI
  element has focus: `F` fullscreen, `Space` play/pause, `←/→` seek ±5 s,
  `↑/↓` volume ±10 %.
- If `ffprobe` is not installed, videos still get indexed but duration /
  resolution / codec stay empty.
- If file-based SQLite isn't writable, the backend falls back to an
  in-memory database so the app still starts (library state is lost on
  restart in that case).
- If `ffmpeg` is unavailable, thumbnails and palettes fall back to a generated
  SVG placeholder.
- On RTX 2080 (Turing) NVDEC doesn't support MPEG-4 ASP (Xvid/DivX), MJPEG
  or some old WMV variants — these automatically fall back to CPU decode.
  NVENC encode still applies even when decode is on CPU.

## Project structure

```
video_feed/
├── backend/
│   ├── main.py                 # FastAPI app + lifespan + SQLite migration
│   ├── config.py               # Pydantic settings (env-driven)
│   ├── database.py             # SQLAlchemy engine + WAL mode
│   ├── models.py               # ORM: Video, Tag, LibraryFolder, WatchProgress, WatchEvent
│   ├── schemas.py              # Pydantic I/O models
│   ├── routers/
│   │   ├── videos.py           # list / detail / filters / next / bulk / move / delete
│   │   ├── streaming.py        # raw / hls / thumbnail / contact-sheet / preview-frame
│   │   ├── transcode.py        # HLS transcoding + scan
│   │   ├── maintenance.py      # duplicates / compress / convert / palettes / encoder / debug
│   │   ├── libraries.py        # CRUD for library folders
│   │   ├── settings.py, stats.py, tags.py
│   └── services/
│       ├── encoder.py          # NVENC/NVDEC detection + ffmpeg-arg builders
│       ├── metadata.py         # ffprobe wrapper with robust stream selection
│       ├── scanner.py          # library walk + move-reconciliation + auto-queue
│       ├── thumbnail.py        # thumbnail / contact sheet / preview frames (with cache invalidation)
│       ├── transcoder.py       # HLS variant ladder worker
│       ├── compressor.py       # FHD downscale worker (with merge-on-collision)
│       ├── converter.py        # WMV/AVI → MP4 worker (NVENC + remux fast-path)
│       ├── palette.py          # Contact-sheet batch worker
│       ├── duplicates.py       # partial-fingerprint + phash
│       └── fileops.py          # Recycle Bin integration
├── frontend/
│   ├── src/
│   │   ├── api/client.ts       # typed API client
│   │   ├── components/         # VideoCard, Grid, Layout, Player, SearchBar
│   │   └── pages/              # HomePage, WatchPage, StatsPage, MaintenancePage
│   └── vite.config.ts
├── frontend_static/            # built SPA served by FastAPI
├── media/                      # thumbnails, contact_sheets, preview_frames, hls, converted
├── data/                       # SQLite DB (default: %LOCALAPPDATA%\VideoFeed\data\)
└── run.py
```
