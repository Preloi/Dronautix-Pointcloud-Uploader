"""Qt dialog factories for the upload workflow."""

from __future__ import annotations

from .path_drop import append_unique_paths, create_path_drop_plain_text_edit
from .upload_wizard_model import (
    STEP_CRS_FORMAT,
    STEP_PROJECT,
    STEP_REVIEW,
    STEP_SOURCES,
    STEP_UPLOAD_LOG,
    WIZARD_STEP_ORDER,
    UploadWizardState,
    build_upload_request_from_wizard_state,
    build_upload_wizard_preview,
    validate_wizard_step,
)


def prompt_new_upload(QtWidgets, parent, defaults=None, initial_sources=None):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Neuer Upload")
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    body = QtWidgets.QLabel("Projekt, Quellen, CRS, Review und Upload-Start in fünf Schritten erfassen.")
    body.setObjectName("MutedText")
    body.setWordWrap(True)
    layout.addWidget(body)

    step_row = QtWidgets.QHBoxLayout()
    step_row.setSpacing(8)
    step_titles = ("Projekt", "Quellen", "CRS / Format", "Review", "Upload")
    step_labels = []
    for number, label_text in enumerate(step_titles, start=1):
        label = QtWidgets.QLabel(f"{number}. {label_text}")
        label.setObjectName("PreviewBadgeLight")
        step_row.addWidget(label)
        step_labels.append(label)
    layout.addLayout(step_row)

    stack = QtWidgets.QStackedWidget()
    layout.addWidget(stack, 1)

    project_page = QtWidgets.QWidget()
    project_layout = QtWidgets.QFormLayout(project_page)
    customer_input = QtWidgets.QLineEdit()
    project_input = QtWidgets.QLineEdit()
    project_layout.addRow("Kunde", customer_input)
    project_layout.addRow("Projekt", project_input)
    stack.addWidget(project_page)

    sources_page = QtWidgets.QWidget()
    sources_layout = QtWidgets.QVBoxLayout(sources_page)
    sources_layout.setContentsMargins(0, 0, 0, 0)
    sources_layout.setSpacing(10)
    def append_paths(paths):
        merged = append_unique_paths(_split_source_paths(source_paths.toPlainText()), paths)
        source_paths.setPlainText("\n".join(merged))

    source_paths = create_path_drop_plain_text_edit(QtWidgets, append_paths)
    source_paths.setPlaceholderText("Dateien oder Potree-Ordner hierher ziehen, eine Quelle pro Zeile")
    source_paths.setMinimumHeight(110)
    prefilled_sources = _split_source_paths("\n".join(str(path) for path in (initial_sources or ())))
    if prefilled_sources:
        source_paths.setPlainText("\n".join(prefilled_sources))
    sources_layout.addWidget(source_paths)

    browse_row = QtWidgets.QHBoxLayout()
    files_button = QtWidgets.QPushButton("Dateien")
    folder_button = QtWidgets.QPushButton("Ordner")
    browse_row.addWidget(files_button)
    browse_row.addWidget(folder_button)
    browse_row.addStretch(1)
    sources_layout.addLayout(browse_row)
    stack.addWidget(sources_page)

    options_page = QtWidgets.QWidget()
    options_root = QtWidgets.QVBoxLayout(options_page)
    options_root.setContentsMargins(0, 0, 0, 0)
    options_root.setSpacing(10)
    options = QtWidgets.QFormLayout()
    converter_input = QtWidgets.QLineEdit(_default_text(defaults, "converter_path"))
    output_input = QtWidgets.QLineEdit(_default_text(defaults, "output_base_dir"))
    horizontal_crs_input = QtWidgets.QLineEdit()
    vertical_crs_input = QtWidgets.QLineEdit()
    overwrite_input = QtWidgets.QCheckBox("Bestehende Potree-Ausgabe überschreiben")
    options.addRow("Potree Converter", converter_input)
    options.addRow("Ausgabeordner", output_input)
    options.addRow("Horizontales CRS", horizontal_crs_input)
    options.addRow("Vertikales CRS", vertical_crs_input)
    options.addRow("", overwrite_input)
    options_root.addLayout(options)
    options_browse_row = QtWidgets.QHBoxLayout()
    converter_button = QtWidgets.QPushButton("Converter")
    output_button = QtWidgets.QPushButton("Ausgabeordner")
    options_browse_row.addWidget(converter_button)
    options_browse_row.addWidget(output_button)
    options_browse_row.addStretch(1)
    options_root.addLayout(options_browse_row)
    stack.addWidget(options_page)

    review_text = QtWidgets.QPlainTextEdit()
    review_text.setReadOnly(True)
    review_text.setMinimumHeight(190)
    stack.addWidget(review_text)

    upload_log = QtWidgets.QPlainTextEdit()
    upload_log.setReadOnly(True)
    upload_log.setMinimumHeight(190)
    stack.addWidget(upload_log)

    error_label = QtWidgets.QLabel("")
    error_label.setObjectName("ErrorText")
    error_label.setWordWrap(True)
    error_label.hide()
    layout.addWidget(error_label)

    result_request = {"value": None}
    current_index = {"value": 0}

    def browse_files():
        paths, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
            dialog,
            "Punktwolken auswählen",
            "",
            "Punktwolken (*.las *.laz *.copc.laz);;Alle Dateien (*)",
        )
        append_paths(paths)

    def browse_folder():
        path = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Potree-Ordner auswählen")
        append_paths([path] if path else [])

    def browse_converter():
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            dialog,
            "PotreeConverter auswählen",
            "",
            "PotreeConverter (*.exe);;Alle Dateien (*)",
        )
        if path:
            converter_input.setText(path)

    def browse_output():
        path = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Ausgabeordner auswählen")
        if path:
            output_input.setText(path)

    files_button.clicked.connect(browse_files)
    folder_button.clicked.connect(browse_folder)
    converter_button.clicked.connect(browse_converter)
    output_button.clicked.connect(browse_output)

    def state_from_inputs():
        return UploadWizardState(
            customer=customer_input.text(),
            project=project_input.text(),
            source_paths=_split_source_paths(source_paths.toPlainText()),
            converter_path=converter_input.text(),
            output_base_dir=output_input.text(),
            overwrite=overwrite_input.isChecked(),
            horizontal_crs=horizontal_crs_input.text(),
            vertical_crs=vertical_crs_input.text(),
            current_step=WIZARD_STEP_ORDER[current_index["value"]],
        )

    def set_error(messages):
        if not messages:
            error_label.hide()
            error_label.setText("")
            return
        error_label.setText("\n".join(messages))
        error_label.show()

    def render_dynamic_pages():
        state = state_from_inputs()
        preview = build_upload_wizard_preview(state)
        review_lines = [
            f"Projekt: {preview.project_name}",
            f"Kunde: {preview.customer}",
            f"Quellen: {preview.source_count_label}",
            f"Horizontales CRS: {preview.crs_format.horizontal_crs}",
            f"Vertikales CRS: {preview.crs_format.vertical_crs}",
            f"Zielformat: {preview.crs_format.output_format}",
            f"Upload-Modus: {preview.crs_format.upload_mode}",
            "",
            "Quellen:",
        ]
        review_lines.extend(f"- {source.name} | {source.format} | {source.handling}" for source in preview.sources)
        review_text.setPlainText("\n".join(review_lines))
        upload_log.setPlainText("\n".join(f"[{entry.level}] {entry.message}" for entry in preview.log_entries))

    def set_step(index):
        bounded = max(0, min(index, len(WIZARD_STEP_ORDER) - 1))
        current_index["value"] = bounded
        stack.setCurrentIndex(bounded)
        for label_index, label in enumerate(step_labels):
            marker = ">" if label_index == bounded else " "
            label.setText(f"{marker} {label_index + 1}. {step_titles[label_index]}")
        back_button.setEnabled(bounded > 0)
        next_button.setVisible(bounded < len(WIZARD_STEP_ORDER) - 1)
        start_button.setVisible(bounded == len(WIZARD_STEP_ORDER) - 1)
        set_error(())
        render_dynamic_pages()

    def go_next():
        errors = validate_wizard_step(state_from_inputs())
        if errors:
            set_error(errors)
            return
        set_step(current_index["value"] + 1)

    def go_back():
        set_step(current_index["value"] - 1)

    def accept_if_valid():
        try:
            result_request["value"] = build_upload_request_from_wizard_state(state_from_inputs())
        except ValueError as error:
            set_error((str(error),))
            return
        dialog.accept()

    button_row = QtWidgets.QHBoxLayout()
    back_button = QtWidgets.QPushButton("Zurück")
    next_button = QtWidgets.QPushButton("Weiter")
    start_button = QtWidgets.QPushButton("Upload starten")
    cancel_button = QtWidgets.QPushButton("Abbrechen")
    button_row.addWidget(back_button)
    button_row.addStretch(1)
    button_row.addWidget(cancel_button)
    button_row.addWidget(next_button)
    button_row.addWidget(start_button)
    layout.addLayout(button_row)

    back_button.clicked.connect(go_back)
    next_button.clicked.connect(go_next)
    start_button.clicked.connect(accept_if_valid)
    cancel_button.clicked.connect(dialog.reject)
    set_step(0)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return result_request["value"]


def _split_source_paths(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(text or "").splitlines() if line.strip())


def _default_text(defaults, attribute: str) -> str:
    return str(getattr(defaults, attribute, "") or "")


__all__ = ["prompt_new_upload"]
