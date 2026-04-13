# VideoFeed: Useful Next Changes

What's still open, with a rough priority order at the bottom. The items from
the original list that have since shipped (WMV conversion, NVENC/NVDEC,
palette pre-generation, Stop buttons, review mode, compression policy basics)
are no longer here — see `IMPLEMENTED_CHANGES.md`.

## 1. Compression Policy — Smarter Candidate Scoring

Compression currently triggers on raw height > threshold. More useful filters:

- Add a **minimum file size** threshold (skip files below e.g. 50 MB — they
  won't compress meaningfully anyway).
- **Codec-aware**: an already-efficient `h264` file at 1440p isn't always
  worth recompressing.
- **Bitrate-based scoring**: a 2160p clip encoded at 5 Mbps barely saves
  anything; a 1440p clip at 30 Mbps is a great target.
- **Estimated savings** shown before queueing (rough heuristic from source
  bitrate vs target bitrate).

Same idea as what already exists for the convert / remux split (H.264 →
fast path), just extended to compression targets.

## 2. Per-Video Compression Audit Log

For each compression job, store enough to explain later what happened:

- source filename / target filename
- source size / target size
- time spent
- encoder used (libx264 vs h264_nvenc)
- whether HW decode was active
- source archived path
- whether the row was merged into an existing target row

Surface on the watch page ("Compressed on 2026-04-10, 4.2 GB → 1.3 GB via
h264_nvenc") and as a job history page in Maintenance.

## 3. Rebuild / Repair Derived Assets

One-click maintenance actions for each derived asset kind:

- Rebuild missing **thumbnails**
- Rebuild missing **preview frames** (the 8-frame hover set)
- Rebuild missing **contact sheets** ← already shipped as "Generate All
  Missing (N)"; the thumbnail / preview-frame variants are the same pattern
- Rebuild failed / stale **conversions**

Useful after:

- path moves (previously-cached sheets now point at dead sources)
- scanner fixes
- codec pipeline upgrades
- FFmpeg config changes

## 4. Parallel Worker Slots

Single-worker compressor / converter / palette batches leave NVENC and NVDEC
partially idle (especially when mixed with CPU-decoded files where NVDEC
falls back). Adding a configurable concurrency (e.g. 2–3 slots) would:

- push GPU utilization closer to 100% on long batches
- hide CPU-decode latency behind other GPU work
- cut total batch time by ~2× on RTX 2080

Caveats:

- consumer NVENC has a session limit (3 on Turing without patch)
- SQLAlchemy sessions need to stop being shared between workers
- the "current_video_id" + "current_proc" globals become lists

## 5. GPU-Aware Preview Frames / Thumbnail Pipeline

Thumbnails and preview frames already use `-hwaccel cuda` when available.
Further wins:

- `-hwaccel_output_format cuda` keeps frames in GPU memory → only
  `hwdownload` at the very end before JPEG encode.
- `scale_cuda` instead of CPU `scale` before download.

These are more fragile (filter-graph compatibility varies across ffmpeg
builds), so they should be opt-in / benchmarked.

## 6. Richer Review Workspace

Review mode exists and auto-advances. Ideas to make it more keyboard-first:

- Hotkeys: **Y** confirm, **N** hard delete, **←** previous, **→** next,
  **space** toggle play, **F** favorite.
- Quick-tag chips for common categories (one click adds/removes).
- Duplicate-warning badge in review cards ("also matches file X").
- Sort-by-newest-scanned option (catch what just arrived).
- Sort-by-contact-sheet-generated-at (review the most recently readied
  videos first).

## 7. Better Logging and Job Diagnostics

Each media pipeline already logs to stdout. Useful additions:

- Structured per-job log lines (one JSON line per finished job) for later
  aggregation.
- Explicit log of the full ffmpeg command invoked (helps reproduce failures
  by hand).
- Save stderr/stdout of *failed* jobs to `media/logs/{video_id}.txt`.
- Expose `last_error` on the Video row so the maintenance UI can show
  "why did this one fail?" inline.

## 8. Watch-Progress API and "Continue Watching"

The `WatchProgress` model exists but the router is not wired up. Missing:

- `GET /api/progress/{id}` — current position
- `PUT /api/progress/{id}` — save position (called every ~5 s from player)
- `GET /api/progress/continue-watching` — list of partially-watched videos
- HomePage section "Continue watching" at the top when items exist
- WatchPage resume-prompt: "Resume from 14:32?"

## 9. Search via SQLite FTS5

`ILIKE %q%` handles the current library fine but gets slower as the library
grows past ~10k rows and doesn't do prefix/stemming/fuzzy. FTS5 over
`title + original_filename + tags + category` would be a strict upgrade.

## 10. HLS: Prefer Over Raw for Large / Mid-Bitrate Content

Right now HLS is only used when the browser can't natively play the file.
For big 4K H.264 files on slow networks, serving HLS (even just a single
720p/1080p variant) could give better playback. Needs a policy:

- Auto-transcode to HLS if `file_size > X` *and* `width >= 1920`?
- User override: "Play HLS anyway" in the watch page?

## 11. Recommended Priority Order

If doing this in a practical order, the most valuable sequence is:

1. Watch progress API + "Continue watching" (low effort, high UX).
2. Rebuild missing derived assets (makes the library heal itself after
   messy imports).
3. Per-video compression audit log (ties the existing merge/soft-delete
   logic to visible history).
4. Smarter compression policy (avoids wasted CPU/GPU time on negligible
   gains).
5. Review mode hotkeys (speeds up what's already shipping).
6. Parallel worker slots (the big throughput lever; most code reuse).
7. Better logging (pays off once any of the above are in heavy use).
