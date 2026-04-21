from __future__ import annotations

import uvicorn

from backend import __release_date__, __version__


def main() -> None:
    print(f"VideoFeed v{__version__} (released {__release_date__})")
    print("Listening on http://127.0.0.1:7999")
    # timeout_graceful_shutdown: cap how long uvicorn waits for in-flight Range
    # streams to finish on Ctrl+C. The browser keeps the raw-stream connection
    # open for the entire video file, so without a cap shutdown hangs until the
    # user closes the tab. 3s is enough for normal API requests to drain.
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=7999,
        reload=False,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
