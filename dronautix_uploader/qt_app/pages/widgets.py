"""Shared widgets and helpers of the pages (drop lists, status pills)."""

from __future__ import annotations

from collections.abc import Callable
from ..dashboard_settings_model import settings_status_action_id, status_level_label
from ..path_drop import mime_data_paths

DROP_LIST_MAX_VISIBLE_ROWS = 8


def _fit_drop_list_height(list_widget, min_height: int, max_visible_rows: int = DROP_LIST_MAX_VISIBLE_ROWS) -> None:
    """Grow a drop list with its entries (up to ``max_visible_rows``), then scroll.

    A fixed height showed only two rows for multi-cloud projects with many
    pointclouds.
    """

    rows = min(list_widget.count(), max_visible_rows)
    content = sum(max(list_widget.sizeHintForRow(row), 0) for row in range(rows))
    content += list_widget.spacing() * 2 * rows
    chrome = list_widget.frameWidth() * 2 + 14  # frame + stylesheet padding
    list_widget.setFixedHeight(max(min_height, content + chrome))


def _create_source_drop_list(
    QtCore,
    QtWidgets,
    on_paths_dropped: Callable[[tuple[str, ...]], None],
    on_delete: Callable[[], None] | None = None,
    placeholder: str = "",
):
    from PySide6 import QtGui

    class SourceDropList(QtWidgets.QListWidget):
        def __init__(self):
            super().__init__()
            self.setAcceptDrops(True)
            self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.placeholder_text = placeholder

        def paintEvent(self, event):  # noqa: N802 - Qt override
            super().paintEvent(event)
            if self.count() or not self.placeholder_text:
                return
            painter = QtGui.QPainter(self.viewport())
            painter.setPen(QtGui.QColor("#7f90ab"))
            painter.drawText(
                self.viewport().rect().adjusted(12, 0, -12, 0),
                QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                self.placeholder_text,
            )
            painter.end()

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
            if paths:
                event.acceptProposedAction()
                on_paths_dropped(paths)
                return
            super().dropEvent(event)

        def keyPressEvent(self, event):  # noqa: N802 - Qt override
            if on_delete is not None and event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
                on_delete()
                event.accept()
                return
            super().keyPressEvent(event)

    return SourceDropList()


def _create_settings_status_panel(QtWidgets, title_text: str, items, on_item_action: Callable[[str], None] | None = None):
    panel = QtWidgets.QFrame()
    panel.setObjectName("DetailPanel")
    layout = QtWidgets.QVBoxLayout(panel)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QtWidgets.QLabel(title_text)
    title.setObjectName("PanelTitle")
    layout.addWidget(title)
    for item in items:
        layout.addWidget(_create_settings_status_item(QtWidgets, item, on_item_action=on_item_action))
    layout.addStretch(1)
    return panel


# Information only: pills must not look like the clickable buttons next to them.
_STATUS_PILL_BY_LEVEL = {
    "ok": "StatusPill",
    "warning": "StatusPillWarning",
    "error": "StatusPillDanger",
    "info": "StatusPillInfo",
}


def _create_settings_status_item(QtWidgets, item, on_item_action: Callable[[str], None] | None = None):
    row = QtWidgets.QFrame()
    row.setObjectName("SettingsStatusRow")
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(12)

    text_box = QtWidgets.QVBoxLayout()
    title = QtWidgets.QLabel(item.name)
    title.setObjectName("SectionTitle")
    detail = QtWidgets.QLabel(f"{item.status} - {item.detail}")
    detail.setObjectName("MutedText")
    detail.setWordWrap(True)
    text_box.addWidget(title)
    text_box.addWidget(detail)
    layout.addLayout(text_box, 1)

    badge = QtWidgets.QLabel(status_level_label(item.level))
    badge.setObjectName(_STATUS_PILL_BY_LEVEL.get(item.level, "StatusPillInfo"))
    badge.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
    layout.addWidget(badge)

    action_id = settings_status_action_id(item) if on_item_action is not None else ""
    if action_id:
        action = QtWidgets.QPushButton(item.action)
        action.setObjectName("ActionButton")
        action.clicked.connect(lambda checked=False, selected_action=action_id: on_item_action(selected_action))
        layout.addWidget(action)
    return row


def _clear_layout_widgets(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
