"""Qt dialog factories for project management actions."""

from __future__ import annotations

from .path_drop import append_unique_paths, create_path_drop_plain_text_edit
from .project_management import ProjectPreview
from .project_management_dialog_models import (
    ProjectDuplicateDialogState,
    ProjectDownloadDialogState,
    ProjectReplaceDialogState,
    ProjectRenameDialogState,
    build_delete_dialog_state,
    build_download_dialog_state,
    build_link_state_dialog_state,
    build_duplicate_dialog_state,
    build_rename_dialog_state,
    validate_download_dialog_state,
    validate_duplicate_dialog_state,
    validate_rename_dialog_state,
    validate_replace_all_dialog_state,
    validate_replace_single_dialog_state,
)


def prompt_rename_project(QtWidgets, parent, project: ProjectPreview):
    state = build_rename_dialog_state(project)
    dialog, customer_input, project_input, pointcloud_inputs = _build_project_metadata_dialog(
        QtWidgets,
        parent,
        "Projekt umbenennen",
        "Speichert neue Projekt- und Punktwolkentitel ohne S3-Pfade zu verändern.",
        state.customer,
        state.project,
        state.pointcloud_names,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return validate_rename_dialog_state(
        ProjectRenameDialogState(
            customer=customer_input.text(),
            project=project_input.text(),
            pointcloud_names=tuple(input_widget.text() for input_widget in pointcloud_inputs),
            fallback_pointcloud_names=state.fallback_pointcloud_names,
        )
    )


def prompt_duplicate_project(QtWidgets, parent, project: ProjectPreview):
    state = build_duplicate_dialog_state(project)
    dialog, customer_input, project_input, _pointcloud_inputs = _build_project_metadata_dialog(
        QtWidgets,
        parent,
        "Projekt duplizieren",
        "Erzeugt ein aktives Projekt mit eigener ID und kopierten S3-Daten.",
        state.customer,
        state.project,
        (),
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return validate_duplicate_dialog_state(
        ProjectDuplicateDialogState(customer=customer_input.text(), project=project_input.text())
    )


def confirm_delete_project(QtWidgets, parent, project: ProjectPreview) -> bool:
    state = build_delete_dialog_state(project)
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Projekt löschen")
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QtWidgets.QLabel(state.project_label)
    title.setObjectName("PanelTitle")
    detail = QtWidgets.QLabel(state.detail_text)
    detail.setObjectName("MutedText")
    detail.setWordWrap(True)
    layout.addWidget(title)
    layout.addWidget(detail)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
    delete_button = buttons.addButton(state.confirmation_label, QtWidgets.QDialogButtonBox.DestructiveRole)
    delete_button.clicked.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QtWidgets.QDialog.Accepted


def prompt_download_project(QtWidgets, parent, project: ProjectPreview):
    state = build_download_dialog_state(project)
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Projekt herunterladen")
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QtWidgets.QLabel(state.project_label)
    title.setObjectName("PanelTitle")
    detail = QtWidgets.QLabel(state.detail_text)
    detail.setObjectName("MutedText")
    detail.setWordWrap(True)
    layout.addWidget(title)
    layout.addWidget(detail)

    target_row = QtWidgets.QHBoxLayout()
    target_input = QtWidgets.QLineEdit(state.target_dir)
    target_input.setPlaceholderText("Zielordner für den Download")
    browse_button = QtWidgets.QPushButton("Ordner")
    target_row.addWidget(target_input, 1)
    target_row.addWidget(browse_button)
    layout.addLayout(target_row)

    error_label = QtWidgets.QLabel("")
    error_label.setObjectName("ErrorText")
    error_label.setWordWrap(True)
    error_label.hide()
    layout.addWidget(error_label)

    def browse_folder():
        selected_dir = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Zielordner für den Download wählen")
        if selected_dir:
            target_input.setText(selected_dir)

    browse_button.clicked.connect(browse_folder)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def accept_if_valid():
        try:
            validate_download_dialog_state(ProjectDownloadDialogState(target_dir=target_input.text()))
        except ValueError as error:
            error_label.setText(str(error))
            error_label.show()
            return
        dialog.accept()

    buttons.accepted.connect(accept_if_valid)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return validate_download_dialog_state(ProjectDownloadDialogState(target_dir=target_input.text()))


def confirm_set_project_link_state(QtWidgets, parent, project: ProjectPreview, disabled: bool) -> bool:
    state = build_link_state_dialog_state(project, disabled)
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(state.confirmation_label)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QtWidgets.QLabel(state.project_label)
    title.setObjectName("PanelTitle")
    detail = QtWidgets.QLabel(state.detail_text)
    detail.setObjectName("MutedText")
    detail.setWordWrap(True)
    layout.addWidget(title)
    layout.addWidget(detail)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
    confirm_button = buttons.addButton(state.confirmation_label, QtWidgets.QDialogButtonBox.ActionRole)
    confirm_button.clicked.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QtWidgets.QDialog.Accepted


def prompt_replace_all_pointclouds(QtWidgets, parent, project: ProjectPreview, defaults=None):
    (
        dialog,
        source_paths,
        converter_input,
        output_input,
        horizontal_crs_input,
        vertical_crs_input,
        overwrite_input,
    ) = _build_replace_dialog(
        QtWidgets,
        parent,
        "Punktwolkendaten austauschen",
        f"Ersetzt alle Punktwolken in {project.project}.",
        allow_multiple=True,
        defaults=defaults,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return validate_replace_all_dialog_state(
        _replace_dialog_state_from_inputs(
            source_paths,
            converter_input,
            output_input,
            horizontal_crs_input,
            vertical_crs_input,
            overwrite_input,
        )
    )


def prompt_replace_single_pointcloud(QtWidgets, parent, project: ProjectPreview, pointcloud, defaults=None):
    (
        dialog,
        source_paths,
        converter_input,
        output_input,
        horizontal_crs_input,
        vertical_crs_input,
        overwrite_input,
    ) = _build_replace_dialog(
        QtWidgets,
        parent,
        "Punktwolke austauschen",
        f"Ersetzt {pointcloud.name} in {project.project}.",
        allow_multiple=False,
        defaults=defaults,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return validate_replace_single_dialog_state(
        _replace_dialog_state_from_inputs(
            source_paths,
            converter_input,
            output_input,
            horizontal_crs_input,
            vertical_crs_input,
            overwrite_input,
        )
    )


def _build_project_metadata_dialog(
    QtWidgets,
    parent,
    title: str,
    description: str,
    customer: str,
    project: str,
    pointcloud_names: tuple[str, ...],
):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    body = QtWidgets.QLabel(description)
    body.setObjectName("MutedText")
    body.setWordWrap(True)
    layout.addWidget(body)

    form = QtWidgets.QFormLayout()
    customer_input = QtWidgets.QLineEdit(customer)
    project_input = QtWidgets.QLineEdit(project)
    form.addRow("Kunde", customer_input)
    form.addRow("Projekt", project_input)
    pointcloud_inputs = []
    for index, name in enumerate(pointcloud_names, start=1):
        input_widget = QtWidgets.QLineEdit(name)
        form.addRow(f"Punktwolke {index}", input_widget)
        pointcloud_inputs.append(input_widget)
    layout.addLayout(form)

    error_label = QtWidgets.QLabel("")
    error_label.setObjectName("ErrorText")
    error_label.setWordWrap(True)
    error_label.hide()
    layout.addWidget(error_label)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def accept_if_valid():
        if not customer_input.text().strip():
            error_label.setText("Kunde darf nicht leer sein.")
            error_label.show()
            return
        if not project_input.text().strip():
            error_label.setText("Projektname darf nicht leer sein.")
            error_label.show()
            return
        dialog.accept()

    buttons.accepted.connect(accept_if_valid)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog, customer_input, project_input, tuple(pointcloud_inputs)


def _build_replace_dialog(QtWidgets, parent, title: str, description: str, allow_multiple: bool, defaults=None):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    body = QtWidgets.QLabel(description)
    body.setObjectName("MutedText")
    body.setWordWrap(True)
    layout.addWidget(body)

    def append_paths(paths):
        merged = append_unique_paths(
            _split_source_paths(source_paths.toPlainText()),
            paths,
            allow_multiple=allow_multiple,
        )
        source_paths.setPlainText("\n".join(merged))

    source_paths = create_path_drop_plain_text_edit(QtWidgets, append_paths)
    source_paths.setPlaceholderText("Dateien oder Potree-Ordner hierher ziehen, eine Quelle pro Zeile")
    source_paths.setMinimumHeight(92)
    layout.addWidget(source_paths)

    browse_row = QtWidgets.QHBoxLayout()
    files_button = QtWidgets.QPushButton("Dateien")
    folder_button = QtWidgets.QPushButton("Ordner")
    browse_row.addWidget(files_button)
    browse_row.addWidget(folder_button)
    browse_row.addStretch(1)
    layout.addLayout(browse_row)

    form = QtWidgets.QFormLayout()
    converter_input = QtWidgets.QLineEdit(_default_text(defaults, "converter_path"))
    output_input = QtWidgets.QLineEdit(_default_text(defaults, "output_base_dir"))
    horizontal_crs_input = QtWidgets.QLineEdit()
    vertical_crs_input = QtWidgets.QLineEdit()
    overwrite_input = QtWidgets.QCheckBox("Bestehende Potree-Ausgabe überschreiben")
    form.addRow("Potree Converter", converter_input)
    form.addRow("Ausgabeordner", output_input)
    form.addRow("Horizontales CRS", horizontal_crs_input)
    form.addRow("Vertikales CRS", vertical_crs_input)
    form.addRow("", overwrite_input)
    layout.addLayout(form)

    error_label = QtWidgets.QLabel("")
    error_label.setObjectName("ErrorText")
    error_label.setWordWrap(True)
    error_label.hide()
    layout.addWidget(error_label)

    def browse_files():
        if allow_multiple:
            paths, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
                dialog,
                "Punktwolken auswählen",
                "",
                "Punktwolken (*.las *.laz *.copc.laz);;Alle Dateien (*)",
            )
        else:
            path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
                dialog,
                "Punktwolke auswählen",
                "",
                "Punktwolken (*.las *.laz *.copc.laz);;Alle Dateien (*)",
            )
            paths = [path] if path else []
        append_paths(paths)

    def browse_folder():
        path = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Potree-Ordner auswählen")
        append_paths([path] if path else [])

    files_button.clicked.connect(browse_files)
    folder_button.clicked.connect(browse_folder)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def accept_if_valid():
        state = _replace_dialog_state_from_inputs(
            source_paths,
            converter_input,
            output_input,
            horizontal_crs_input,
            vertical_crs_input,
            overwrite_input,
        )
        try:
            if allow_multiple:
                validate_replace_all_dialog_state(state)
            else:
                validate_replace_single_dialog_state(state)
        except ValueError as error:
            error_label.setText(str(error))
            error_label.show()
            return
        dialog.accept()

    buttons.accepted.connect(accept_if_valid)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog, source_paths, converter_input, output_input, horizontal_crs_input, vertical_crs_input, overwrite_input


def _replace_dialog_state_from_inputs(
    source_paths,
    converter_input,
    output_input,
    horizontal_crs_input,
    vertical_crs_input,
    overwrite_input,
) -> ProjectReplaceDialogState:
    return ProjectReplaceDialogState(
        source_paths=_split_source_paths(source_paths.toPlainText()),
        converter_path=converter_input.text(),
        output_base_dir=output_input.text(),
        overwrite=overwrite_input.isChecked(),
        horizontal_crs=horizontal_crs_input.text(),
        vertical_crs=vertical_crs_input.text(),
    )


def _split_source_paths(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(text or "").splitlines() if line.strip())


def _default_text(defaults, attribute: str) -> str:
    return str(getattr(defaults, attribute, "") or "")


__all__ = [
    "confirm_delete_project",
    "confirm_set_project_link_state",
    "prompt_download_project",
    "prompt_replace_all_pointclouds",
    "prompt_replace_single_pointcloud",
    "prompt_duplicate_project",
    "prompt_rename_project",
]
