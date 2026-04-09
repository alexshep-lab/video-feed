# VideoFeed

Local video streaming app for a personal `videos/` library.

## Current Status

Phase 1 scaffold is in place:

- FastAPI backend with SQLite models
- automatic library scan on startup
- `GET /api/videos`
- `GET /api/videos/{id}`
- `POST /api/transcode/scan`
- raw video streaming with `Range` support at `GET /api/stream/{id}/raw`
- a backend-served UI for home and watch pages
- React + Vite frontend scaffold kept in `frontend/` for future development
- thumbnail generation and hover preview frames via `ffmpeg`
- support for scanning multiple library folders, including folders outside the project

## Run

### Current Working App

```powershell
pip install -r backend/requirements.txt
python run.py
```

Open `http://127.0.0.1:8000`.

## External Video Libraries

You can point the app to one or more folders outside the project via `.env`:

```powershell
copy .env.example .env
```

Then edit `.env` and set:

```env
VIDEOFEED_LIBRARY_DIRS_RAW=G:\AlexShep_Labs_Projects\video_feed\videos;D:\MyVideoArchive
```

On Windows, separate folders with `;`.

### Optional Frontend Dev Scaffold

```powershell
cd frontend
npm install
npm run dev
```

The Vite scaffold is included for future frontend work, but in the current environment the working UI is served directly by FastAPI at `http://127.0.0.1:8000`.

## Notes

- The scanner now walks video libraries recursively.
- If `ffprobe` is not installed, videos are still indexed, but metadata like duration and resolution may be empty.
- HLS transcoding is not implemented yet.
- If file-based SQLite is unavailable in the environment, the backend falls back to an in-memory database so the app can still start and scan the library.
- If `ffmpeg` is unavailable, the UI falls back to generated placeholder artwork instead of real preview frames.
