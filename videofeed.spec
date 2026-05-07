# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the VideoFeed desktop bundle.

Build:
    pyinstaller --noconfirm videofeed.spec

Output: dist/VideoFeed/VideoFeed.exe + supporting DLLs + frontend_static/
in --onedir layout. Launch via the .exe directly or the desktop shortcut
created by scripts/install_shortcut.ps1.

FFmpeg is intentionally NOT bundled — the app calls `ffmpeg`/`ffprobe`
through PATH, and /health surfaces availability so the SPA shows a banner
when they're missing. Bundling adds ~80MB and the user only runs this on
their own machine where ffmpeg is already installed.
"""
from pathlib import Path

# Paths are evaluated relative to the spec file's location at build time.
PROJECT_ROOT = Path(SPECPATH).resolve()
FRONTEND_STATIC = PROJECT_ROOT / "frontend_static"
ICON = PROJECT_ROOT / "frontend" / "public" / "assets" / "logo" / "favicon.ico"

# --- Analysis -----------------------------------------------------------------

a = Analysis(
    ["run.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # Built SPA — backend/main.py mounts /assets from frontend_static/assets
        # and serves index.html as the SPA shell. Must exist at the install root
        # alongside the exe (sys._MEIPASS in --onedir == the dist dir).
        (str(FRONTEND_STATIC), "frontend_static"),
    ],
    hiddenimports=[
        # SQLAlchemy resolves dialects via entry points — PyInstaller's static
        # analysis doesn't see those, so the SQLite dialect must be hinted.
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.sqlite.pysqlite",
        # uvicorn[standard] pulls websockets/httptools/watchfiles via lazy
        # imports; --collect-submodules below catches the rest.
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # pystray picks its backend at runtime via __import__("pystray._" + os);
        # PyInstaller can't see that. Force the Win32 backend explicitly.
        "pystray._win32",
        # Pillow plugins for the formats our tray icon might come in as
        # (favicon.ico is the default; PNG fallback exists). Without these
        # hints, Image.open works but plugin auto-discovery silently no-ops.
        "PIL.IcoImagePlugin",
        "PIL.PngImagePlugin",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Hard exclude tk/tkinter — uvicorn doesn't need it and PyInstaller
        # can otherwise bundle ~30MB of unused DLLs.
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# --- Executable --------------------------------------------------------------

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoFeed",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX often trips Windows AV / SmartScreen
    console=False,        # --noconsole: no cmd window when launched
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

# --- Bundle (--onedir) -------------------------------------------------------

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VideoFeed",
)
