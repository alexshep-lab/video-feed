# VideoFeed: Useful Next Changes

This document lists the most useful next changes based on the implemented work and the current library profile.

## 1. WMV Playback Strategy

This project has many WMV files. Those files are a poor fit for direct browser playback, so the best next step is to make them playable immediately without waiting for manual action.

Recommended direction:

- Detect WMV files during scan.
- Automatically queue them for background conversion into browser-friendly MP4.
- Store converted output alongside the DB record.
- Prefer converted MP4 immediately on the watch page.
- Keep the original WMV file until the converted file is validated.

Good target:

- video: H.264
- audio: AAC
- container: MP4
- `+faststart` enabled

Expected benefit:

- WMV opens like normal video
- fewer silent playback failures
- no manual transcoding button needed for common legacy formats

## 2. GPU Acceleration for RTX 2080

Right now many heavy media operations are CPU-bound:

- WMV conversion
- HLS/transcode generation
- compression
- some thumbnail / frame generation workflows

With an RTX 2080, the highest-impact improvement is NVIDIA acceleration through FFmpeg.

Recommended work:

- add backend setting for preferred encoder mode:
  - `cpu`
  - `nvidia`
- support FFmpeg NVDEC/NVENC when available
- use:
  - `h264_nvenc` for MP4 output
  - optional CUDA hardware decode when supported by source codec
- keep CPU fallback when GPU path is unavailable

Typical GPU-oriented commands would be based on:

- `-hwaccel cuda`
- `-c:v h264_nvenc`

Important note:

- Some frame extraction operations may still be cheaper or simpler on CPU.
- The biggest wins will come from long-running conversion and compression jobs, not from every single thumbnail call.

## 3. Pre-generate Contact Sheets During Scan

Frame palettes currently generate lazily.

Recommended next step:

- generate contact sheets during scan for:
  - new videos
  - videos missing contact sheet
- optionally skip for confirmed videos if storage is a concern
- optionally generate only for:
  - videos under a size threshold
  - videos under a duration threshold
  - unconfirmed videos

Expected benefit:

- watch page shows palette instantly
- easier moderation and review workflow
- no confusion about whether palette generation happened

## 4. Explicit Media Cache Status

It would help to show media cache state in the UI or logs:

- thumbnail exists / missing
- preview frames ready / partial / missing
- contact sheet ready / missing
- compressed version ready / missing
- HLS ready / missing

Useful additions:

- backend endpoint for asset readiness
- maintenance card for rebuilding missing derived assets
- logs like:
  - `contact-sheet generated`
  - `contact-sheet served from cache`
  - `thumbnail regenerated`

## 5. Rebuild Derived Media Assets

A useful maintenance feature would be:

- `Rebuild missing thumbnails`
- `Rebuild preview frames`
- `Rebuild contact sheets`
- `Rebuild WMV conversions`

This is especially useful after:

- path moves
- scanner fixes
- codec pipeline upgrades
- FFmpeg config changes

## 6. Compression Policy Improvements

Compression now works better, but the policy can be improved further.

Recommended improvements:

- add minimum file size threshold
  - for example skip files smaller than 50 MB or 100 MB
- add codec-aware filtering
  - for example avoid recompressing already efficient H.264 encodes unless they are very large
- add bitrate-based candidate scoring
- add estimated savings before queueing

Expected benefit:

- avoids useless compression of tiny files
- makes queue more meaningful

## 7. Per-Video Compression Audit

For each compression job, it would be useful to store:

- source filename
- target filename
- source size
- target size
- time spent
- encoder used
- whether GPU or CPU was used
- source archived path

This could be shown:

- in maintenance
- on watch page
- in a future job history page

## 8. GPU-aware Thumbnail and Preview Pipeline

Even if not every single image job needs GPU, it may still be worth adding configurable acceleration for:

- preview frame extraction
- contact sheet generation

Recommended approach:

- keep current CPU pipeline as default safe mode
- add optional GPU mode
- benchmark on your system before forcing it globally

Because your system is:

- Intel i7-8700
- 32 GB RAM
- NVIDIA GeForce RTX 2080 8 GB

the GPU path is likely worth it for long-running video tasks.

## 9. Better Review Workspace

Review mode can be expanded into a more dedicated moderation workspace.

Useful additions:

- keyboard shortcuts
  - confirm
  - unconfirm
  - next
  - trash
  - open
- visible contact sheet directly in the card
- quick tags for common categories
- duplicate warnings in review flow
- sort by newest scanned

## 10. Better Logging and Job Diagnostics

Media pipelines are hard to debug without clear logs.

Useful next improvements:

- structured compression logs
- explicit ffmpeg command logging
- log whether GPU path or CPU path was chosen
- save stderr/stdout on failed jobs
- expose last error message in maintenance UI

That would make it much easier to answer:

- did conversion start
- did it use GPU
- did it finish
- if it failed, why

## 11. Recommended Priority Order

If doing this in a practical order, the most valuable sequence is:

1. Auto-convert WMV to MP4 for browser playback.
2. Add GPU/NVENC pipeline for conversion and compression.
3. Pre-generate contact sheets during scan.
4. Add min-size rule for compression candidates.
5. Add rebuild-derived-assets maintenance tools.
6. Add better job diagnostics and failure surfacing.

