"""Qt dialog factories for settings editing."""

from __future__ import annotations

from .dashboard_settings_model import (
    UPDATE_CHANNEL_MANUAL,
    UPDATE_CHANNEL_PREVIEW,
    UPDATE_CHANNEL_STABLE,
)
from .settings_controller import SettingsFormState


def prompt_settings(QtWidgets, parent, state: SettingsFormState):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Einstellungen bearbeiten")
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    body = QtWidgets.QLabel("AWS-Zugang, S3-Bucket, Converter und lokale Ausgabeordner konfigurieren.")
    body.setObjectName("MutedText")
    body.setWordWrap(True)
    layout.addWidget(body)

    form = QtWidgets.QFormLayout()
    access_input = QtWidgets.QLineEdit(state.aws_access_key_id)
    secret_input = QtWidgets.QLineEdit(state.aws_secret_access_key)
    secret_input.setEchoMode(QtWidgets.QLineEdit.Password)
    region_input = QtWidgets.QLineEdit(state.region_name)
    bucket_input = QtWidgets.QLineEdit(state.bucket_name)
    converter_input = QtWidgets.QLineEdit(state.converter_path)
    output_input = QtWidgets.QLineEdit(state.output_base_dir)
    update_channel_input = QtWidgets.QComboBox()
    update_channel_input.addItems([UPDATE_CHANNEL_STABLE, UPDATE_CHANNEL_PREVIEW, UPDATE_CHANNEL_MANUAL])
    update_channel_index = update_channel_input.findText(state.update_channel)
    if update_channel_index >= 0:
        update_channel_input.setCurrentIndex(update_channel_index)
    form.addRow("AWS Access Key", access_input)
    form.addRow("AWS Secret Key", secret_input)
    form.addRow("Region", region_input)
    form.addRow("S3 Bucket", bucket_input)
    form.addRow("Potree Converter", converter_input)
    form.addRow("Output-Ordner", output_input)
    form.addRow("Update-Kanal", update_channel_input)
    layout.addLayout(form)

    browse_row = QtWidgets.QHBoxLayout()
    converter_button = QtWidgets.QPushButton("Converter")
    output_button = QtWidgets.QPushButton("Output")
    browse_row.addWidget(converter_button)
    browse_row.addWidget(output_button)
    browse_row.addStretch(1)
    layout.addLayout(browse_row)

    error_label = QtWidgets.QLabel("")
    error_label.setObjectName("ErrorText")
    error_label.setWordWrap(True)
    error_label.hide()
    layout.addWidget(error_label)

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
        path = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Output-Ordner auswählen")
        if path:
            output_input.setText(path)

    converter_button.clicked.connect(browse_converter)
    output_button.clicked.connect(browse_output)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    def state_from_inputs():
        return SettingsFormState(
            aws_access_key_id=access_input.text(),
            aws_secret_access_key=secret_input.text(),
            region_name=region_input.text(),
            bucket_name=bucket_input.text(),
            converter_path=converter_input.text(),
            output_base_dir=output_input.text(),
            update_channel=update_channel_input.currentText(),
        )

    def accept_if_valid():
        current = state_from_inputs()
        if not current.region_name.strip() or not current.bucket_name.strip():
            error_label.setText("Region und S3 Bucket sind erforderlich.")
            error_label.show()
            return
        dialog.accept()

    buttons.accepted.connect(accept_if_valid)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    return state_from_inputs()


__all__ = ["prompt_settings"]
