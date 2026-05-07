# Changelog

All notable changes to **VideoFeed** are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [SemVer](https://semver.org/) with a pre-1.0 suffix while the
surface is still moving.

## [0.4.0] — 2026-05-07

### Added
- **Windows desktop bundle**. PyInstaller `--onedir` spec
  (`videofeed.spec`), build script (`scripts/build_bundle.ps1`), and a
  desktop-shortcut installer (`scripts/install_shortcut.ps1`). Output:
  `dist\VideoFeed\VideoFeed.exe` (~50 MB total bundle), launched via a
  `.lnk` on the Desktop with the project favicon.
- **System tray icon** (pystray) with `Open VideoFeed` and `Quit` menu
  items — primary affordance for stopping the server when running
  windowed (no console). Quit triggers a graceful uvicorn shutdown.
- **Frozen-mode browser auto-open** — `run.py` polls `/health` after
  uvicorn starts and pops the default browser at the bound URL.
- **File logging** when frozen — rotating
  `%LOCALAPPDATA%\VideoFeed\logs\server.log` (2 MB × 3) captures both
  application logs and uvicorn's own access / startup output, since
  `--noconsole` bundles have no usable stdout.

### Changed
- **Default port: 7999 → 47999** (retry walks 47999..48008). The old
  range overlapped with the typical local dev band (3000/5173/8000/8080)
  and collided with whatever else the user was running. The new default
  sits well above that band.
- `run.py` instantiates `uvicorn.Server` explicitly when frozen so the
  tray's Quit handler can flip `should_exit = True`. Source-mode runs
  still call `uvicorn.run()` blocking on the main thread.
- `_resource_root()` and `_install_root()` helpers in `backend/config.py`
  resolve read-only resources via `sys._MEIPASS` (frozen) and the .exe
  parent for an optional user-editable `.env`.

### Fixed
- **Bundle startup crash** — PyInstaller `--noconsole` sets
  `sys.stdout = sys.stderr = None`, so uvicorn's `DefaultFormatter`
  crashed in `__init__` calling `sys.stdout.isatty()`. `run.py` now
  redirects both streams to devnull at the very top of the file when
  frozen, and passes `log_config=None` to `uvicorn.run()` so uvicorn's
  loggers fall through to the root logger (which has the rotating
  file handler).
- `frontend/src/api/client.ts` dev-fallback was checking port 5173 but
  Vite is configured for 3000; `${origin}/api` was returning the wrong
  base in dev runs without `VITE_API_BASE` set.
- `scripts/build_bundle.ps1` no longer trips on PyInstaller's stderr
  INFO output. PyInstaller writes its normal progress lines to stderr,
  and PowerShell 5.1 with `$ErrorActionPreference = "Stop"` was
  promoting those to terminating native-command errors before
  `$LASTEXITCODE` could be checked.

### Packaging notes
- FFmpeg / ffprobe are still **not bundled** — required in PATH;
  `/health` reports their availability so the SPA can show a banner.
- Bundle deps: `pystray==0.19.5` + `Pillow>=11.0.0` (in
  `requirements-dev.txt` since source-mode never imports `backend.tray`).

## [0.3.0] — 2026-05-07

### Added
- **Maintenance UI overhaul** — tile-grid layout for quick tools,
  separate Heavy Operations block, short-video purge widget
  (`N min : NN sec`), inline Recycle action in convert + palette
  candidate lists, local notes pad at the bottom of the page.
- **Brand assets** — VideoFeed logo, favicon set, web manifest
  wired into Layout / `<head>`.
- **Filename tag extraction** recognises more formats; scanner now
  auto-applies known filename tags.
- **`/health` reports** ffmpeg + ffprobe availability and database
  mode (file vs in-memory). Cached via `shutil.which`.
- **`needs_transcode` flag** on `VideoBase` — single source of truth
  for "does this need HLS?", computed from the converter's full
  extension + codec set so the player UI can stop carrying its own
  parallel list.
- **Port-collision retry** in `run.py` — probes 7999..8008, falls back
  to the first free port, writes the chosen one to
  `%LOCALAPPDATA%\VideoFeed\data\port.txt` for a future launcher.
- Regression tests for the maintenance confirm-gates and for
  schema migrations on old `library_folders` databases.

### Fixed
- **Scanner**: rows whose file disappeared transiently (network share
  hiccup, brief stat error) are now resurrected on the next scan
  instead of staying buried forever — the existing-file branches
  reset `deleted_at = NULL` when the file is found again.
- **Schema migration**: `library_folders.is_incoming` was added to the
  model but not the inline ALTER-TABLE helper, so old DBs booted
  fine and crashed `GET /api/libraries` with "no such column".
  `_migrate_tables` is now driven by a per-table dict.
- **Watch-time stats**: WatchPage was sending the absolute
  `currentTime` to `/update-watch-time` every 10s, which the server
  added as an increment — 30s of viewing accumulated to 60s. Now
  sends per-tick deltas with seek-forward / seek-backward guards.
- **Stats overview**: `total_videos`, `total_size_bytes`,
  `total_favorites`, `total_views`, `total_watch_time` and the
  most-viewed / most-watched / favorites top lists now exclude
  soft-deleted rows. Numbers match the library page.
- **Maintenance confirm-gates**: the `confirm: true` body field on
  `compress/archive/purge`, `missing-files/purge`, and
  `short-videos/purge` was guarding correctly on the success path
  but `NameError`-ing on the unhappy path because `HTTPException`
  wasn't imported. Imported + tested.
- **Watch player teardown** on every PATCH (favorite toggle, tag
  save) — the HLS attach effect depended on the entire `video`
  object, so any state refresh threw away playback position. Now
  pinned to `[playerMode, video?.hls_stream_url, video?.raw_stream_url,
  reviewMode]`.
- **Watch error handling**: `toggleFavorite` / `toggleConfirm` /
  `saveTags` were swallowing PATCH failures. `saveTags` left
  `saving=true` forever on rejection. Errors now surface in red text
  under the tags input; `setSaving(false)` runs in `finally`.
- **Worker queue dedup** in compressor / converter / palette /
  transcoder no longer iterates `asyncio.Queue._queue` from FastAPI
  threadpool handlers (race vs the worker). New `QueuedIds` helper
  (set + `threading.Lock`) keeps a parallel snapshot.
- **Database fallback**: in-memory SQLite fallback now logs a loud
  `ERROR` with the underlying OSError, so a misconfigured install
  doesn't silently lose every scan on restart.
- **Path normalization**: `compressor._compress_video` and
  `replace_converted_originals` write `str(path.resolve())` to
  match the scanner's `existing_by_path` lookup keys. Avoids
  duplicate rows or moved-video heuristic firing on subsequent scans.
- **Layout `/api/version`** now goes through `API_BASE` so it works
  under the Vite dev server and any future packaged origin layout.
- **Transcoder enqueue** dedups against in-flight + queued IDs;
  back-to-back "transcode all" clicks no longer create N copies.

### Security
- `POST /libraries` no longer recursively `rglob`s the supplied path
  before responding. Bounded to one-level `iterdir`, capped at 1000
  entries, with permission-error tolerance per child.
- `confirm: true` body gate on the three unscoped bulk-delete
  endpoints (compress archive purge, missing-files purge,
  short-videos purge). Targeted purges with explicit `paths` /
  `older_than_days` skip the gate.

### Packaging
- **PyInstaller-aware resource resolution** — `_resource_root()`
  uses `sys._MEIPASS` in a frozen bundle, repo root in source,
  for read-only assets (frontend_static). `_install_root()` uses
  the directory of `sys.executable` for an optional user-editable
  `.env` next to the exe.
- **No more console-window flicker** in `--noconsole` Windows
  bundles — every ffmpeg / ffprobe call now passes
  `creationflags=CREATE_NO_WINDOW` via a shared
  `HIDDEN_SUBPROCESS_KWARGS` constant.
- **Vite outDir** is now `../frontend_static` with `emptyOutDir`,
  so `npm run build` is one step and the manual `xcopy` is gone.

### Removed
- Dead `Video.tags` string column from the model, the response
  schema, the response mapping, and the TS type. Existing rows had
  it as `NULL` always (never written). Underlying SQLite column
  lingers in old DBs; SQLAlchemy ignores it.
- Unused `frontend/src/components/SearchBar.tsx` and
  `VideoPlayer.tsx` — both had zero importers.

### Performance
- HomePage memoizes `activeTagSet` and the filtered tag sidebar so a
  fresh `Set` isn't passed to every `VideoCard` on every keystroke.

## [0.2.0] — 2026-04-21

### Added
- Exposed `/api/version` + UI footer with build version and release date.
- First public-release baseline: LICENSE, CHANGELOG, SECURITY notice.
- Pytest suite (`tests/`) covering tag normalization, HTTP Range parsing,
  CORS defaults, and path-traversal regression for `/stream/{id}/hls/...`.
- Configurable CORS via `VIDEOFEED_CORS_ORIGINS_RAW` (semicolon-separated).

### Security
- **Path traversal fix** in `/api/stream/{id}/hls/{path}` — the handler
  now resolves the target under the HLS dir and rejects anything that
  escapes via `../` or absolute paths.
- **CORS hardening** — the old `allow_origins=["*"] + allow_credentials=true`
  combination is gone. Default is a loopback whitelist; wildcards can be
  opted into via env but credentials are then auto-disabled.
- `ALTER TABLE` migration now validates column identifiers against a
  `^[A-Za-z_][A-Za-z0-9_]*$` regex before interpolation.
- Unknown `/api/...` routes now return HTTP 404 instead of silently
  falling through to the SPA shell with 200.

### Changed
- Filename-based tag extraction (studio prefix + folder abbreviation
  expansion + `&`-joined actor names).
- Tag dedup with fingerprint + fuzzy clusters via `difflib`.
- Tag normalizer with service-folder blacklist and post-migration
  scanner idempotence.
- Compression candidate filter is now orientation-aware (shorter side).

## [0.1.0] — 2026-04-08

Initial scaffold: FastAPI + React/Vite, SQLite index, ffmpeg thumbnails /
HLS, multi-folder scan, library grid, watch page, maintenance page.
