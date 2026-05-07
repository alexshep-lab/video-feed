# VideoFeed Pre-Release Full Code Review

Date: 2026-05-07

Scope: full application review before release, not limited to git diff. Reviewed
backend startup, SQLite schema handling, FastAPI routers, file-operation safety,
media pipeline services, frontend playback/review flow, and release checks.

## Summary

The project is in a generally healthy state: backend tests pass, frontend
production build succeeds, and several high-risk areas already have defensive
code, especially HLS path traversal protection and CORS defaults.

Release should wait for a few targeted fixes:

1. Add missing SQLite migration for `library_folders.is_incoming`.
2. Fix watch-time accounting so the frontend sends deltas, not absolute
   playback positions.
3. Import `HTTPException` in the maintenance router so safety checks return
   clean 400s instead of crashing.
4. Keep frontend non-native playback detection in sync with backend conversion
   formats.
5. Decide whether stats overview should exclude soft-deleted rows consistently.

## Findings

### 1. Release Blocker: Old Databases Break On `library_folders.is_incoming`

Severity: High

`LibraryFolder.is_incoming` exists in the model, and routers already read it,
but the startup migration only alters the `videos` table. `create_all()` does
not add missing columns to existing SQLite tables.

Files:

- `backend/models.py`
- `backend/main.py`
- `backend/routers/libraries.py`

Observed behavior:

- A DB created before `is_incoming` existed starts successfully.
- `GET /api/libraries` then crashes with:
  `sqlite3.OperationalError: no such column: library_folders.is_incoming`

Recommendation:

- Add an idempotent SQLite migration for `library_folders`, at minimum:
  `is_incoming BOOLEAN NOT NULL DEFAULT 0`.
- Consider generalizing the inline migration helper so each table has its own
  expected-column list.
- Add a regression test that boots from an old `library_folders` schema.

### 2. Watch-Time Statistics Are Overcounted

Severity: High

The frontend stores `video.currentTime` and sends it every 10 seconds. The
backend treats the received value as an increment and adds it to
`total_watch_time` and the latest `WatchEvent.watch_duration`.

Files:

- `frontend/src/pages/WatchPage.tsx`
- `backend/routers/stats.py`

Impact:

- Watching 10, 20, and 30 seconds sends `10 + 20 + 30`, so 30 seconds becomes
  60 seconds.
- Seeking forward can add a large fake duration.
- Opening a watch page records a view before playback actually starts.

Recommendation:

- Track the last reported playback position in the frontend.
- Send only positive deltas while playback is active.
- Ignore negative deltas caused by seeking backward.
- Optionally move `recordWatchEvent()` from page mount to the first `play`
  event.

### 3. Maintenance Safety Checks Crash Because `HTTPException` Is Missing

Severity: Medium-High

`backend/routers/maintenance.py` raises `HTTPException` in several protective
branches, but imports only `APIRouter`, `Body`, `Depends`, `Query`, and
`Request`.

Files:

- `backend/routers/maintenance.py`

Confirmed affected branches:

- `POST /api/maintenance/compress/archive/purge` when full purge lacks
  confirmation.
- `POST /api/maintenance/missing-files/purge` when `confirm` is false.
- `POST /api/maintenance/short-videos/purge` when `confirm` is false.

Observed behavior:

- Direct calls raise `NameError: name 'HTTPException' is not defined`.

Recommendation:

- Add `HTTPException` to the FastAPI import.
- Add tests for these unconfirmed destructive-operation guards.

### 4. Frontend Non-Native Format Detection Is Narrower Than Backend Conversion

Severity: Medium

The frontend treats only `.wmv` and `.avi` as non-native formats. The backend
conversion service also handles `.flv`, `.mpg`, `.mpeg`, `.asf`, `.mts`,
`.m2ts`, `.ts`, and `.3gp`, plus several unsupported codecs by ffprobe codec
name.

Files:

- `frontend/src/pages/WatchPage.tsx`
- `backend/services/converter.py`

Impact:

- Some unsupported files may be presented as raw playback with `video/mp4`.
- Users may see browser decode errors instead of the expected conversion/HLS
  flow.

Recommendation:

- Expose playability/conversion-needed state from the backend in `VideoDetail`,
  or duplicate the full backend extension list in a shared API response.
- Prefer backend-derived state to avoid future drift.

### 5. Stats Overview Mixes Active And Soft-Deleted Rows

Severity: Medium

Pipeline stats explicitly count active rows, but overview totals and some top
lists query all `Video` rows without excluding `deleted_at`.

Files:

- `backend/routers/stats.py`

Impact:

- `total_videos`, `total_size_bytes`, `total_favorites`, most-viewed lists,
  and favorites can include soft-deleted videos.
- This can disagree with library counts and pipeline cards.

Recommendation:

- Decide whether Stats should be "all historical rows" or "active library".
- For release consistency, active-only is probably clearer.
- Add `Video.deleted_at.is_(None)` to overview and top-list queries if active
  stats are desired.

## Positive Notes

- Backend test suite passes when run with a writable pytest temp directory.
- Frontend production build succeeds.
- HLS streaming path traversal is guarded with `resolve()` and `relative_to()`.
- CORS default is loopback-only, with wildcard credentials disabled.
- Many destructive maintenance operations already have confirmation guards and
  path-boundary checks.
- File operations are generally fail-safe: Recycle Bin is preferred for user
  content, converted/cache artifacts are treated as regenerable, and several
  locked-file flows degrade to soft-delete or retry.

## Verification

Commands run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp\basetemp
npm run build
```

Results:

- Backend: `50 passed`
- Frontend: build succeeded
- Vite warning: main JS bundle is large, around `804 kB` minified and
  `243 kB` gzip. This is not a release blocker, but code-splitting the
  maintenance page would be a reasonable later optimization.

Notes:

- Plain `pytest -q` was not available in PATH.
- Running pytest inside the default temp directory hit a Windows permission
  issue. Using explicit `--basetemp` resolved it.
- Temporary review directories were removed after verification.

## Suggested Fix Order

1. Migration for `library_folders.is_incoming`.
2. `HTTPException` import plus safety-branch tests.
3. Watch-time delta accounting plus first-play view event.
4. Backend-derived `needs_conversion` or synchronized frontend format list.
5. Stats active-row consistency.
6. Optional: Vite code splitting for large maintenance/watch bundles.

