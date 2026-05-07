"""Windows system-tray icon for the frozen desktop bundle.

Pure-Python wrapper around ``pystray``. Adds two menu items to the tray:

  - ``Open VideoFeed`` (default) - opens the browser at the running server URL.
  - ``Quit``                       - tells the caller to shut the server down
                                     and removes the tray icon.

This module is only imported in the frozen bundle (run.py decides). In source
mode the server still runs in the foreground and Ctrl+C is the natural quit.
"""
from __future__ import annotations

import logging
import webbrowser
from pathlib import Path
from typing import Callable

logger = logging.getLogger("videofeed.tray")


def run_tray(*, url: str, icon_path: Path, on_quit: Callable[[], None]) -> None:
    """Run the tray icon on the calling thread. Blocks until Quit is clicked.

    ``on_quit`` is invoked from the pystray click handler, so it must return
    quickly - kick off the actual shutdown (e.g. ``server.should_exit = True``)
    and let the caller join the server thread afterwards.
    """
    # Imported lazily so source-mode runs (where the bundle's pystray hidden
    # imports aren't on the path) can still import this module if they want.
    from PIL import Image
    import pystray

    try:
        image = Image.open(icon_path)
    except Exception:
        logger.warning("Tray icon at %s failed to load, falling back", icon_path)
        image = _fallback_image()

    def _open(icon: "pystray.Icon", item: object) -> None:
        webbrowser.open(url)

    def _quit(icon: "pystray.Icon", item: object) -> None:
        try:
            on_quit()
        except Exception:
            logger.exception("on_quit handler raised")
        icon.stop()

    icon = pystray.Icon(
        "VideoFeed",
        image,
        title=f"VideoFeed - {url}",
        menu=pystray.Menu(
            pystray.MenuItem("Open VideoFeed", _open, default=True),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    icon.run()


def _fallback_image() -> "Image.Image":  # type: ignore[name-defined]
    """64x64 solid-orange square. Used only when the favicon load fails."""
    from PIL import Image
    return Image.new("RGBA", (64, 64), (255, 122, 24, 255))
