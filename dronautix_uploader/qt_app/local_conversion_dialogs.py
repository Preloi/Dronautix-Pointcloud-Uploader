"""Qt dialog factories for local Potree conversion."""

from __future__ import annotations

from .path_drop import create_path_drop_line_edit
from .local_conversion_dialog_models import (
    LocalConversionDialogState,
    validate_local_conversion_dialog_state,
)


def prompt_local_conversion(QtWidgets, parent, defaults=None):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Lokale Konvertierung")
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    body = QtWidgets.QLabel("LAS/LAZ lokal mit PotreeConverter in einen Potree-Ordner umwandeln.")
    body.setObjectName("MutedText")
    body.setWordWrap(True)
    layout.addWidget(body)

    form = QtWidgets.QFormLayout()
    source_input = create_path_drop_line_edit(QtWidgets)
    source_input.setPlaceholderText("LAS/LAZ-Datei hierher ziehen oder per Dialog auswählen")
    output_input = QtWidgets.QLineEdit(_default_text(defaults, "output_base_dir"))
    converter_input = QtWidgets.QLineEdit(_default_text(defaults, "converter_path"))
    overwrite_input = QtWidgets.QCheckBox("Bestehenden Ausgabeordner überschreiben")
    form.addRow("Quelldatei", source_input)
    form.addRow("Ausgabeordner", output_input)
    form.addRow("Potree Converter", converter_input)
    form.addRow("", overwrite_input)
    layout.addLayout(form)

    browse_row = QtWidgets.QHBoxLayout()
    source_button = QtWidgets.QPushButton("Quelldatei")
    output_button = QtWidgets.QPushButton("Zielordner")
    converter_button = QtWidgets.QPushButton("Converter")
    browse_row.addWidget(source_button)
    browse_row.addWidget(output_button)
    browse_row.addWidget(converter_button)
    browse_row.addStretch(1)
    layout.addLayout(browse_row)

    error_label = QtWidgets.QLabel("")
    error_label.setObjectName("ErrorText")
    error_label.setWordWrap(True)
    error_label.hide()
    layout.addWidget(error_label)

    def browse_source():
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            dialog,
            "LAS/LAZ-Datei auswählen",
            "",
            "Punktwolken (*.las *.laz);;Alle Dateien (*)",
        )
        if path:
            source_input.setText(path)

    def browse_output():
        path = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Potree-Ausgabeordner auswählen")
        if path:
            output_input.setText(path)

    def browse_converter():
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            dialog,
            "PotreeConverter auswählen",
            "",
            "PotreeConverter (*.exe);;Alle Dateien (*)",
        )
        if path:
            converter_input.setText(path)

    source_button.clicked.connect(browse_source)
    output_button.clicked.connect(browse_output)
    converter_button.clicked.connect(browse_converter)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def state_from_inputs():
        return LocalConversionDialogState(
            source_file=source_input.text(),
            output_dir=output_input.text(),
            converter_path=converter_input.text(),
            overwrite=overwrite_input.isChecked(),
        )

    def accept_if_valid():
        try:
            validate_local_conversion_dialog_state(state_from_inputs())
        except (ValueError, FileExistsError) as error:
            error_label.setText(str(error))
            error_label.show()
            return
        dialog.accept()

    buttons.accepted.connect(accept_if_valid)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return validate_local_conversion_dialog_state(state_from_inputs())


def _default_text(defaults, attribute: str) -> str:
    return str(getattr(defaults, attribute, "") or "")


__all__ = ["prompt_local_conversion"]
