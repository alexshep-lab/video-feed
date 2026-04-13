# VideoFeed: Implemented Changes

This document summarizes the changes already implemented. Sorted roughly from
oldest feature to newest. The earlier playback / scanning / compression /
duplicates foundation sits at the top; the recent batch media pipeline
(NVENC, WMV conversion, review mode, palette generation) is at the bottom.

## 1. Playback and Streaming

- Fixed raw MP4 playback in the browser.
- Removed the incorrect hardcoded frontend API base pointing to `127.0.0.1`.
- Raw video streaming now:
  - returns proper MIME type based on file extension
  - handles `Range` requests more correctly
  - supports suffix ranges
  - clamps invalid range ends to file size
- Native browser-playable formats now prefer direct raw playback instead of unnecessary transcoding.
- Watch page now starts playback automatically when a video is opened.

Relevant files:

- `backend/routers/streaming.py`
- `frontend/src/api/client.ts`
- `frontend/src/pages/WatchPage.tsx`

## 2. Watch Page

- Restored proper raw stream URL delivery to the watch page.
- Watch page now shows absolute file path instead of only library-relative path.
- Added clearer player-state handling for:
  - raw playback
  - HLS playback
  - transcoding in progress
  - transcoding failure
- Added frame palette section below the video.
- For unconfirmed videos the palette is visible immediately.
- For confirmed videos the palette is hidden under a spoiler block.
- Confirmed button on the watch page is now green.

Relevant files:

- `backend/schemas.py`
- `backend/routers/videos.py`
- `frontend/src/pages/WatchPage.tsx`

## 3. Home Page and Library UX

- Replaced classic pagination with infinite scroll on the main library page.
- Added a tile size slider for adjusting card size.
- Restored hover preview autoplay on video cards.
- Fixed preview frame URL generation for card hover previews.
- Added full path display on cards where needed.
- Added extra metadata display support on cards.

Relevant files:

- `frontend/src/pages/HomePage.tsx`
- `frontend/src/components/VideoCard.tsx`
- `frontend/src/components/VideoGrid.tsx`

## 4. Unconfirmed / Review Flow

- Unconfirmed mode uses the same filters and search inputs as the library.
- Review mode now supports reviewing from one place.
- Review page was extended to support both confirmed and unconfirmed videos in one flow.
- Confirmation can be toggled directly from the review list.

Relevant files:

- `frontend/src/pages/HomePage.tsx`

## 5. Thumbnails, Preview Frames, and Contact Sheets

- Thumbnail generation was made more robust.
- New videos now generate thumbnails during scanning instead of only lazily on first request.
- Missing-source thumbnails, preview frames, and contact sheets now return a visual fallback instead of a broken 404 card.
- Contact sheet endpoint is available and used by the watch page as a frame palette.

Relevant files:

- `backend/services/scanner.py`
- `backend/services/thumbnail.py`
- `backend/routers/streaming.py`

## 6. Scanner Improvements

- Added safer Unicode sanitization to avoid scan crashes on broken surrogate characters in filenames or paths.
- Added moved-file reconciliation:
  - if file path changed
  - but filename and size match
  - and old path is missing
  - scanner updates the existing DB record instead of leaving stale entries
- Added cleanup behavior for stale missing-file records under scanned roots.
- Scanner progress endpoint is exposed and used by the frontend.
- When an existing file's content changes (mtime/size differ) and a cached
  conversion exists, scanner invalidates it and re-queues if the new file
  still needs browser-friendly conversion.
- `POST /api/transcode/scan?force_metadata=true` re-runs ffprobe on every
  existing row, useful after a metadata-extraction bug fix.

Relevant files:

- `backend/services/scanner.py`
- `backend/routers/transcode.py`
- `frontend/src/pages/HomePage.tsx`
- `frontend/src/api/client.ts`

## 7. Duplicates

- Maintenance duplicates view was redesigned to use direct cards with more metadata.
- Paths, size, duration, and other metadata are now visible in duplicate cards.
- Old stale entries with missing source files are filtered out from duplicate output.
- Exact duplicates now use a **partial fingerprint**: SHA-1 of
  `file_size + first 64 KB + last 64 KB`. Video container headers fit inside
  that window, so for rows of identical size the probability of false
  positives is negligible, and the query runs in seconds instead of minutes
  on slow network drives.
- Duplicate list is collapsible — 150+ groups no longer murder the DOM.

Relevant files:

- `backend/services/duplicates.py`
- `frontend/src/pages/MaintenancePage.tsx`
- `frontend/src/components/VideoCard.tsx`

## 8. Deletion Modes

- Clarified maintenance deletion behavior.
- Added distinct actions:
  - `Hide` for soft delete
  - `Recycle Bin` for Windows recycle-bin move
- Recycle Bin integration uses Windows Shell API.

Relevant files:

- `backend/services/fileops.py`
- `backend/routers/videos.py`
- `frontend/src/api/client.ts`
- `frontend/src/pages/MaintenancePage.tsx`

## 9. Compression Workflow

- Compression candidate logic was improved.
- Candidate filtering now uses strict rule:
  - `video.height > minHeight`
- Candidates with missing source files are filtered out (ghost rows).
- Added single-item compression queue UI:
  - current filename
  - target filename
  - thumbnail
  - metadata
- Added `Ignore` action for videos that should not be suggested for compression.
- Added bulk compression candidate counts.
- Added compression progress panel:
  - queue size
  - worker state
  - current file
  - current file progress
  - overall batch progress
  - completed count
  - failed count
- Compression output now uses `FHD` suffix in filename.
- Original source file is archived into `L:\Prvt\big` after successful compression.
- Database record is updated to point to the new compressed file.
- **Collision-safe update**: if the compressed output path already belongs to
  another DB row (e.g. a pre-existing FHD copy was scanned as its own entry),
  metadata / tags / view counts are merged into that row and the source row
  is soft-deleted, preventing the `UNIQUE constraint failed` crash.
- After success, thumbnail and contact sheet are invalidated and regenerated
  from the new file content.
- When the compressor encounters a missing source file it soft-deletes the
  row instead of marking it `failed`, so it stops cluttering candidate lists.
- Compression candidate list is collapsible (lazy-fetched when opened).

Relevant files:

- `backend/services/compressor.py`
- `backend/routers/maintenance.py`
- `frontend/src/pages/MaintenancePage.tsx`
- `frontend/src/api/client.ts`
- `backend/config.py`

## 10. Compression Failure Fix

- Fixed a real compressor failure where `ffmpeg` stderr lines could exceed asyncio stream readline limits.
- Compression progress now uses `ffmpeg -progress pipe:2` instead of parsing large human-readable stderr lines.

Relevant files:

- `backend/services/compressor.py`

## 11. Encoder Abstraction — NVENC / NVDEC

A shared encoder module (`backend/services/encoder.py`) now drives encoder
choice for everything (compression, HLS transcoder, WMV/AVI conversion,
palette generation).

- `detect_nvenc_available()` — probes `ffmpeg -encoders` once, cached.
- `detect_cuda_decode_available()` — probes `ffmpeg -hwaccels` once, cached.
- `get_effective_encoder()` — returns `h264_nvenc` or `libx264` based on
  settings and availability.
- `build_quality_video_args(crf_or_cq, preset)` — CRF-style args (for
  compressor / converter).
- `build_bitrate_video_args(video_bitrate, preset)` — bitrate-constrained
  args (for HLS variant ladder).
- `build_hw_decode_args()` — returns `["-hwaccel", "cuda"]` when NVDEC is
  available, else empty.
- libx264 preset names (`slow`, `medium`, …) are mapped to NVENC presets
  (`p5`, `p4`, …) automatically.
- `VIDEOFEED_ENCODER_MODE` env: `auto` (default) / `cpu` / `nvenc`.
- Compressor, transcoder and converter log which encoder was used for each
  job.

## 12. Browser Conversion (WMV / AVI → MP4)

- New `backend/services/converter.py` worker — same queue/lifecycle pattern
  as the compressor.
- New DB fields on `videos`: `convert_status`, `convert_progress`,
  `converted_path`. Inline SQLite migration in `main.py` adds the columns on
  startup if missing.
- Extensions auto-queued: `.wmv`, `.avi`.
- **Remux fast-path**: when the source video stream is already H.264 (e.g.
  H.264-in-AVI), video is `-c:v copy`-ed and only audio is re-encoded to AAC.
  Seconds per file instead of minutes.
- **Full re-encode** for real WMV3 / VC-1 / MPEG-4 ASP / etc., using NVENC
  when available.
- NVDEC hardware decode is used for the full re-encode path, with automatic
  retry on pure CPU decode when NVDEC rejects the codec (Turing NVDEC can't
  handle some older WMV and MPEG-4 ASP variants).
- Original file stays in place. Converted output lands at
  `media/converted/{video_id}.mp4` with `+faststart`.
- The raw streaming endpoint transparently serves the converted file when
  `convert_status == 'completed'`, so playback is instant.
- After successful conversion, thumbnail and contact sheet are invalidated
  and eagerly regenerated from the converted MP4 — the next WatchPage open
  doesn't hang waiting on slow WMV decode.
- Scanner auto-queues new WMV/AVI files. No auto-requeue on startup —
  stuck `processing` rows are reset to `failed` so the user has to
  explicitly kick a batch from the UI.
- Stale `converted_path` is cleaned up when source content changes.
- Candidate endpoint is paginated (SQL `LIMIT/OFFSET`), sortable by
  H.264-first / smallest / largest / name.

Relevant files:

- `backend/services/converter.py`
- `backend/services/encoder.py`
- `backend/services/scanner.py`
- `backend/main.py`
- `backend/models.py`
- `backend/schemas.py`
- `backend/routers/streaming.py`
- `backend/routers/maintenance.py`
- `backend/routers/videos.py`

## 13. Metadata Extraction Robustness

- `ffprobe` stream selection used to pick the first `codec_type == "video"`
  entry, which sometimes hit an embedded cover art / thumbnail image and
  recorded bogus dimensions.
- New selector skips `disposition.attached_pic` streams, drops known image
  codecs (mjpeg / png / gif / bmp / webp / tiff), and picks the video
  stream with the largest pixel area.
- Added debug endpoints for diagnosing specific rows:
  - `GET /api/maintenance/debug/video/{id}` — stored vs fresh ffprobe
  - `POST /api/maintenance/debug/refresh-metadata/{id}` — re-extract one row

Relevant files:

- `backend/services/metadata.py`
- `backend/routers/maintenance.py`

## 14. Hardware-Accelerated Thumbnails and Palettes

- `thumbnail.py` now routes every ffmpeg invocation through
  `_run_ffmpeg_with_hw_fallback`: try `-hwaccel cuda` first, fall back to
  CPU decode on non-zero exit (captures warning + stderr for debugging).
- Contact sheet generation is the biggest winner — its `select` filter
  forces ffmpeg to decode every frame of the whole file to count them. On
  NVDEC this is many times faster.
- `invalidate_video_cache(video_id)` helper drops thumbnail / contact sheet /
  preview frames for a video. Used from compressor and converter whenever
  source content changes.
- Streaming endpoints (`thumbnail`, `contact-sheet`, `preview-frame`) use a
  shared `effective_source_path(video)` helper that prefers the converted
  MP4 over the original WMV/AVI — avoids CPU decode on every first click
  after conversion.

Relevant files:

- `backend/services/thumbnail.py`
- `backend/services/encoder.py`
- `backend/routers/streaming.py`
- `backend/services/compressor.py`
- `backend/services/converter.py`

## 15. Video Palettes Batch Generation

- New `backend/services/palette.py` worker for batch generation of 16-frame
  contact sheets. Same queue / start / stop / status contract as the
  converter.
- Uses the effective playable source (converted MP4 or original) so the
  palette matches what the user watches, and decodes faster when conversion
  exists.
- NVDEC-first with automatic CPU fallback via the shared thumbnail helper.
- Maintenance endpoints:
  - `GET /api/maintenance/palettes/status` — worker state
  - `GET /api/maintenance/palettes/missing-count` — how many rows still lack
    a palette on disk
  - `POST /api/maintenance/palettes/generate-all` — queue every missing one
  - `POST /api/maintenance/palettes/generate/{id}` — queue one
  - `POST /api/maintenance/palettes/stop` — drain queue
- Maintenance UI section with progress bar, missing-count, Stop button,
  auto-poll every 3 s while active.

Relevant files:

- `backend/services/palette.py`
- `backend/routers/maintenance.py`
- `backend/main.py`
- `frontend/src/api/client.ts`
- `frontend/src/pages/MaintenancePage.tsx`

## 16. Review Mode (Auto-Advance)

- New `GET /api/videos/next?after={id}&...` endpoint returns the next video
  matching the same filters as the current listing. Walks a capped window
  of rows, skips up to `after`, then returns the first match that also
  passes any Python-side filters (like the ready-palette check).
- Library HomePage encodes the active filter into the URL when the user
  opens a video from Unconfirmed mode: `?review=1&confirmed=false&ready=...`
- WatchPage detects `review=1`, shows an amber banner at the top, and after
  **Confirm** or **Hard Delete** calls `fetchNextVideo(currentId, filters)`
  and navigates to the next match (preserving the review query string so
  the chain keeps working). Empty result returns to the library.
- New **"Ready to review"** checkbox in Unconfirmed mode filters the list
  to videos that:
  - don't need conversion, or conversion is `completed`; AND
  - have a contact sheet on disk
  This is the set you can actually review right now (palette visible,
  native playback works).
- `VideoCard` takes an optional `linkQuery` prop so HomePage can propagate
  the filter into the card's `to={}` link.

Relevant files:

- `backend/routers/videos.py`
- `backend/services/palette.py` (palette_exists check)
- `frontend/src/api/client.ts`
- `frontend/src/components/VideoCard.tsx`
- `frontend/src/pages/HomePage.tsx`
- `frontend/src/pages/WatchPage.tsx`

## 17. Stop Buttons for Batch Workers

- Compressor, converter and palette workers each expose a `stop_*_all()`
  function:
  - Drains the asyncio queue.
  - Kills the currently-running ffmpeg subprocess (tracked via
    `_current_proc`).
  - Marks the interrupted row as `failed` and recalculates the batch
    `total_jobs` so the progress bar shows a sane stopping point.
  - The worker task itself stays alive for future enqueues.
- Endpoints:
  - `POST /api/maintenance/compress/stop`
  - `POST /api/maintenance/convert/stop`
  - `POST /api/maintenance/palettes/stop`
- Frontend: red **Stop** button appears in each section only while that
  worker is actually busy (queue > 0 or a current job is running).
- `confirm()` dialog before sending the request so it's not too easy to
  fat-finger.

Relevant files:

- `backend/services/compressor.py`
- `backend/services/converter.py`
- `backend/services/palette.py`
- `backend/routers/maintenance.py`
- `frontend/src/api/client.ts`
- `frontend/src/pages/MaintenancePage.tsx`

## 18. Maintenance UI Polish

- All three large lists (duplicates results, compression candidates, convert
  candidates) are now collapsible via a `<SpoilerToggle>` helper. Collapsed
  by default for heavy lists so the page loads light.
- Convert candidates list is paginated (20 per page, prev/next, page
  indicator) and sortable. Default sort = H.264 first so remux-fast-path
  wins surface at the top.
- Convert cards have a selection checkbox for custom batching
  (`Convert Selected`). Selection state survives pagination.
- Compression and convert sections each show the effective encoder badge
  (e.g. "encoder: h264_nvenc (NVENC ready)") so you can see at a glance
  whether hardware acceleration is active.
- Status panels poll only while a worker is active; otherwise they're idle.
- Data fetches for big lists happen lazily when the user expands the
  spoiler — no thumbnail stampede on page load.

Relevant files:

- `frontend/src/pages/MaintenancePage.tsx`
- `frontend/src/api/client.ts`

## 19. Cache-Control for index.html

- FastAPI now serves `frontend_static/index.html` with
  `Cache-Control: no-cache, no-store, must-revalidate`.
- Hashed assets under `/assets/` (content-addressed JS/CSS bundles) keep
  their default cacheability.
- This makes fresh `npm run build` outputs appear on the next browser
  refresh without a hard reload.

Relevant files:

- `backend/main.py`

## 20. Operational Notes

- Frontend changes are built into `frontend/dist/` and copied to
  `frontend_static/`.
- Backend changes require restarting `python run.py`. The running process
  does NOT pick up Python edits until restart, even if the file changed on
  disk.
- After a backend restart that changes route order or adds/removes routes,
  do one hard refresh in the browser to pull fresh `index.html`.
- On RTX 2080 (Turing) NVDEC doesn't decode MPEG-4 ASP (Xvid/DivX), MJPEG
  or several older WMV variants. Ffmpeg for those files falls back to CPU
  decode automatically; NVENC encode still applies.
