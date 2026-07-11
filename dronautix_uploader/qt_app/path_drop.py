"""Qt path drop helpers for file and folder inputs."""

from __future__ import annotations

from collections.abc import Callable, Iterable


def mime_data_paths(mime_data) -> tuple[str, ...]:
    """Return local filesystem paths from a Qt mime data object."""

    if mime_data is None or not _call_bool(mime_data, "hasUrls"):
        return ()

    paths: list[str] = []
    for url in _call_list(mime_data, "urls"):
        if not _call_bool(url, "isLocalFile"):
            continue
        path = str(_call_value(url, "toLocalFile") or "").strip()
        if path:
            paths.append(path)
    return tuple(paths)


def append_unique_paths(
    existing_paths: Iterable[str],
    new_paths: Iterable[str],
    *,
    allow_multiple: bool = True,
) -> tuple[str, ...]:
    cleaned_new = tuple(str(path).strip() for path in new_paths if str(path or "").strip())
    if not cleaned_new:
        return tuple(str(path).strip() for path in existing_paths if str(path or "").strip())
    if not allow_multiple:
        return (cleaned_new[0],)

    merged: list[str] = []
    seen: set[str] = set()
    for path in tuple(existing_paths) + cleaned_new:
        cleaned = str(path).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        merged.append(cleaned)
    return tuple(merged)


def create_path_drop_plain_text_edit(QtWidgets, on_paths_dropped: Callable[[tuple[str, ...]], None]):
    class PathDropPlainTextEdit(QtWidgets.QPlainTextEdit):
        def __init__(self):
            super().__init__()
            self.setAcceptDrops(True)

        def dragEnterEvent(self, event):  # noqa: N802 - Qt override
            if mime_data_paths(event.mimeData()):
                event.acceptProposedAction()
                return
            super().dragEnterEvent(event)

        def dragMoveEvent(self, event):  # noqa: N802 - Qt override
            if mime_data_paths(event.mimeData()):
                event.acceptProposedAction()
                return
            super().dragMoveEvent(event)

        def dropEvent(self, event):  # noqa: N802 - Qt override
            paths = mime_data_paths(event.mimeData())
            if not paths:
                super().dropEvent(event)
                return
            on_paths_dropped(paths)
            event.acceptProposedAction()

    return PathDropPlainTextEdit()


def create_path_drop_line_edit(QtWidgets, on_path_dropped: Callable[[str], None] | None = None):
    class PathDropLineEdit(QtWidgets.QLineEdit):
        def __init__(self):
            super().__init__()
            self.setAcceptDrops(True)

        def dragEnterEvent(self, event):  # noqa: N802 - Qt override
            if mime_data_paths(event.mimeData()):
                event.acceptProposedAction()
                return
            super().dragEnterEvent(event)

        def dragMoveEvent(self, event):  # noqa: N802 - Qt override
            if mime_data_paths(event.mimeData()):
                event.acceptProposedAction()
                return
            super().dragMoveEvent(event)

        def dropEvent(self, event):  # noqa: N802 - Qt override
            paths = mime_data_paths(event.mimeData())
            if not paths:
                super().dropEvent(event)
                return
            path = paths[0]
            if on_path_dropped is None:
                self.setText(path)
            else:
                on_path_dropped(path)
            event.acceptProposedAction()

    return PathDropLineEdit()


def _call_bool(instance, method_name: str) -> bool:
    method = getattr(instance, method_name, None)
    if method is None:
        return False
    return bool(method())


def _call_list(instance, method_name: str) -> tuple:
    method = getattr(instance, method_name, None)
    if method is None:
        return ()
    return tuple(method() or ())


def _call_value(instance, method_name: str):
    method = getattr(instance, method_name, None)
    if method is None:
        return None
    return method()


__all__ = [
    "append_unique_paths",
    "create_path_drop_line_edit",
    "create_path_drop_plain_text_edit",
    "mime_data_paths",
]
