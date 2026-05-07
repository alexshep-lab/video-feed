from __future__ import annotations

# A PyInstaller --noconsole Windows bundle has sys.stdout and sys.stderr
# set to None. Anything that calls sys.stdout.isatty() — uvicorn's
# DefaultFormatter, logging.StreamHandler with default stream, even some
# stdlib output paths — crashes with AttributeError before our logging
# config has a chance to run. Redirect both to devnull *first*, before
# any third-party imports touch them. In source mode (sys.stdout = real
# terminal) this block is a no-op.
import os as _os
import sys as _sys
if getattr(_sys, "frozen", False):
    if _sys.stdout is None:
        _sys.stdout = open(_os.devnull, "w", encoding="utf-8", buffering=1)
    if _sys.stderr is None:
        _sys.stderr = open(_os.devnull, "w", encoding="utf-8", buffering=1)
del _os, _sys

import os
import socket
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen

import uvicorn

from backend import __release_date__, __version__
from backend.config import get_settings

DEFAULT_PORT = 47999
PORT_RETRY_RANGE = 10  # try 47999..48008 inclusive
HEALTH_POLL_TIMEOUT_S = 15.0
HEALTH_POLL_INTERVAL_S = 0.25


def _find_open_port(host: str, start: int, attempts: int) -> int | None:
    """Return the first port from `start` that we can bind to.

    Probe is a real bind+close on the same host the server will use, so
    a port held by another process (a previous server instance, an IDE
    proxy, anything) is detected before uvicorn starts and dies loudly.
    """
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    return None


def _write_port_file(port: int) -> None:
    """Persist the bound port so a desktop-launcher can open the right URL.

    Best-effort: if the data dir isn't writable yet, skip silently — the
    launcher can fall back to probing /health on the default port range.
    """
    try:
        settings = get_settings()
        target = settings.data_dir / "port.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(port), encoding="utf-8")
    except OSError:
        pass


def _wait_then_open_browser(url: str) -> None:
    """Poll /health until the server responds, then open the default browser.

    Run on a background thread so it doesn't block uvicorn's startup. Bails
    silently after a timeout — if the server takes longer than that, the user
    can hit the URL manually (we still printed it).
    """
    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT_S
    health_url = f"{url.rstrip('/')}/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=1.0) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        time.sleep(HEALTH_POLL_INTERVAL_S)


def _should_open_browser() -> bool:
    """True when running as a frozen desktop launch — never in dev.

    Source-mode runs (``python run.py`` during dev) shouldn't pop a browser
    on every restart. The frozen bundle is invoked by a desktop shortcut
    where opening the UI is the whole point.
    """
    if getattr(sys, "frozen", False):
        return True
    # Manual override for testing the launcher logic in source.
    return os.environ.get("VIDEOFEED_OPEN_BROWSER") == "1"


def main() -> None:
    host = "127.0.0.1"
    port = _find_open_port(host, DEFAULT_PORT, PORT_RETRY_RANGE)
    if port is None:
        sys.stderr.write(
            f"Could not bind any port in {DEFAULT_PORT}..{DEFAULT_PORT + PORT_RETRY_RANGE - 1}. "
            "Close whatever else is using these ports and retry.\n"
        )
        sys.exit(1)

    if port != DEFAULT_PORT:
        print(f"Port {DEFAULT_PORT} was busy — falling back to {port}.")

    _write_port_file(port)
    url = f"http://{host}:{port}"
    print(f"VideoFeed v{__version__} (released {__release_date__})")
    print(f"Listening on {url}")

    if _should_open_browser():
        threading.Thread(
            target=_wait_then_open_browser,
            args=(url,),
            daemon=True,
        ).start()

    # timeout_graceful_shutdown: cap how long uvicorn waits for in-flight Range
    # streams to finish on Ctrl+C. The browser keeps the raw-stream connection
    # open for the entire video file, so without a cap shutdown hangs until the
    # user closes the tab. 3s is enough for normal API requests to drain.
    #
    # Pass the app object directly (not "backend.main:app") so uvicorn's
    # import-string machinery doesn't run inside the PyInstaller bundle —
    # that codepath is fragile when the package was extracted from _MEIPASS.
    #
    # When frozen: pass log_config=None so uvicorn doesn't run dictConfig
    # against its own LOGGING_CONFIG. That config builds DefaultFormatter,
    # which calls sys.stdout.isatty() during construction — even with our
    # devnull fix above, PyInstaller logging has been a moving target across
    # versions and we'd rather not depend on it. With log_config=None,
    # uvicorn's loggers fall through to the root logger that
    # backend.main._configure_logging() set up (StreamHandler + rotating
    # server.log), so access logs and startup info still land in the file.
    # Source mode keeps uvicorn's default colored output.
    from backend.main import app
    uvicorn_kwargs: dict = {
        "host": host,
        "port": port,
        "reload": False,
        "timeout_graceful_shutdown": 3,
    }
    if getattr(sys, "frozen", False):
        uvicorn_kwargs["log_config"] = None
    uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
