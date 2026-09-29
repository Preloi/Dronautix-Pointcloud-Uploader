"""Project management page: table, detail panel and project actions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import inspect
from ..error_messages import describe_error
from ..task_worker import create_task_worker
from ..project_management import (
    ProjectPreview,
    STATUS_ALL,
    STATUS_FILTERS,
    load_project_previews,
    project_datum_sort_key,
    status_filter_accepts,
)
from ..project_management_actions import (
    ACTION_DELETE,
    ACTION_DISABLE_LINK,
    ACTION_COPY_LINK,
    ACTION_DOWNLOAD,
    ACTION_ENABLE_LINK,
    ACTION_DUPLICATE,
    ACTION_OPEN_LINK,
    ACTION_RENAME,
    ACTION_REPLACE_ALL_POINTCLOUDS,
    ACTION_REPLACE_SINGLE_POINTCLOUD,
    ACTION_REPLACE_SINGLE_MODEL,
    ACTION_REPAIR_CRS_METADATA,
    ACTION_ADD_MODELS,
    ACTION_ADD_POINTCLOUDS,
    ACTION_REMOVE_MODEL,
    ACTION_REMOVE_POINTCLOUD,
    action_by_id,
    is_action_available,
)

PROJECT_SEARCH_DEBOUNCE_MS = 200


ProjectProvider = Callable[[], Iterable[ProjectPreview]]


ProjectActionCallback = Callable[..., None]


def create_projects_page(
    QtCore,
    QtGui,
    QtWidgets,
    on_placeholder_action: Callable[[str], None] | None = None,
    *,
    project_previews: Iterable[ProjectPreview] | None = None,
    project_provider: ProjectProvider | None = None,
    on_project_action: ProjectActionCallback | None = None,
    on_load_state_changed: Callable[[], None] | None = None,
    can_start_load: Callable[[], bool] | None = None,
    empty_state_provider: Callable[[], str] | None = None,
    on_open_settings: Callable[[], None] | None = None,
    on_load_finished: Callable[[bool, str], None] | None = None,
):
    projects = tuple(project_previews or ())
    action_callback = on_project_action or on_placeholder_action
    project_role = QtCore.Qt.UserRole + 1
    disabled_role = QtCore.Qt.UserRole + 2
    pointcloud_role = QtCore.Qt.UserRole + 3
    search_role = QtCore.Qt.UserRole + 4
    sort_role = QtCore.Qt.UserRole + 5
    model_role = QtCore.Qt.UserRole + 6
    date_columns = (4, 5)

    class ProjectsFilterProxy(QtCore.QSortFilterProxyModel):
        def __init__(self):
            super().__init__()
            self._status = STATUS_ALL
            self.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
            self.setFilterRole(search_role)

        def set_status(self, status: str):
            self._status = status
            self.invalidateFilter()

        def filterAcceptsRow(self, source_row: int, source_parent):
            source_model = self.sourceModel()
            status_index = source_model.index(source_row, 3, source_parent)
            disabled = bool(source_model.data(status_index, disabled_role))
            if not status_filter_accepts(disabled, self._status):
                return False
            return super().filterAcceptsRow(source_row, source_parent)

        def lessThan(self, left, right):
            # Date columns are displayed in German DD.MM.YYYY order but must sort
            # chronologically, so compare stable ISO sort keys instead.
            if left.column() in date_columns:
                source_model = self.sourceModel()
                left_key = source_model.data(left, sort_role) or ""
                right_key = source_model.data(right, sort_role) or ""
                return str(left_key) < str(right_key)
            return super().lessThan(left, right)

    page = QtWidgets.QWidget()
    page.setObjectName("Page")
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(32, 28, 32, 28)
    root.setSpacing(18)

    header = QtWidgets.QHBoxLayout()
    title_box = QtWidgets.QVBoxLayout()
    title = QtWidgets.QLabel("Projektverwaltung")
    title.setObjectName("PageTitle")
    subtitle = QtWidgets.QLabel("Projekte suchen, prüfen und verwalten.")
    subtitle.setObjectName("MutedText")
    title_box.addWidget(title)
    title_box.addWidget(subtitle)
    header.addLayout(title_box, 1)
    root.addLayout(header)

    toolbar = QtWidgets.QHBoxLayout()
    toolbar.setSpacing(12)

    search = QtWidgets.QLineEdit()
    search.setObjectName("SearchField")
    search.setPlaceholderText("Projekte suchen")
    search.setClearButtonEnabled(True)
    toolbar.addWidget(search, 1)

    status_filter = QtWidgets.QComboBox()
    status_filter.setObjectName("StatusFilter")
    status_filter.addItems(list(STATUS_FILTERS))
    toolbar.addWidget(status_filter)

    refresh_button = QtWidgets.QPushButton("Aktualisieren")
    refresh_button.setObjectName("ActionButton")
    toolbar.addWidget(refresh_button)
    root.addLayout(toolbar)
    load_error_label = QtWidgets.QLabel("")
    load_error_label.setObjectName("ErrorText")
    load_error_label.setWordWrap(True)
    load_error_label.hide()
    root.addWidget(load_error_label)

    content = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
    content.setObjectName("ContentSplitter")
    content.setChildrenCollapsible(False)

    table = QtWidgets.QTableView()
    table.setObjectName("ProjectsTable")
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    table.setSortingEnabled(True)
    table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    table.setTextElideMode(QtCore.Qt.ElideRight)
    table.setWordWrap(False)

    class StatusToggleDelegate(QtWidgets.QStyledItemDelegate):
        def paint(self, painter, option, index):
            checked = index.data(QtCore.Qt.CheckStateRole) == QtCore.Qt.CheckState.Checked.value
            view_option = QtWidgets.QStyleOptionViewItem(option)
            self.initStyleOption(view_option, index)
            view_option.features &= ~QtWidgets.QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
            view_option.text = ""
            style = option.widget.style() if option.widget else QtWidgets.QApplication.style()
            style.drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem, view_option, painter, option.widget)

            track = QtCore.QRect(option.rect.left() + 8, option.rect.center().y() - 8, 32, 16)
            knob = QtCore.QRect(track.right() - 13 if checked else track.left() + 2, track.top() + 2, 12, 12)
            painter.save()
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor("#238b45" if checked else "#8b2f3b"))
            painter.drawRoundedRect(track, 8, 8)
            painter.setBrush(QtGui.QColor("#ffffff"))
            painter.drawEllipse(knob)
            selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
            # Green/red text on the blue selection is hard to read; the toggle
            # colour still carries the state.
            painter.setPen(QtGui.QColor("#ffffff" if selected else ("#2ecc71" if checked else "#e74c3c")))
            painter.drawText(option.rect.adjusted(48, 0, -4, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, index.data())
            painter.restore()

        def editorEvent(self, event, model, option, index):
            clicked = (
                event.type() == QtCore.QEvent.Type.MouseButtonRelease
                and event.button() == QtCore.Qt.MouseButton.LeftButton
            )
            keyed = (
                event.type() == QtCore.QEvent.Type.KeyPress
                and event.key() in (QtCore.Qt.Key.Key_Space, QtCore.Qt.Key.Key_Return)
            )
            if not clicked and not keyed:
                return False
            checked = index.data(QtCore.Qt.CheckStateRole) == QtCore.Qt.CheckState.Checked.value
            return model.setData(
                index,
                QtCore.Qt.CheckState.Unchecked if checked else QtCore.Qt.CheckState.Checked,
                QtCore.Qt.CheckStateRole,
            )

    status_toggle_delegate = StatusToggleDelegate(table)
    status_toggle_delegate.setObjectName("ProjectStatusToggleDelegate")
    table.setItemDelegateForColumn(3, status_toggle_delegate)
    source_model = _create_projects_model(QtCore, QtGui, projects, project_role, disabled_role, search_role, sort_role)
    proxy_model = ProjectsFilterProxy()
    proxy_model.setSourceModel(source_model)
    proxy_model.setFilterKeyColumn(-1)
    status_filter.currentTextChanged.connect(proxy_model.set_status)

    table.setModel(proxy_model)

    optional_columns = (2, 5)  # Format, Aktualisiert

    def update_optional_columns():
        narrow = table.viewport().width() < 640
        for column in optional_columns:
            table.setColumnHidden(column, narrow)

    class NarrowTableWatcher(QtCore.QObject):
        def eventFilter(self, watched, event):  # noqa: N802 - Qt override
            if event.type() == QtCore.QEvent.Type.Resize:
                update_optional_columns()
            return False

    narrow_watcher = NarrowTableWatcher(table)
    table.viewport().installEventFilter(narrow_watcher)

    def fit_project_columns():
        header = table.horizontalHeader()
        table.resizeColumnsToContents()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        # "Projekt" absorbs the free width (and elides) so Status and dates
        # stay visible without horizontal scrolling on 1024 px screens.
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.resizeSection(0, min(max(header.sectionSize(0), 90), 180))
        header.resizeSection(3, max(header.sectionSize(3), 112))

    fit_project_columns()

    table_container = QtWidgets.QWidget()
    table_container_layout = QtWidgets.QVBoxLayout(table_container)
    table_container_layout.setContentsMargins(0, 0, 0, 0)
    table_container_layout.setSpacing(8)
    empty_state = QtWidgets.QFrame()
    empty_state.setObjectName("ProjectsEmptyState")
    empty_state_layout = QtWidgets.QHBoxLayout(empty_state)
    empty_state_layout.setContentsMargins(14, 10, 14, 10)
    empty_state_label = QtWidgets.QLabel("")
    empty_state_label.setObjectName("MutedText")
    empty_state_label.setWordWrap(True)
    empty_state_layout.addWidget(empty_state_label, 1)
    empty_state_button = QtWidgets.QPushButton("Einstellungen öffnen")
    empty_state_button.setObjectName("ActionButton")
    empty_state_button.setCursor(QtCore.Qt.PointingHandCursor)
    empty_state_button.setVisible(False)
    if on_open_settings is not None:
        empty_state_button.clicked.connect(lambda: on_open_settings())
    empty_state_layout.addWidget(empty_state_button)
    empty_state.hide()
    table_container_layout.addWidget(empty_state)
    table_container_layout.addWidget(table, 1)
    content.addWidget(table_container)

    def update_empty_state():
        if proxy_model.rowCount() > 0:
            empty_state.hide()
            return
        reason = ""
        if not projects and empty_state_provider is not None:
            try:
                reason = str(empty_state_provider() or "")
            except Exception:
                reason = ""
        if reason:
            empty_state_label.setText(reason)
            empty_state_button.setVisible(on_open_settings is not None)
        elif projects:
            empty_state_label.setText("Keine Projekte passen zur Suche oder zum Statusfilter.")
            empty_state_button.setVisible(False)
        else:
            empty_state_label.setText("Noch keine Projekte vorhanden. Neue Projekte über „Upload“ anlegen.")
            empty_state_button.setVisible(False)
        empty_state.show()

    status_filter.currentTextChanged.connect(lambda _text: update_empty_state())

    detail_panel = QtWidgets.QFrame()
    detail_panel.setObjectName("DetailPanel")
    detail_layout = QtWidgets.QVBoxLayout(detail_panel)
    detail_layout.setContentsMargins(20, 20, 20, 20)
    detail_layout.setSpacing(14)

    title_row = QtWidgets.QHBoxLayout()
    detail_title = QtWidgets.QLabel("Kein Projekt ausgewählt")
    detail_title.setObjectName("PanelTitle")
    detail_title.setWordWrap(True)
    status_badge = QtWidgets.QLabel("")
    status_badge.setObjectName("StatusPill")
    status_badge.setAlignment(QtCore.Qt.AlignCenter)
    status_badge.hide()
    title_row.addWidget(detail_title, 1)
    title_row.addWidget(status_badge, 0, QtCore.Qt.AlignTop)
    detail_layout.addLayout(title_row)

    detail_hint = QtWidgets.QLabel("Wähle ein Projekt aus, um die wichtigsten Daten und Punktwolken zu sehen.")
    detail_hint.setObjectName("MutedText")
    detail_hint.setWordWrap(True)
    detail_layout.addWidget(detail_hint)

    info_container = QtWidgets.QWidget()
    info_grid = QtWidgets.QGridLayout(info_container)
    info_grid.setContentsMargins(0, 0, 0, 0)
    info_grid.setHorizontalSpacing(14)
    info_grid.setVerticalSpacing(8)
    info_field_keys = ("Kunde", "Format", "Punktwolken", "3D-Modelle", "CRS", "Erstellt am", "Viewer-Link", "S3-Pfad")
    info_values = {}
    for row, key in enumerate(info_field_keys):
        key_label = QtWidgets.QLabel(key)
        key_label.setObjectName("SectionTitle")
        value_label = QtWidgets.QLabel("-")
        value_label.setObjectName("MutedText")
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        info_grid.addWidget(key_label, row, 0, QtCore.Qt.AlignTop)
        info_grid.addWidget(value_label, row, 1)
        info_values[key] = value_label
    info_grid.setColumnStretch(1, 1)
    info_container.hide()
    detail_layout.addWidget(info_container)

    viewer_link_label = info_values["Viewer-Link"]
    viewer_link_label.setOpenExternalLinks(False)
    viewer_link_label.setTextInteractionFlags(
        QtCore.Qt.TextBrowserInteraction | QtCore.Qt.TextSelectableByKeyboard
    )
    viewer_link_label.linkActivated.connect(lambda url: QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)))

    def _set_viewer_link(label, project):
        link = project.link or ""
        if link and not project.disabled:
            label.setTextFormat(QtCore.Qt.RichText)
            label.setText(f'<a href="{link}" style="color:#7ab8ff; text-decoration:none;">{link}</a>')
            label.setToolTip("Im Browser öffnen")
        else:
            label.setTextFormat(QtCore.Qt.PlainText)
            label.setText(link or "-")
            label.setToolTip("Link ist deaktiviert" if link else "")

    cloud_label = QtWidgets.QLabel("Punktwolken")
    cloud_label.setObjectName("SectionTitle")
    cloud_label.hide()
    cloud_list = QtWidgets.QListWidget()
    cloud_list.setObjectName("PointcloudList")
    cloud_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    cloud_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    cloud_list.hide()
    detail_layout.addWidget(cloud_label)
    detail_layout.addWidget(cloud_list, 1)

    model_label = QtWidgets.QLabel("3D-Modelle")
    model_label.setObjectName("SectionTitle")
    model_label.hide()
    model_list = QtWidgets.QListWidget()
    model_list.setObjectName("ModelList")
    model_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    model_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    model_list.hide()
    detail_layout.addWidget(model_label)
    detail_layout.addWidget(model_list, 1)

    history_label = QtWidgets.QLabel("Historie")
    history_label.setObjectName("SectionTitle")
    history_label.hide()
    history_log = QtWidgets.QPlainTextEdit()
    history_log.setObjectName("ProjectHistoryLog")
    history_log.setReadOnly(True)
    history_log.setMaximumHeight(130)
    history_log.hide()
    detail_layout.addWidget(history_label)
    detail_layout.addWidget(history_log)

    primary_action_ids = (ACTION_OPEN_LINK, ACTION_COPY_LINK)
    edit_action_ids = (
        ACTION_RENAME,
        ACTION_REPAIR_CRS_METADATA,
        ACTION_REPLACE_ALL_POINTCLOUDS,
        ACTION_ADD_POINTCLOUDS,
        ACTION_ADD_MODELS,
        ACTION_REPLACE_SINGLE_POINTCLOUD,
        ACTION_REPLACE_SINGLE_MODEL,
        ACTION_REMOVE_MODEL,
        ACTION_REMOVE_POINTCLOUD,
        ACTION_DUPLICATE,
        ACTION_DOWNLOAD,
        ACTION_DISABLE_LINK,
        ACTION_ENABLE_LINK,
        ACTION_DELETE,
    )
    # Two rows: the primary action spans the panel, secondary actions below.
    # A single row clipped the labels once the panel got narrower than ~430 px.
    actions = QtWidgets.QVBoxLayout()
    actions.setSpacing(8)
    secondary_actions = QtWidgets.QHBoxLayout()
    secondary_actions.setSpacing(8)
    action_buttons = {}
    for action_id in primary_action_ids:
        button = QtWidgets.QPushButton(action_by_id(action_id).label)
        button.setObjectName("PrimaryButton" if action_id == ACTION_OPEN_LINK else "ActionButton")
        button.setEnabled(False)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.clicked.connect(
            lambda checked=False, selected_action_id=action_id: _handle_project_action_click(selected_action_id)
        )
        if action_id == ACTION_OPEN_LINK:
            actions.addWidget(button)
        else:
            secondary_actions.addWidget(button, 1)
        action_buttons[action_id] = button

    edit_button = QtWidgets.QToolButton()
    edit_button.setObjectName("ActionButton")
    edit_button.setText("Bearbeiten")
    edit_button.setEnabled(False)
    edit_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    edit_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    edit_button.setArrowType(QtCore.Qt.DownArrow)
    edit_button.setCursor(QtCore.Qt.PointingHandCursor)
    edit_menu = QtWidgets.QMenu(edit_button)
    edit_actions = {}
    for action_id in edit_action_ids:
        if action_id in {ACTION_REMOVE_POINTCLOUD, ACTION_REMOVE_MODEL, ACTION_DELETE}:
            edit_menu.addSeparator()
        menu_action = edit_menu.addAction(action_by_id(action_id).label)
        menu_action.triggered.connect(
            lambda checked=False, selected_action_id=action_id: _handle_project_action_click(selected_action_id)
        )
        edit_actions[action_id] = menu_action
    edit_button.setMenu(edit_menu)
    edit_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    secondary_actions.addWidget(edit_button, 1)
    actions.addLayout(secondary_actions)
    detail_layout.addLayout(actions)
    detail_panel.setMinimumWidth(360)
    content.addWidget(detail_panel)
    content.setStretchFactor(0, 3)
    content.setStretchFactor(1, 2)
    content.setSizes([760, 420])

    root.addWidget(content, 1)

    def selected_project():
        selection = table.selectionModel().selectedRows()
        if not selection:
            return None
        source_index = proxy_model.mapToSource(selection[0])
        return source_model.data(source_index, project_role)

    def selected_pointcloud():
        selected_items = cloud_list.selectedItems()
        if not selected_items:
            return None
        return selected_items[0].data(pointcloud_role)

    def selected_model():
        selected_items = model_list.selectedItems()
        if not selected_items:
            return None
        return selected_items[0].data(model_role)

    load_generation = 0
    active_loads = []

    def cleanup_load(current_bundle, current_receiver):
        try:
            active_loads.remove((current_bundle, current_receiver))
        except ValueError:
            pass
        current_receiver.deleteLater()
        if on_load_state_changed is not None:
            on_load_state_changed()

    def apply_projects(loaded_projects):
        nonlocal projects
        selected = selected_project()
        selected_project_id = selected.project_id if selected is not None else ""
        projects = tuple(loaded_projects)
        source_model.blockSignals(True)
        try:
            _populate_projects_model(
                QtCore,
                QtGui,
                source_model,
                projects,
                project_role,
                disabled_role,
                search_role,
                sort_role,
            )
        finally:
            source_model.blockSignals(False)
        proxy_model.invalidate()
        fit_project_columns()
        update_empty_state()
        if selected_project_id:
            _select_project_by_id(selected_project_id)
        _select_first_visible_project_if_needed()
        update_detail_panel()

    class ProjectLoadReceiver(QtCore.QObject):
        def __init__(self, generation):
            super().__init__(page)
            self.generation = generation
            self.result_handled = False
            self.thread_done = False
            self.bundle = None

        def _finish_if_ready(self):
            if self.result_handled and self.thread_done:
                cleanup_load(self.bundle, self)

        @QtCore.Slot(object)
        def loaded(self, loaded_projects):
            try:
                if self.generation == load_generation:
                    load_error_label.hide()
                    load_error_label.setText("")
                    apply_projects(loaded_projects)
                    if on_load_finished is not None:
                        on_load_finished(True, "")
            finally:
                self.result_handled = True
                self._finish_if_ready()

        @QtCore.Slot(object)
        def failed(self, error):
            try:
                if self.generation == load_generation:
                    reason = describe_error(error) if isinstance(error, BaseException) else str(error)
                    load_error_label.setText(f"Projekte konnten nicht geladen werden: {reason}")
                    load_error_label.show()
                    if on_load_finished is not None:
                        on_load_finished(False, reason)
            finally:
                self.result_handled = True
                self._finish_if_ready()

        @QtCore.Slot()
        def thread_finished(self):
            self.thread_done = True
            self._finish_if_ready()

    def reload_projects():
        nonlocal load_generation
        if can_start_load is not None and not can_start_load():
            return
        if project_provider is None:
            apply_projects(project_previews or ())
            return
        load_generation += 1
        receiver = ProjectLoadReceiver(load_generation)
        bundle = create_task_worker(QtCore, lambda: load_project_previews(project_provider))
        receiver.bundle = bundle
        active_loads.append((bundle, receiver))
        if on_load_state_changed is not None:
            on_load_state_changed()
        bundle.worker.result.connect(receiver.loaded)
        bundle.worker.error.connect(receiver.failed)

        bundle.thread.finished.connect(receiver.thread_finished)
        bundle.thread.start()

    def _select_project_by_id(project_id: str):
        selection_model = table.selectionModel()
        if selection_model is None:
            return
        selection_model.clearSelection()
        for row in range(source_model.rowCount()):
            source_index = source_model.index(row, 0)
            project = source_model.data(source_index, project_role)
            if project is None or project.project_id != project_id:
                continue
            proxy_index = proxy_model.mapFromSource(source_index)
            if not proxy_index.isValid():
                return
            selection_model.select(
                proxy_index,
                QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
            )
            table.scrollTo(proxy_index)
            return

    def _select_first_visible_project_if_needed():
        selection_model = table.selectionModel()
        if selection_model is None:
            return
        if _has_visible_selected_project(selection_model):
            return
        if proxy_model.rowCount() <= 0:
            selection_model.clearSelection()
            return
        first_index = proxy_model.index(0, 0)
        if not first_index.isValid():
            selection_model.clearSelection()
            return
        selection_model.select(
            first_index,
            QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
        )
        table.setCurrentIndex(first_index)
        table.scrollTo(first_index)

    def _has_visible_selected_project(selection_model):
        for index in selection_model.selectedRows():
            if not index.isValid() or index.model() is not proxy_model:
                continue
            if 0 <= index.row() < proxy_model.rowCount():
                return True
        return False

    def _handle_project_action_click(action_id: str, project_override=None):
        # Capture the current selection now, then run the action on the next
        # event-loop tick. Opening a modal dialog directly from a QMenu/QToolButton
        # "triggered" slot re-enters the menu's popup loop and can crash Qt on
        # Windows, so the dispatch is deferred until the menu has fully closed.
        project = project_override or selected_project()
        pointcloud = None if project_override is not None or action_id == ACTION_ADD_MODELS else (
            selected_model() if action_id in {ACTION_REPLACE_SINGLE_MODEL, ACTION_REMOVE_MODEL} else selected_pointcloud()
        )
        QtCore.QTimer.singleShot(
            0,
            lambda: _dispatch_project_action(action_callback, action_id, project, pointcloud),
        )

    def _handle_status_toggle(item):
        if item.column() != 3:
            return
        project = item.data(project_role)
        if project is None:
            return
        requested_disabled = item.checkState() != QtCore.Qt.CheckState.Checked
        if requested_disabled == project.disabled:
            return
        source_model.blockSignals(True)
        try:
            item.setCheckState(
                QtCore.Qt.CheckState.Unchecked if project.disabled else QtCore.Qt.CheckState.Checked
            )
        finally:
            source_model.blockSignals(False)
        _handle_project_action_click(
            ACTION_ENABLE_LINK if project.disabled else ACTION_DISABLE_LINK,
            project,
        )

    source_model.itemChanged.connect(_handle_status_toggle)

    def show_project_context_menu(position):
        index = table.indexAt(position)
        if not index.isValid():
            return
        table.selectionModel().select(
            index,
            QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
        )
        update_detail_panel()
        project = selected_project()
        if project is None:
            return
        menu = QtWidgets.QMenu(table)
        for action_id in (
            ACTION_OPEN_LINK,
            ACTION_COPY_LINK,
            ACTION_RENAME,
            ACTION_REPAIR_CRS_METADATA,
            ACTION_DUPLICATE,
            ACTION_DOWNLOAD,
            ACTION_DISABLE_LINK,
            ACTION_ENABLE_LINK,
            ACTION_DELETE,
            ACTION_REPLACE_ALL_POINTCLOUDS,
            ACTION_ADD_POINTCLOUDS,
            ACTION_ADD_MODELS,
        ):
            if not is_action_available(action_id, project):
                continue
            action = action_by_id(action_id)
            menu_action = menu.addAction(action.label)
            menu_action.triggered.connect(lambda checked=False, selected_action_id=action_id: _handle_project_action_click(selected_action_id))
        if menu.actions():
            menu.exec(table.viewport().mapToGlobal(position))

    def show_pointcloud_context_menu(position):
        item = cloud_list.itemAt(position)
        if item is None:
            return
        cloud_list.setCurrentItem(item)
        project = selected_project()
        pointcloud = selected_pointcloud()
        menu = QtWidgets.QMenu(cloud_list)
        for action_id in (ACTION_REPLACE_SINGLE_POINTCLOUD, ACTION_REMOVE_POINTCLOUD):
            if not is_action_available(action_id, project, pointcloud):
                continue
            if action_id == ACTION_REMOVE_POINTCLOUD and menu.actions():
                menu.addSeparator()
            menu_action = menu.addAction(action_by_id(action_id).label)
            menu_action.triggered.connect(
                lambda checked=False, selected_action_id=action_id: _handle_project_action_click(selected_action_id)
            )
        if menu.actions():
            menu.exec(cloud_list.viewport().mapToGlobal(position))

    def show_model_context_menu(position):
        item = model_list.itemAt(position)
        if item is None:
            return
        model_list.setCurrentItem(item)
        project = selected_project()
        model = selected_model()
        if not any(
            is_action_available(action_id, project, model)
            for action_id in (ACTION_REPLACE_SINGLE_MODEL, ACTION_REMOVE_MODEL)
        ):
            return
        menu = QtWidgets.QMenu(model_list)
        for action_id in (ACTION_REPLACE_SINGLE_MODEL, ACTION_REMOVE_MODEL):
            if not is_action_available(action_id, project, model):
                continue
            if action_id == ACTION_REMOVE_MODEL and menu.actions():
                menu.addSeparator()
            menu_action = menu.addAction(action_by_id(action_id).label)
            menu_action.triggered.connect(
                lambda checked=False, selected_action_id=action_id: _handle_project_action_click(selected_action_id)
            )
        menu.exec(model_list.viewport().mapToGlobal(position))

    def update_action_buttons():
        project = selected_project()
        pointcloud = selected_pointcloud()
        for action_id, button in action_buttons.items():
            button.setEnabled(is_action_available(action_id, project, pointcloud))
        any_edit_available = False
        for action_id, menu_action in edit_actions.items():
            resource = selected_model() if action_id in {ACTION_REPLACE_SINGLE_MODEL, ACTION_REMOVE_MODEL} else pointcloud
            available = is_action_available(action_id, project, resource)
            menu_action.setEnabled(available)
            menu_action.setVisible(available)
            any_edit_available = any_edit_available or available
        edit_button.setEnabled(any_edit_available)

    def update_detail_panel():
        project = selected_project()
        update_action_buttons()
        cloud_list.clear()
        model_list.clear()
        if project is None:
            detail_title.setText("Kein Projekt ausgewählt")
            status_badge.hide()
            detail_hint.show()
            info_container.hide()
            cloud_label.hide()
            cloud_list.hide()
            model_label.hide()
            model_list.hide()
            history_label.hide()
            history_log.hide()
            return

        detail_title.setText(project.project)
        status_badge.setText("Inaktiv" if project.disabled else "Aktiv")
        status_badge.setObjectName("StatusPillDanger" if project.disabled else "StatusPill")
        status_badge.style().unpolish(status_badge)
        status_badge.style().polish(status_badge)
        status_badge.show()
        detail_hint.hide()
        info_container.show()

        info_values["Kunde"].setText(project.customer or "-")
        info_values["Format"].setText(project.format or "-")
        info_values["Punktwolken"].setText(str(len(project.pointclouds)))
        info_values["3D-Modelle"].setText(str(len(project.models)))
        info_values["CRS"].setText(getattr(project, "crs", "") or "-")
        info_values["Erstellt am"].setText(project.created or "-")
        _set_viewer_link(info_values["Viewer-Link"], project)
        info_values["S3-Pfad"].setText(project.s3_path or "-")

        cloud_label.show()
        cloud_list.show()
        for pointcloud in project.pointclouds:
            parts = [pointcloud.name, pointcloud.format]
            if pointcloud.points and pointcloud.points != "-":
                parts.append(f"{pointcloud.points} Punkte")
            parts.append(f"CRS: {pointcloud.crs}")
            item = QtWidgets.QListWidgetItem("  ·  ".join(parts))
            item.setData(pointcloud_role, pointcloud)
            if pointcloud.s3_path:
                item.setToolTip(pointcloud.s3_path)
            cloud_list.addItem(item)

        if project.models:
            model_label.show()
            model_list.show()
            for model in project.models:
                crs = " / ".join(value for value in (model.crs, model.vertical_crs) if value) or "Unbekannt"
                item = QtWidgets.QListWidgetItem("  ·  ".join((model.name, "GLB", f"CRS: {crs}")))
                item.setData(model_role, model)
                item.setToolTip(model.s3_path)
                model_list.addItem(item)
        else:
            model_label.hide()
            model_list.hide()

        if project.history:
            history_log.setPlainText("\n".join(project.history))
            history_label.show()
            history_log.show()
        else:
            history_log.clear()
            history_label.hide()
            history_log.hide()

        update_action_buttons()

    def open_selected_project_if_available():
        project = selected_project()
        if is_action_available(ACTION_OPEN_LINK, project):
            _handle_project_action_click(ACTION_OPEN_LINK)

    def replace_double_clicked_pointcloud():
        project = selected_project()
        pointcloud = selected_pointcloud()
        if is_action_available(ACTION_REPLACE_SINGLE_POINTCLOUD, project, pointcloud):
            _handle_project_action_click(ACTION_REPLACE_SINGLE_POINTCLOUD)

    def replace_double_clicked_model():
        project = selected_project()
        model = selected_model()
        if is_action_available(ACTION_REPLACE_SINGLE_MODEL, project, model):
            _handle_project_action_click(ACTION_REPLACE_SINGLE_MODEL)

    table.selectionModel().selectionChanged.connect(lambda selected, deselected: update_detail_panel())
    table.doubleClicked.connect(lambda index: open_selected_project_if_available())
    cloud_list.itemSelectionChanged.connect(update_action_buttons)
    model_list.itemSelectionChanged.connect(update_action_buttons)
    cloud_list.itemDoubleClicked.connect(lambda item: replace_double_clicked_pointcloud())
    model_list.itemDoubleClicked.connect(lambda item: replace_double_clicked_model())
    table.customContextMenuRequested.connect(show_project_context_menu)
    cloud_list.customContextMenuRequested.connect(show_pointcloud_context_menu)
    model_list.customContextMenuRequested.connect(show_model_context_menu)
    # Debounced: filtering a large index on every keystroke made typing lag.
    search_timer = QtCore.QTimer(page)
    search_timer.setSingleShot(True)
    search_timer.setInterval(PROJECT_SEARCH_DEBOUNCE_MS)

    def apply_search_now():
        search_timer.stop()
        proxy_model.setFilterFixedString(search.text())
        update_empty_state()
        _select_first_visible_project_if_needed()
        update_detail_panel()

    search_timer.timeout.connect(apply_search_now)
    search.textChanged.connect(lambda _text: search_timer.start())
    page.apply_search_now = apply_search_now
    status_filter.currentTextChanged.connect(lambda text: (_select_first_visible_project_if_needed(), update_detail_panel()))
    refresh_button.clicked.connect(reload_projects)
    proxy_model.modelReset.connect(update_detail_panel)
    proxy_model.rowsRemoved.connect(update_detail_panel)
    def focus_search():
        search.setFocus()
        search.selectAll()

    def clear_search():
        if search.text():
            search.clear()
            apply_search_now()  # Esc should reset the list immediately
            return True
        return False

    _select_first_visible_project_if_needed()
    update_detail_panel()
    page.reload_projects = reload_projects
    page.project_load_error_label = load_error_label
    page._active_project_loads = active_loads
    page.focus_search = focus_search
    page.clear_search = clear_search
    page.focus_default = focus_search
    if project_provider is not None:
        QtCore.QTimer.singleShot(0, reload_projects)
    return page


def _resolve_project_previews(
    project_previews: Iterable[ProjectPreview] | None = None,
    project_provider: ProjectProvider | None = None,
) -> tuple[ProjectPreview, ...]:
    if project_previews is not None:
        return tuple(project_previews)
    if project_provider is not None:
        try:
            return load_project_previews(project_provider)
        except Exception:
            return ()
    return ()


def _dispatch_project_action(
    callback: ProjectActionCallback | None,
    action_id: str,
    project: ProjectPreview | None,
    pointcloud=None,
) -> object | None:
    if callback is None:
        return None

    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return callback(action_id, project, pointcloud)

    accepts_varargs = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values())
    positional_parameters = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if accepts_varargs or len(positional_parameters) >= 3:
        return callback(action_id, project, pointcloud)
    if len(positional_parameters) >= 2:
        return callback(action_id, project)
    return callback(action_id)


def _create_projects_model(QtCore, QtGui, projects, project_role, disabled_role, search_role, sort_role):
    model = QtGui.QStandardItemModel(0, 6)
    _populate_projects_model(QtCore, QtGui, model, projects, project_role, disabled_role, search_role, sort_role)
    return model


def _populate_projects_model(QtCore, QtGui, model, projects, project_role, disabled_role, search_role, sort_role):
    model.setRowCount(0)
    model.setHorizontalHeaderLabels(["Kunde", "Projekt", "Format", "Status", "Erstellt am", "Aktualisiert"])
    for project in projects:
        row = (project.customer, project.project, project.format, project.status, project.created, project.updated)
        items = [QtGui.QStandardItem(value) for value in row]
        search_text = _format_project_search_text(project)
        for item in items:
            item.setEditable(False)
            item.setData(project, project_role)
            item.setData(project.disabled, disabled_role)
            item.setData(search_text, search_role)
        items[4].setData(project_datum_sort_key(project.created), sort_role)
        items[5].setData(project.updated_sort, sort_role)
        items[3].setForeground(QtGui.QBrush(QtGui.QColor("#e74c3c" if project.disabled else "#2ecc71")))
        items[3].setCheckable(True)
        items[3].setCheckState(
            QtCore.Qt.CheckState.Unchecked if project.disabled else QtCore.Qt.CheckState.Checked
        )
        model.appendRow(items)


def _format_project_search_text(project) -> str:
    pointcloud_text = " ".join(
        " ".join((pointcloud.name, pointcloud.format, pointcloud.crs, pointcloud.s3_path, pointcloud.viewer_path))
        for pointcloud in project.pointclouds
    )
    model_text = " ".join(
        " ".join((model.model_id, model.name, model.format, model.crs, model.vertical_crs, model.s3_path, model.viewer_path))
        for model in project.models
    )
    return " ".join(
        (
            project.project_id,
            project.project,
            project.customer,
            project.format,
            project.status,
            project.created,
            project.updated,
            project.link,
            project.s3_path,
            project.viewer_path,
            pointcloud_text,
            model_text,
        )
    )
