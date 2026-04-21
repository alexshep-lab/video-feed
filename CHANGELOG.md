# Changelog

All notable changes to **VideoFeed** are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [SemVer](https://semver.org/) with a pre-1.0 suffix while the
surface is still moving.

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
