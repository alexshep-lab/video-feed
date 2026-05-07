"""Thread-safe shadow set for asyncio.Queue contents.

The worker services (compressor/converter/palette) accept enqueue calls
from FastAPI sync request handlers — these run on threadpool workers,
not on the asyncio event-loop thread. The workers themselves consume
the same queue from the loop thread. Iterating ``asyncio.Queue._queue``
(a ``collections.deque``) from the request thread while the worker is
mutating it via ``get`` / ``put_nowait`` risks
``RuntimeError: deque mutated during iteration``.

Each service keeps a parallel ``set[str]`` of currently-queued video IDs
guarded by a ``threading.Lock``; dedup checks read a snapshot of that set
instead of poking the queue's internal deque.
"""
from __future__ import annotations

import threading


class QueuedIds:
    def __init__(self) -> None:
        self._set: set[str] = set()
        self._lock = threading.Lock()

    def add(self, video_id: str) -> None:
        with self._lock:
            self._set.add(video_id)

    def discard(self, video_id: str) -> None:
        with self._lock:
            self._set.discard(video_id)

    def clear(self) -> None:
        with self._lock:
            self._set.clear()

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._set)
