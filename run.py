from __future__ import annotations

import socket
import sys

import uvicorn

from backend import __release_date__, __version__
from backend.config import get_settings

DEFAULT_PORT = 7999
PORT_RETRY_RANGE = 10  # try 7999..8008 inclusive


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
    print(f"VideoFeed v{__version__} (released {__release_date__})")
    print(f"Listening on http://{host}:{port}")
    # timeout_graceful_shutdown: cap how long uvicorn waits for in-flight Range
    # streams to finish on Ctrl+C. The browser keeps the raw-stream connection
    # open for the entire video file, so without a cap shutdown hangs until the
    # user closes the tab. 3s is enough for normal API requests to drain.
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
