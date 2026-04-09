# VideoFeed: Implemented Changes

This document summarizes the changes already implemented during the recent debugging and improvement cycle.

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

Relevant files:

- `backend/services/scanner.py`
- `backend/routers/transcode.py`
- `frontend/src/pages/HomePage.tsx`
- `frontend/src/api/client.ts`

## 7. Duplicates

- Maintenance duplicates view was redesigned to use direct cards with more metadata.
- Paths, size, duration, and other metadata are now visible in duplicate cards.
- Old stale entries with missing source files are filtered out from duplicate output.
- Exact duplicates now use file-content SHA-1 grouping instead of weak size-plus-duration grouping.

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

## 11. Known Current Behavior

- Frame palette generation is lazy right now.
- It is generated on first request to:
  - `/api/stream/{video_id}/contact-sheet`
- If the palette area is not rendered or not opened, the sheet may not yet exist on disk.

## 12. Operational Notes

- Frontend changes are built into `frontend_static`.
- Backend changes require restarting `python run.py`.
- If browser behavior looks stale after changes, use a hard refresh.

