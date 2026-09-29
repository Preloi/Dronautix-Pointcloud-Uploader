"""Activity page: log of operations with filters."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from ..activity_model import (
    ACTION_ALL,
    ACTION_FILTERS,
    ActivityLogEntry,
    ActivityPreview,
    SEVERITY_ALL,
    SEVERITY_FILTERS,
    STATUS_ALL as ACTIVITY_STATUS_ALL,
    STATUS_FILTERS as ACTIVITY_STATUS_FILTERS,
    format_activity_detail,
    format_activity_search_text,
)

ActivityProvider = Callable[[], ActivityPreview | Iterable[ActivityLogEntry]]


def create_activity_page(
    QtCore,
    QtGui,
    QtWidgets,
    *,
    activity_preview: ActivityPreview | None = None,
    activity_provider: ActivityProvider | None = None,
):
    activity_role = QtCore.Qt.UserRole + 10
    action_role = QtCore.Qt.UserRole + 11
    status_role = QtCore.Qt.UserRole + 12
    severity_role = QtCore.Qt.UserRole + 13
    search_role = QtCore.Qt.UserRole + 14

    class ActivityFilterProxy(QtCore.QSortFilterProxyModel):
        def __init__(self):
            super().__init__()
            self._action = ACTION_ALL
            self._status = ACTIVITY_STATUS_ALL
            self._severity = SEVERITY_ALL
            self._query = ""
            self.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)

        def set_query(self, query: str):
            self._query = query.strip().casefold()
            self.invalidateFilter()

        def set_action(self, action: str):
            self._action = action
            self.invalidateFilter()

        def set_status(self, status: str):
            self._status = status
            self.invalidateFilter()

        def set_severity(self, severity: str):
            self._severity = severity
            self.invalidateFilter()

        def filterAcceptsRow(self, source_row: int, source_parent):
            source_model = self.sourceModel()
            first_index = source_model.index(source_row, 0, source_parent)
            if self._action != ACTION_ALL and source_model.data(first_index, action_role) != self._action:
                return False
            if self._status != ACTIVITY_STATUS_ALL and source_model.data(first_index, status_role) != self._status:
                return False
            if self._severity != SEVERITY_ALL and source_model.data(first_index, severity_role) != self._severity:
                return False
            if self._query and self._query not in source_model.data(first_index, search_role).casefold():
                return False
            return True

    preview = _resolve_activity_preview(activity_preview, activity_provider)

    page = QtWidgets.QWidget()
    page.setObjectName("Page")
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(32, 28, 32, 28)
    root.setSpacing(18)

    header = QtWidgets.QHBoxLayout()
    title_box = QtWidgets.QVBoxLayout()
    title = QtWidgets.QLabel("Aktivitäten")
    title.setObjectName("PageTitle")
    subtitle = QtWidgets.QLabel("Protokoll aller Uploads, Änderungen und Fehler dieser Sitzung.")
    subtitle.setObjectName("MutedText")
    title_box.addWidget(title)
    title_box.addWidget(subtitle)
    header.addLayout(title_box, 1)
    refresh_button = QtWidgets.QPushButton("Aktualisieren")
    refresh_button.setObjectName("ActionButton")
    header.addWidget(refresh_button)
    root.addLayout(header)

    summary = preview.status_summary
    status_row = QtWidgets.QHBoxLayout()
    status_row.setSpacing(12)
    activity_stat_labels = {}
    for label, value in (
        ("Gesamt", summary.total),
        ("Läuft", summary.running),
        ("Warnungen", summary.warnings),
        ("Fehler", summary.failed),
        ("Erledigt", summary.completed),
    ):
        card = _create_activity_stat_card(QtWidgets, label, str(value))
        activity_stat_labels[label] = card.findChild(QtWidgets.QLabel, "ActivityStatValue")
        status_row.addWidget(card)
    root.addLayout(status_row)

    toolbar = QtWidgets.QHBoxLayout()
    toolbar.setSpacing(12)

    search = QtWidgets.QLineEdit()
    search.setObjectName("SearchField")
    search.setPlaceholderText("Logs suchen")
    search.setClearButtonEnabled(True)
    toolbar.addWidget(search, 1)

    action_filter = QtWidgets.QComboBox()
    action_filter.setObjectName("StatusFilter")
    action_filter.addItems(list(ACTION_FILTERS))
    toolbar.addWidget(action_filter)

    status_filter = QtWidgets.QComboBox()
    status_filter.setObjectName("StatusFilter")
    status_filter.addItems(list(ACTIVITY_STATUS_FILTERS))
    toolbar.addWidget(status_filter)

    severity_filter = QtWidgets.QComboBox()
    severity_filter.setObjectName("StatusFilter")
    severity_filter.addItems(list(SEVERITY_FILTERS))
    toolbar.addWidget(severity_filter)
    root.addLayout(toolbar)

    content = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
    content.setObjectName("ContentSplitter")
    content.setChildrenCollapsible(False)

    table = QtWidgets.QTableView()
    table.setObjectName("ActivityTable")
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    table.setSortingEnabled(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

    source_model = _create_activity_model(
        QtGui,
        preview.entries,
        activity_role,
        action_role,
        status_role,
        severity_role,
        search_role,
    )
    proxy_model = ActivityFilterProxy()
    proxy_model.setSourceModel(source_model)
    search.textChanged.connect(proxy_model.set_query)
    action_filter.currentTextChanged.connect(proxy_model.set_action)
    status_filter.currentTextChanged.connect(proxy_model.set_status)
    severity_filter.currentTextChanged.connect(proxy_model.set_severity)

    table.setModel(proxy_model)
    table.resizeColumnsToContents()
    table.sortByColumn(0, QtCore.Qt.DescendingOrder)
    content.addWidget(table)

    detail_panel = QtWidgets.QFrame()
    detail_panel.setObjectName("DetailPanel")
    detail_layout = QtWidgets.QVBoxLayout(detail_panel)
    detail_layout.setContentsMargins(20, 20, 20, 20)
    detail_layout.setSpacing(14)

    detail_title = QtWidgets.QLabel("Kein Logeintrag ausgewählt")
    detail_title.setObjectName("PanelTitle")
    detail_badge = QtWidgets.QLabel("Status")
    detail_badge.setObjectName("PreviewBadgeLight")
    detail_text = QtWidgets.QLabel("Wähle einen Eintrag aus, um Aktion, Status, Pfade und Details zu sehen.")
    detail_text.setObjectName("MutedText")
    detail_text.setWordWrap(True)
    detail_text.setTextInteractionFlags(
        QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard
    )
    detail_layout.addWidget(detail_title)
    detail_layout.addWidget(detail_badge, 0, QtCore.Qt.AlignLeft)
    detail_layout.addWidget(detail_text)
    detail_layout.addStretch(1)
    content.addWidget(detail_panel)
    content.setSizes([820, 380])

    root.addWidget(content, 1)

    def selected_activity():
        selection = table.selectionModel().selectedRows()
        if not selection:
            return None
        source_index = proxy_model.mapToSource(selection[0])
        return source_model.data(source_index, activity_role)

    def reload_activity():
        nonlocal preview
        preview = _resolve_activity_preview(activity_preview, activity_provider)
        _populate_activity_model(
            QtGui,
            source_model,
            preview.entries,
            activity_role,
            action_role,
            status_role,
            severity_role,
            search_role,
        )
        _update_activity_summary_labels(activity_stat_labels, preview.status_summary)
        table.resizeColumnsToContents()
        table.sortByColumn(0, QtCore.Qt.DescendingOrder)
        update_detail_panel()

    def update_detail_panel():
        entry = selected_activity()
        if entry is None:
            detail_title.setText("Kein Logeintrag ausgewählt")
            detail_badge.setText("Status")
            detail_text.setText("Wähle einen Eintrag aus, um Aktion, Status, Pfade und Details zu sehen.")
            return

        detail_title.setText(entry.summary)
        detail_badge.setText(f"{entry.status} - {entry.severity}")
        detail_text.setText(format_activity_detail(entry))

    table.selectionModel().selectionChanged.connect(lambda selected, deselected: update_detail_panel())
    refresh_button.clicked.connect(reload_activity)
    proxy_model.modelReset.connect(update_detail_panel)
    proxy_model.rowsRemoved.connect(update_detail_panel)
    update_detail_panel()
    page.reload_activity = reload_activity
    return page


def _resolve_activity_preview(
    activity_preview: ActivityPreview | None = None,
    activity_provider: ActivityProvider | None = None,
) -> ActivityPreview:
    if activity_preview is not None:
        return activity_preview
    if activity_provider is None:
        return ActivityPreview(entries=())
    provided = activity_provider()
    if isinstance(provided, ActivityPreview):
        return provided
    return ActivityPreview(entries=tuple(provided))


def _create_activity_stat_card(QtWidgets, label_text: str, value_text: str):
    card = QtWidgets.QFrame()
    card.setObjectName("ActivityStatCard")
    layout = QtWidgets.QVBoxLayout(card)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(4)

    value = QtWidgets.QLabel(value_text)
    value.setObjectName("ActivityStatValue")
    label = QtWidgets.QLabel(label_text)
    label.setObjectName("MutedText")
    layout.addWidget(value)
    layout.addWidget(label)
    return card


def _create_activity_model(QtGui, entries, activity_role, action_role, status_role, severity_role, search_role):
    model = QtGui.QStandardItemModel(0, 6)
    _populate_activity_model(QtGui, model, entries, activity_role, action_role, status_role, severity_role, search_role)
    return model


def _populate_activity_model(QtGui, model, entries, activity_role, action_role, status_role, severity_role, search_role):
    model.setRowCount(0)
    model.setHorizontalHeaderLabels(["Zeit", "Aktion", "Projekt", "Status", "Severity", "Zusammenfassung"])
    for entry in entries:
        row = (entry.timestamp, entry.action, entry.project, entry.status, entry.severity, entry.summary)
        items = [QtGui.QStandardItem(value) for value in row]
        for item in items:
            item.setEditable(False)
            item.setData(entry, activity_role)
            item.setData(entry.action, action_role)
            item.setData(entry.status, status_role)
            item.setData(entry.severity, severity_role)
            item.setData(format_activity_search_text(entry), search_role)
        model.appendRow(items)


def _update_activity_summary_labels(labels, summary):
    values = {
        "Gesamt": summary.total,
        "Läuft": summary.running,
        "Warnungen": summary.warnings,
        "Fehler": summary.failed,
        "Erledigt": summary.completed,
    }
    for label, value in values.items():
        widget = labels.get(label)
        if widget is not None:
            widget.setText(str(value))
