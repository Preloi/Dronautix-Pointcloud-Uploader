"""Settings page: AWS access, output folder, update channel and status."""

from __future__ import annotations

from collections.abc import Callable
from ..dashboard_settings_model import SettingsPreview, UPDATE_CHANNELS, example_settings_preview
from ..settings_controller import SettingsFormState
from .widgets import _clear_layout_widgets, _create_settings_status_panel

def create_settings_page(
    QtCore,
    QtWidgets,
    *,
    settings_state: SettingsFormState | None = None,
    settings_state_provider: Callable[[], SettingsFormState] | None = None,
    settings_preview: SettingsPreview | None = None,
    settings_provider: Callable[[], SettingsPreview] | None = None,
    on_settings_action: Callable[..., None] | None = None,
):
    state = _resolve_settings_state(settings_state, settings_state_provider)
    preview = _resolve_settings_preview(settings_preview, settings_provider)

    page = QtWidgets.QWidget()
    page.setObjectName("Page")
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(32, 28, 32, 28)
    root.setSpacing(18)

    header = QtWidgets.QHBoxLayout()
    title_box = QtWidgets.QVBoxLayout()
    title = QtWidgets.QLabel("Einstellungen")
    title.setObjectName("PageTitle")
    subtitle = QtWidgets.QLabel("AWS-Zugang, Ausgabeordner und Updates.")
    subtitle.setObjectName("MutedText")
    title_box.addWidget(title)
    title_box.addWidget(subtitle)
    header.addLayout(title_box, 1)
    root.addLayout(header)

    content = QtWidgets.QHBoxLayout()
    content.setSpacing(18)
    root.addLayout(content, 1)

    form_panel = QtWidgets.QFrame()
    form_panel.setObjectName("DetailPanel")
    form_root = QtWidgets.QVBoxLayout(form_panel)
    form_root.setContentsMargins(20, 20, 20, 20)
    form_root.setSpacing(14)

    form_title = QtWidgets.QLabel("Konfiguration")
    form_title.setObjectName("PanelTitle")
    form_root.addWidget(form_title)

    form = QtWidgets.QFormLayout()
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)

    access_input = QtWidgets.QLineEdit()
    secret_input = QtWidgets.QLineEdit()
    secret_input.setEchoMode(QtWidgets.QLineEdit.Password)
    # Editable: common regions to pick from, any valid region can be typed.
    region_input = QtWidgets.QComboBox()
    region_input.setEditable(True)
    region_input.addItems(list(COMMON_AWS_REGIONS))
    region_input.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
    bucket_input = QtWidgets.QLineEdit()
    output_input = QtWidgets.QLineEdit()
    update_channel_input = QtWidgets.QComboBox()
    access_input.setObjectName("AwsAccessInput")
    secret_input.setObjectName("AwsSecretInput")
    region_input.setObjectName("AwsRegionInput")
    bucket_input.setObjectName("S3BucketInput")
    output_input.setObjectName("OutputDirInput")
    update_channel_input.setObjectName("UpdateChannelInput")
    update_channel_input.addItems(list(UPDATE_CHANNELS))

    form.addRow("AWS Access Key", access_input)
    form.addRow("AWS Secret Key", secret_input)
    output_row = QtWidgets.QHBoxLayout()
    output_row.setSpacing(8)
    output_row.addWidget(output_input, 1)
    output_button = QtWidgets.QPushButton("...")
    output_button.setObjectName("ActionButton")
    output_button.setToolTip("Output-Ordner auswählen")
    output_button.setAccessibleName("Output-Ordner auswählen")
    output_row.addWidget(output_button)

    form.addRow("Region", region_input)
    form.addRow("S3 Bucket", bucket_input)
    form.addRow("Output-Ordner", output_row)
    form.addRow("Updates", update_channel_input)
    form_root.addLayout(form)

    action_row = QtWidgets.QHBoxLayout()
    action_row.setSpacing(10)
    save_button = QtWidgets.QPushButton("Speichern")
    save_button.setObjectName("PrimaryButton")
    test_button = QtWidgets.QPushButton("Verbindung testen")
    test_button.setObjectName("ActionButton")
    update_button = QtWidgets.QPushButton("Update prüfen")
    update_button.setObjectName("ActionButton")
    reload_button = QtWidgets.QPushButton("Neu laden")
    reload_button.setObjectName("ActionButton")
    reload_button.setToolTip("Gespeicherte Einstellungen erneut laden (F5)")
    clear_credentials_button = QtWidgets.QPushButton("Zugangsdaten entfernen")
    clear_credentials_button.setObjectName("ActionButton")
    clear_credentials_button.setToolTip("AWS-Schlüssel aus dem Windows-Anmeldeinformationsspeicher entfernen")
    # Two rows: five buttons in one row were clipped below ~1400 px.
    action_row.addWidget(save_button)
    action_row.addWidget(test_button)
    action_row.addWidget(update_button)
    action_row.addStretch(1)
    form_root.addLayout(action_row)
    secondary_action_row = QtWidgets.QHBoxLayout()
    secondary_action_row.setSpacing(10)
    secondary_action_row.addWidget(reload_button)
    secondary_action_row.addStretch(1)
    secondary_action_row.addWidget(clear_credentials_button)
    form_root.addLayout(secondary_action_row)

    hint = QtWidgets.QLabel("Der integrierte PotreeConverter wird automatisch verwendet.")
    hint.setObjectName("MutedText")
    hint.setWordWrap(True)
    form_root.addWidget(hint)
    form_root.addStretch(1)
    content.addWidget(form_panel, 2)

    status_container = QtWidgets.QVBoxLayout()
    status_container.setContentsMargins(0, 0, 0, 0)
    status_container.setSpacing(12)
    content.addLayout(status_container, 1)

    def apply_state_to_inputs(selected_state: SettingsFormState):
        access_input.setText(selected_state.aws_access_key_id)
        secret_input.setText(selected_state.aws_secret_access_key)
        region_input.setCurrentText(selected_state.region_name)
        bucket_input.setText(selected_state.bucket_name)
        output_input.setText(selected_state.output_base_dir)
        channel_index = update_channel_input.findText(selected_state.update_channel)
        update_channel_input.setCurrentIndex(channel_index if channel_index >= 0 else 0)

    def state_from_inputs() -> SettingsFormState:
        return SettingsFormState(
            aws_access_key_id=access_input.text(),
            aws_secret_access_key=secret_input.text(),
            region_name=region_input.currentText(),
            bucket_name=bucket_input.text(),
            converter_path=state.converter_path,
            output_base_dir=output_input.text(),
            update_channel=update_channel_input.currentText(),
        )

    def browse_output():
        path = QtWidgets.QFileDialog.getExistingDirectory(page, "Output-Ordner auswählen")
        if path:
            output_input.setText(path)

    def dispatch_settings_action(action_id: str, payload=None):
        if on_settings_action is None:
            return
        if payload is None:
            on_settings_action(action_id)
            return
        on_settings_action(action_id, payload)

    def render_settings():
        nonlocal state
        nonlocal preview
        state = _resolve_settings_state(settings_state, settings_state_provider)
        preview = _resolve_settings_preview(settings_preview, settings_provider)
        apply_state_to_inputs(state)
        _clear_layout_widgets(status_container)
        status_container.addWidget(_create_settings_status_panel(QtWidgets, "Status", preview.settings_status))
        status_container.addStretch(1)

    def has_unsaved_changes() -> bool:
        current = state_from_inputs()
        fields = ("aws_access_key_id", "aws_secret_access_key", "region_name", "bucket_name", "output_base_dir", "update_channel")
        return any(
            str(getattr(current, field) or "").strip() != str(getattr(state, field) or "").strip()
            for field in fields
        )

    def reload_settings_confirming_discard():
        if has_unsaved_changes():
            answer = QtWidgets.QMessageBox.question(
                page,
                "Einstellungen neu laden",
                "Die Einstellungen enthalten ungespeicherte Änderungen.\n\nÄnderungen verwerfen und neu laden?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return False
        render_settings()
        return True

    output_button.clicked.connect(browse_output)
    save_button.clicked.connect(lambda checked=False: dispatch_settings_action("save", state_from_inputs()))
    test_button.clicked.connect(lambda checked=False: dispatch_settings_action("test_connection", state_from_inputs()))
    update_button.clicked.connect(lambda checked=False: dispatch_settings_action("check_update"))
    reload_button.clicked.connect(lambda checked=False: reload_settings_confirming_discard())
    clear_credentials_button.clicked.connect(lambda checked=False: dispatch_settings_action("clear_credentials"))
    for button in (save_button, test_button, update_button, clear_credentials_button):
        button.setEnabled(on_settings_action is not None)

    render_settings()
    # F5 / "Neu laden" ask before discarding edits; after a successful save the
    # main window re-renders unconditionally via ``render_saved_settings``.
    page.reload_settings = reload_settings_confirming_discard
    page.render_saved_settings = render_settings
    page.has_unsaved_changes = has_unsaved_changes
    return page


COMMON_AWS_REGIONS = (
    "eu-central-1",
    "eu-central-2",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eu-north-1",
    "eu-south-1",
    "us-east-1",
    "us-east-2",
    "us-west-2",
)


def _resolve_settings_preview(
    settings_preview: SettingsPreview | None = None,
    settings_provider: Callable[[], SettingsPreview] | None = None,
) -> SettingsPreview:
    if settings_preview is not None:
        return settings_preview
    if settings_provider is not None:
        return settings_provider()
    return example_settings_preview()


def _resolve_settings_state(
    settings_state: SettingsFormState | None = None,
    settings_state_provider: Callable[[], SettingsFormState] | None = None,
) -> SettingsFormState:
    if settings_state is not None:
        return settings_state
    if settings_state_provider is not None:
        return settings_state_provider()
    return SettingsFormState()
