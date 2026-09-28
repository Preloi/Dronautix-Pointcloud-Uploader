"""CRS detection off the GUI thread (UI-free, no Qt import).

Reading LAS headers or Potree metadata from a NAS can block for seconds. The
detection therefore runs in a *daemon* thread instead of a QThread: a file
access that hangs on a dead network share cannot be cancelled, only let go.
Closing the window never waits for it and never destroys a running thread;
late results are simply dropped by the receiver.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os
import threading
from typing import Any

from dronautix_uploader.core.crs_detection import detect_pointcloud_crs

CrsDetector = Callable[[str], "dict | None"]
ResultCallback = Callable[[str, int, dict], None]

# Files detect_potree_crs() reads, in the order it reads them.
POTREE_METADATA_FILES = ("metadata.json", "cloud.js")


def detection_fingerprint(path: str) -> tuple | None:
    """Identify the bytes a detection depends on; ``None`` disables caching.

    For a Potree folder the folder's own mtime does not change when its
    ``metadata.json`` is rewritten, so the fingerprint uses the metadata file.
    Called in the worker thread: ``stat`` on a NAS can block as well.
    """

    try:
        absolute = os.path.abspath(path)
        if os.path.isdir(absolute):
            for name in POTREE_METADATA_FILES:
                candidate = os.path.join(absolute, name)
                if os.path.isfile(candidate):
                    stat = os.stat(candidate)
                    return ("potree", os.path.normcase(absolute), name, stat.st_size, stat.st_mtime_ns)
            return None
        stat = os.stat(absolute)
        return ("file", os.path.normcase(absolute), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None


class CrsDetectionCache:
    """Thread-safe cache keyed by :func:`detection_fingerprint`."""

    def __init__(self, max_entries: int = 512) -> None:
        self._entries: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def detect(self, path: str, detector: CrsDetector = detect_pointcloud_crs) -> dict:
        fingerprint = detection_fingerprint(path)
        if fingerprint is not None:
            with self._lock:
                cached = self._entries.get(fingerprint)
            if cached is not None:
                return dict(cached)
        try:
            info = detector(path) or {}
        except Exception:
            info = {}
        info = dict(info) if isinstance(info, dict) else {}
        if fingerprint is not None:
            with self._lock:
                if len(self._entries) >= self._max_entries:
                    self._entries.pop(next(iter(self._entries)))
                self._entries[fingerprint] = dict(info)
        return info


SHARED_CRS_CACHE = CrsDetectionCache()


def start_crs_detection(
    requests: Iterable[tuple[str, int]],
    on_result: ResultCallback,
    *,
    detector: CrsDetector = detect_pointcloud_crs,
    cache: CrsDetectionCache = SHARED_CRS_CACHE,
) -> threading.Thread:
    """Detect ``(path, generation)`` requests in a daemon thread.

    ``on_result(path, generation, info)`` is called from the worker thread;
    the Qt side forwards it through a signal to the GUI thread. If the
    receiver is gone (window closed), delivery errors end the worker quietly.
    """

    items = tuple(requests)

    def run() -> None:
        for path, generation in items:
            info = cache.detect(path, detector)
            try:
                on_result(path, generation, info)
            except RuntimeError:
                return  # receiver QObject already deleted

    thread = threading.Thread(target=run, name="crs-detection", daemon=True)
    thread.start()
    return thread


def detect_with_cache(path: str, detector: CrsDetector = detect_pointcloud_crs) -> dict | None:
    """Blocking variant for code that already runs in a worker thread."""

    return SHARED_CRS_CACHE.detect(path, detector) or None


__all__ = [
    "CrsDetectionCache",
    "SHARED_CRS_CACHE",
    "detect_with_cache",
    "detection_fingerprint",
    "start_crs_detection",
]
