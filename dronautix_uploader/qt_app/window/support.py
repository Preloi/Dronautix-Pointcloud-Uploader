"""Shared helpers of the main window and its mixins (UI-free except make_qt_types)."""

from __future__ import annotations

from types import SimpleNamespace
import dataclasses
import logging
import os
import shutil
import threading

from ..activity_model import (
    ACTION_DELETE as ACTIVITY_ACTION_DELETE,
    ACTION_DOWNLOAD as ACTIVITY_ACTION_DOWNLOAD,
    ACTION_REPLACE as ACTIVITY_ACTION_REPLACE,
    ACTION_UPDATE as ACTIVITY_ACTION_UPDATE,
    ACTION_UPLOAD as ACTIVITY_ACTION_UPLOAD,
)
from ..project_management_actions import (
    ACTION_DELETE,
    ACTION_DOWNLOAD,
    ACTION_REPLACE_ALL_POINTCLOUDS,
    ACTION_REPLACE_SINGLE_POINTCLOUD,
    ACTION_ADD_POINTCLOUDS,
    ACTION_ADD_MODELS,
    ACTION_REMOVE_MODEL,
    ACTION_REMOVE_POINTCLOUD,
)
from dronautix_uploader.core.crs_detection import detect_pointcloud_crs
from ..crs_detection_worker import detect_with_cache

LOGGER = logging.getLogger(__name__)

UPLOAD_TEMP_PREFIX = "dronautix_potree_"
GLB_UPLOAD_STAGING_ROOT_NAME = "dronautix_glb_upload"
GLB_UPLOAD_TEMP_PREFIX = ".glb-upload-"
GLB_UPLOAD_STALE_AGE_SECONDS = 24 * 60 * 60


@dataclasses.dataclass
class _SpatialWarningRequest:
    message: str
    completed: threading.Event = dataclasses.field(default_factory=threading.Event)
    accepted: bool = False


@dataclasses.dataclass
class _CrsRepairRequest:
    message: str
    completed: threading.Event = dataclasses.field(default_factory=threading.Event)
    accepted: bool = False


def _detect_crs_or_none(source_path: str):
    """CRS-Erkennung darf eine Projektaktion nie abbrechen.

    Runs inside the action's worker thread and shares the upload page's cache.
    """

    try:
        return detect_with_cache(source_path, detect_pointcloud_crs)
    except Exception:
        return None


def _activity_action_for_project_action(action_id: str) -> str:
    if action_id in {ACTION_REPLACE_ALL_POINTCLOUDS, ACTION_REPLACE_SINGLE_POINTCLOUD}:
        return ACTIVITY_ACTION_REPLACE
    if action_id in {ACTION_ADD_POINTCLOUDS, ACTION_ADD_MODELS}:
        return ACTIVITY_ACTION_UPLOAD
    if action_id in {ACTION_DELETE, ACTION_REMOVE_POINTCLOUD, ACTION_REMOVE_MODEL}:
        return ACTIVITY_ACTION_DELETE
    if action_id == ACTION_DOWNLOAD:
        return ACTIVITY_ACTION_DOWNLOAD
    return ACTIVITY_ACTION_UPDATE


def resolve_runtime_project_rows(provider, *, fallback_rows=()):
    """Return project-management rows from callable or service-style providers."""

    if provider is None:
        return tuple(fallback_rows)
    if callable(provider):
        return provider()
    return provider.list_projects_for_management()


def _remove_temp_dir_with_retry(path: str, warnings: list[str]) -> None:
    name = os.path.basename(path)
    if not name.startswith((UPLOAD_TEMP_PREFIX, GLB_UPLOAD_TEMP_PREFIX)):
        warning = f"Unbekannten Ordner nicht als Upload-Temp gelöscht: {path}"
        warnings.append(warning)
        LOGGER.warning(warning)
        return
    for _attempt in range(2):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            cleanup_error = error
    warning = f"Temporären Ordner nach erneutem Versuch nicht gelöscht: {path} ({cleanup_error})"
    warnings.append(warning)
    LOGGER.warning(warning)


def make_qt_types(QtCore):
    """QObject helper classes; Qt is imported lazily by the caller."""

    class _MainThreadTaskDispatcher(QtCore.QObject):
        """Marshals worker-thread signals onto the GUI thread.

        Instances are created on the main thread, so connecting the worker's
        cross-thread signals to these slots uses a queued connection and the
        wrapped callbacks run on the GUI thread.
        """

        def __init__(self, on_result, on_error, on_finished):
            super().__init__()
            self._on_result = on_result
            self._on_error = on_error
            self._on_finished = on_finished

        @QtCore.Slot(object)
        def dispatch_result(self, value):
            if self._on_result is not None:
                self._on_result(value)

        @QtCore.Slot(object)
        def dispatch_error(self, error):
            if self._on_error is not None:
                self._on_error(error)

        @QtCore.Slot()
        def dispatch_finished(self):
            if self._on_finished is not None:
                self._on_finished()

    class _SpatialWarningEmitter(QtCore.QObject):
        requested = QtCore.Signal(object)

    class _CrsRepairEmitter(QtCore.QObject):
        requested = QtCore.Signal(object)

    class _UpdateProgressEmitter(QtCore.QObject):
        progressed = QtCore.Signal(object, object)

    return SimpleNamespace(
        MainThreadTaskDispatcher=_MainThreadTaskDispatcher,
        SpatialWarningEmitter=_SpatialWarningEmitter,
        CrsRepairEmitter=_CrsRepairEmitter,
        UpdateProgressEmitter=_UpdateProgressEmitter,
    )
