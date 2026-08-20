"""Page factories for the QtWidgets app."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import inspect
import json
import os

from .activity_model import (
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
    normalize_progress_value,
)
from .dashboard_settings_model import (
    SettingsPreview,
    UPDATE_CHANNELS,
    example_settings_preview,
    settings_status_action_id,
    status_level_label,
)
from dronautix_uploader.core.crs_detection import detect_pointcloud_crs, normalize_crs_value
from dronautix_uploader.core.crs_service import get_crs_display_value, get_vertical_crs_display_value

from .glb_upload_model import (
    append_unique_glb_paths,
    explicit_glb_model_json_pair,
    format_file_size,
)
from .path_drop import mime_data_paths
from .project_management import (
    ProjectPreview,
    STATUS_ALL,
    STATUS_FILTERS,
    load_project_previews,
    project_datum_sort_key,
    status_filter_accepts,
)
from .project_management_actions import (
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
    ACTION_ADD_POINTCLOUDS,
    ACTION_REMOVE_POINTCLOUD,
    action_by_id,
    is_action_available,
)
from .settings_controller import SettingsFormState
from .upload_wizard_model import (
    source_format_label,
    source_handling_label,
)


UPLOAD_MODE_UPLOAD = "upload"
UPLOAD_MODE_CONVERT = "convert"


@dataclass
class UploadFormInputs:
    mode: str
    customer: str
    project: str
    source_paths: tuple[str, ...]
    converter_path: str
    output_base_dir: str
    horizontal_crs: str
    vertical_crs: str
    overwrite: bool


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
    region_input = QtWidgets.QLineEdit()
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
    form.addRow("Region", region_input)
    form.addRow("S3 Bucket", bucket_input)
    form.addRow("Output-Ordner", output_input)
    form.addRow("Updates", update_channel_input)
    form_root.addLayout(form)

    browse_row = QtWidgets.QHBoxLayout()
    browse_row.setSpacing(10)
    output_button = QtWidgets.QPushButton("Output wählen")
    output_button.setObjectName("ActionButton")
    browse_row.addWidget(output_button)
    browse_row.addStretch(1)
    form_root.addLayout(browse_row)

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
    action_row.addWidget(save_button)
    action_row.addWidget(test_button)
    action_row.addWidget(update_button)
    action_row.addWidget(reload_button)
    action_row.addStretch(1)
    form_root.addLayout(action_row)

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
        region_input.setText(selected_state.region_name)
        bucket_input.setText(selected_state.bucket_name)
        output_input.setText(selected_state.output_base_dir)
        channel_index = update_channel_input.findText(selected_state.update_channel)
        update_channel_input.setCurrentIndex(channel_index if channel_index >= 0 else 0)

    def state_from_inputs() -> SettingsFormState:
        return SettingsFormState(
            aws_access_key_id=access_input.text(),
            aws_secret_access_key=secret_input.text(),
            region_name=region_input.text(),
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

    output_button.clicked.connect(browse_output)
    save_button.clicked.connect(lambda checked=False: dispatch_settings_action("save", state_from_inputs()))
    test_button.clicked.connect(lambda checked=False: dispatch_settings_action("test_connection", state_from_inputs()))
    update_button.clicked.connect(lambda checked=False: dispatch_settings_action("check_update"))
    reload_button.clicked.connect(lambda checked=False: render_settings())
    for button in (save_button, test_button, update_button):
        button.setEnabled(on_settings_action is not None)

    render_settings()
    page.reload_settings = render_settings
    return page


def create_upload_page(
    QtCore,
    QtWidgets,
    *,
    on_start: Callable[[], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
    defaults_provider: Callable[[], object] | None = None,
):
    """Single-screen upload + local conversion form (no modal, no stepper)."""

    state = {
        "mode": UPLOAD_MODE_UPLOAD,
        "running": False,
        "sources": [],
        "detected_crs": {},
        "models": [],
        "model_sidecars": {},
        "model_results": {},
    }

    page = QtWidgets.QWidget()
    page.setObjectName("Page")
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(32, 28, 32, 28)
    root.setSpacing(16)

    # --- Header with mode toggle -------------------------------------------
    header = QtWidgets.QHBoxLayout()
    title_box = QtWidgets.QVBoxLayout()
    title = QtWidgets.QLabel("Upload")
    title.setObjectName("PageTitle")
    subtitle = QtWidgets.QLabel("Punktwolken konvertieren und zu S3 hochladen.")
    subtitle.setObjectName("MutedText")
    title_box.addWidget(title)
    title_box.addWidget(subtitle)
    header.addLayout(title_box, 1)

    mode_upload_button = QtWidgets.QPushButton("Hochladen")
    mode_upload_button.setObjectName("ActionButton")
    mode_upload_button.setCheckable(True)
    mode_upload_button.setChecked(True)
    mode_upload_button.setCursor(QtCore.Qt.PointingHandCursor)
    mode_upload_button.setToolTip("Punktwolken konvertieren und zu S3 hochladen")
    mode_convert_button = QtWidgets.QPushButton("Nur konvertieren")
    mode_convert_button.setObjectName("ActionButton")
    mode_convert_button.setCheckable(True)
    mode_convert_button.setCursor(QtCore.Qt.PointingHandCursor)
    mode_convert_button.setToolTip("LAS/LAZ nur lokal in ein Potree-Projekt umwandeln, ohne Upload")
    mode_group = QtWidgets.QButtonGroup(page)
    mode_group.setExclusive(True)
    mode_group.addButton(mode_upload_button)
    mode_group.addButton(mode_convert_button)
    header.addWidget(mode_upload_button)
    header.addWidget(mode_convert_button)
    root.addLayout(header)

    # --- Project card -------------------------------------------------------
    project_panel = QtWidgets.QFrame()
    project_panel.setObjectName("DetailPanel")
    project_layout = QtWidgets.QFormLayout(project_panel)
    project_layout.setContentsMargins(20, 16, 20, 16)
    project_layout.setHorizontalSpacing(18)
    project_layout.setVerticalSpacing(12)
    customer_input = QtWidgets.QLineEdit()
    customer_input.setObjectName("UploadCustomerInput")
    customer_input.setPlaceholderText("z. B. Dronautix")
    project_input = QtWidgets.QLineEdit()
    project_input.setObjectName("UploadProjectInput")
    project_input.setPlaceholderText("z. B. Nord-Aufmass")
    project_row_label = project_layout.labelForField  # noqa: F841 - kept for clarity
    project_layout.addRow("Kunde", customer_input)
    project_layout.addRow("Projekt", project_input)
    root.addWidget(project_panel)

    # --- Sources card -------------------------------------------------------
    sources_panel = QtWidgets.QFrame()
    sources_panel.setObjectName("DetailPanel")
    sources_layout = QtWidgets.QVBoxLayout(sources_panel)
    sources_layout.setContentsMargins(20, 16, 20, 16)
    sources_layout.setSpacing(10)
    sources_header = QtWidgets.QHBoxLayout()
    sources_title = QtWidgets.QLabel("Quellen")
    sources_title.setObjectName("PanelTitle")
    sources_hint = QtWidgets.QLabel("Dateien/Ordner hierher ziehen")
    sources_hint.setObjectName("MutedText")
    sources_header.addWidget(sources_title)
    sources_header.addStretch(1)
    sources_header.addWidget(sources_hint)
    sources_layout.addLayout(sources_header)

    def add_sources(paths):
        if state["running"]:
            return
        single = state["mode"] == UPLOAD_MODE_CONVERT
        cleaned = [str(path).strip() for path in paths if str(path or "").strip()]
        if not cleaned:
            return
        if single:
            state["sources"] = [cleaned[-1]]
        else:
            seen = list(state["sources"])
            for path in cleaned:
                if path not in seen:
                    seen.append(path)
            state["sources"] = seen
        detect_sources_crs()
        render_sources()
        render_models()

    source_handlers = {}
    source_list = _create_source_drop_list(
        QtCore,
        QtWidgets,
        add_sources,
        on_delete=lambda: source_handlers.get("remove", lambda: None)(),
    )
    source_list.setObjectName("UploadSourceList")
    source_list.setMinimumHeight(150)
    source_list.setToolTip("Dateien/Ordner hierher ziehen. Markieren und 'Entf' entfernt Quellen.")
    sources_layout.addWidget(source_list, 1)

    sources_buttons = QtWidgets.QHBoxLayout()
    files_button = QtWidgets.QPushButton("Dateien")
    files_button.setObjectName("ActionButton")
    files_button.setToolTip("Punktwolken-Dateien auswählen")
    folder_button = QtWidgets.QPushButton("Ordner")
    folder_button.setObjectName("ActionButton")
    folder_button.setToolTip("Potree-Ordner auswählen")
    remove_button = QtWidgets.QPushButton("Entfernen")
    remove_button.setObjectName("ActionButton")
    remove_button.setToolTip("Markierte Quellen entfernen (Entf)")
    sources_buttons.addWidget(files_button)
    sources_buttons.addWidget(folder_button)
    sources_buttons.addWidget(remove_button)
    sources_buttons.addStretch(1)
    sources_count = QtWidgets.QLabel("Keine Quelle")
    sources_count.setObjectName("MutedText")
    sources_buttons.addWidget(sources_count)
    sources_layout.addLayout(sources_buttons)
    root.addWidget(sources_panel, 1)

    # --- Optional GLB models ----------------------------------------------
    models_panel = QtWidgets.QFrame()
    models_panel.setObjectName("UploadModelsPanel")
    models_layout = QtWidgets.QVBoxLayout(models_panel)
    models_layout.setContentsMargins(20, 16, 20, 16)
    models_layout.setSpacing(10)
    models_header = QtWidgets.QHBoxLayout()
    models_title = QtWidgets.QLabel("3D-Modelle (optional)")
    models_title.setObjectName("PanelTitle")
    models_hint = QtWidgets.QLabel("Nur GLB · nativ X=Ost, Y=Nord, Z=Höhe (m)")
    models_hint.setObjectName("MutedText")
    models_header.addWidget(models_title)
    models_header.addStretch(1)
    models_header.addWidget(models_hint)
    models_layout.addLayout(models_header)

    model_handlers = {}

    def model_key(path: str) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def add_models(paths):
        if state["running"]:
            return
        try:
            sidecar_pair = explicit_glb_model_json_pair(paths)
        except ValueError as error:
            show_error(str(error))
            return
        if sidecar_pair is not None:
            model_path, sidecar_path = sidecar_pair
            state["models"] = list(append_unique_glb_paths(state["models"], (model_path,)))
            selected_model_path = next(path for path in state["models"] if model_key(path) == model_key(model_path))
            state["model_sidecars"][model_key(selected_model_path)] = sidecar_path
            render_models()
            return
        unsupported = [
            str(path)
            for path in paths
            if str(path or "").strip() and not str(path).strip().lower().endswith(".glb")
        ]
        state["models"] = list(append_unique_glb_paths(state["models"], paths))
        if unsupported:
            show_error("3D-Modelle müssen das Format .glb haben.")
        render_models()

    model_list = _create_source_drop_list(
        QtCore,
        QtWidgets,
        add_models,
        on_delete=lambda: model_handlers.get("remove", lambda: None)(),
    )
    model_list.setObjectName("UploadModelList")
    model_list.setMinimumHeight(104)
    model_list.setToolTip("GLB-Dateien hierher ziehen. Markieren und 'Entf' entfernt Modelle.")
    models_layout.addWidget(model_list)

    models_buttons = QtWidgets.QHBoxLayout()
    model_files_button = QtWidgets.QPushButton("Dateien")
    model_files_button.setObjectName("ActionButton")
    model_files_button.setToolTip("GLB-Modelle auswählen")
    model_sidecar_button = QtWidgets.QPushButton("Sidecar")
    model_sidecar_button.setObjectName("UploadModelSidecarButton")
    model_sidecar_button.setToolTip("Für genau ein markiertes GLB ein explizites model.json zuordnen")
    model_remove_button = QtWidgets.QPushButton("Entfernen")
    model_remove_button.setObjectName("ActionButton")
    model_remove_button.setToolTip("Markierte Modelle entfernen (Entf)")
    model_count = QtWidgets.QLabel("Keine Modelle")
    model_count.setObjectName("UploadModelCount")
    models_buttons.addWidget(model_files_button)
    models_buttons.addWidget(model_sidecar_button)
    models_buttons.addWidget(model_remove_button)
    models_buttons.addStretch(1)
    models_buttons.addWidget(model_count)
    models_layout.addLayout(models_buttons)

    root.addWidget(models_panel)

    # --- Advanced (collapsible) --------------------------------------------
    advanced_toggle = QtWidgets.QToolButton()
    advanced_toggle.setObjectName("AdvancedToggle")
    advanced_toggle.setText("Erweitert (CRS, Ausgabeordner)")
    advanced_toggle.setCheckable(True)
    advanced_toggle.setCursor(QtCore.Qt.PointingHandCursor)
    advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    advanced_toggle.setArrowType(QtCore.Qt.RightArrow)
    root.addWidget(advanced_toggle)

    advanced_panel = QtWidgets.QFrame()
    advanced_panel.setObjectName("DetailPanel")
    advanced_form = QtWidgets.QFormLayout(advanced_panel)
    advanced_form.setContentsMargins(20, 16, 20, 16)
    advanced_form.setHorizontalSpacing(18)
    advanced_form.setVerticalSpacing(12)
    horizontal_crs_input = QtWidgets.QLineEdit()
    horizontal_crs_input.setPlaceholderText("automatisch erkennen")
    vertical_crs_input = QtWidgets.QLineEdit()
    vertical_crs_input.setPlaceholderText("optional")
    output_input = QtWidgets.QLineEdit()
    overwrite_input = QtWidgets.QCheckBox("Bestehende Potree-Ausgabe überschreiben")
    output_row = QtWidgets.QHBoxLayout()
    output_browse = QtWidgets.QPushButton("...")
    output_browse.setObjectName("ActionButton")
    output_browse.setMaximumWidth(40)
    output_browse.setToolTip("Ausgabeordner auswählen")
    output_row.addWidget(output_input, 1)
    output_row.addWidget(output_browse)
    advanced_form.addRow("Horizontales CRS", horizontal_crs_input)
    advanced_form.addRow("Vertikales CRS", vertical_crs_input)
    output_row_label = QtWidgets.QLabel("Ausgabeordner")
    advanced_form.addRow(output_row_label, output_row)
    advanced_form.addRow("", overwrite_input)
    converter_hint = QtWidgets.QLabel("Der integrierte PotreeConverter wird automatisch verwendet.")
    converter_hint.setObjectName("MutedText")
    converter_hint.setWordWrap(True)
    advanced_form.addRow("", converter_hint)
    advanced_panel.setVisible(False)
    root.addWidget(advanced_panel)

    def set_output_row_visible(visible: bool):
        # Upload uses a temporary folder, so the manual output folder only matters
        # in "Nur konvertieren" mode.
        if hasattr(advanced_form, "setRowVisible"):
            advanced_form.setRowVisible(output_row, visible)
        else:
            output_row_label.setVisible(visible)
            output_input.setVisible(visible)
            output_browse.setVisible(visible)

    def toggle_advanced(checked):
        advanced_panel.setVisible(checked)
        advanced_toggle.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)

    advanced_toggle.toggled.connect(toggle_advanced)

    # --- Action row + inline progress --------------------------------------
    error_label = QtWidgets.QLabel("")
    error_label.setObjectName("ErrorText")
    error_label.setWordWrap(True)
    error_label.hide()
    root.addWidget(error_label)

    status_line = QtWidgets.QLabel("")
    status_line.setObjectName("UploadStatusLine")
    status_line.setWordWrap(True)
    status_line.hide()
    root.addWidget(status_line)

    phase_panel = QtWidgets.QFrame()
    phase_panel.setObjectName("UploadPhasePanel")
    phase_layout = QtWidgets.QGridLayout(phase_panel)
    phase_layout.setContentsMargins(16, 12, 16, 12)
    phase_layout.setHorizontalSpacing(12)
    phase_layout.setVerticalSpacing(8)
    phase_bars = {}
    phase_statuses = {}
    phase_rows = {}
    phase_specs = (
        ("preparation", "Vorbereitung", "UploadPreparationProgress"),
        ("conversion", "Konvertierung", "UploadConversionProgress"),
        ("optimization", "Modelle optimieren", "UploadModelOptimizationProgress"),
        ("upload", "Upload", "UploadTransferProgress"),
        ("index", "Projekt speichern", "UploadIndexProgress"),
    )
    for row, (phase, label_text, object_name) in enumerate(phase_specs):
        label = QtWidgets.QLabel(label_text)
        bar = QtWidgets.QProgressBar()
        bar.setObjectName(object_name)
        bar.setProperty("role", "UploadPhaseProgress")
        bar.setTextVisible(True)
        status = QtWidgets.QLabel("Wartet")
        status.setObjectName("MutedText")
        status.setMinimumWidth(100)
        phase_layout.addWidget(label, row, 0)
        phase_layout.addWidget(bar, row, 1)
        phase_layout.addWidget(status, row, 2)
        phase_bars[phase] = bar
        phase_statuses[phase] = status
        phase_rows[phase] = (label, bar, status)
    phase_layout.setColumnStretch(1, 1)
    phase_panel.hide()
    root.addWidget(phase_panel)

    action_row = QtWidgets.QHBoxLayout()
    progress_bar = QtWidgets.QProgressBar()
    progress_bar.setObjectName("UploadProgress")
    progress_bar.setTextVisible(True)
    progress_bar.hide()
    action_row.addWidget(progress_bar, 1)
    cancel_button = QtWidgets.QPushButton("Abbrechen")
    cancel_button.setObjectName("ActionButton")
    cancel_button.setCursor(QtCore.Qt.PointingHandCursor)
    cancel_button.setToolTip("Laufenden Vorgang abbrechen; bereits hochgeladene Dateien werden entfernt")
    cancel_button.hide()
    action_row.addWidget(cancel_button)
    start_button = QtWidgets.QPushButton("Hochladen")
    start_button.setObjectName("PrimaryButton")
    start_button.setMinimumWidth(160)
    start_button.setCursor(QtCore.Qt.PointingHandCursor)
    start_button.setEnabled(on_start is not None)
    action_row.addWidget(start_button)
    root.addLayout(action_row)

    log_view = QtWidgets.QPlainTextEdit()
    log_view.setObjectName("UploadLogView")
    log_view.setReadOnly(True)
    log_view.setMinimumHeight(120)
    log_view.hide()
    root.addWidget(log_view)

    # --- Behaviour ----------------------------------------------------------
    def current_defaults():
        if defaults_provider is None:
            return None
        try:
            return defaults_provider()
        except Exception:
            return None

    def detect_sources_crs():
        for path in state["sources"]:
            if path in state["detected_crs"]:
                continue
            try:
                state["detected_crs"][path] = detect_pointcloud_crs(path) or {}
            except Exception:
                state["detected_crs"][path] = {}

    def crs_display_for_path(path: str) -> str:
        manual_horizontal = horizontal_crs_input.text().strip()
        manual_vertical = vertical_crs_input.text().strip()
        if manual_horizontal:
            return f"{manual_horizontal}{' / ' + manual_vertical if manual_vertical else ''} (manuell)"
        detected = state["detected_crs"].get(path)
        if path not in state["detected_crs"]:
            return "wird erkannt..."
        if detected:
            horizontal = get_crs_display_value(detected)
            vertical = get_vertical_crs_display_value(detected)
            if horizontal:
                return f"{horizontal}{' / ' + vertical if vertical else ''}"
        return "nicht erkannt"

    def crs_info_for_path(path: str):
        detected = state["detected_crs"].get(path)
        info = dict(detected) if isinstance(detected, dict) and detected else {}
        manual_horizontal = horizontal_crs_input.text().strip()
        manual_vertical = vertical_crs_input.text().strip()
        if manual_horizontal:
            manual_info = normalize_crs_value(manual_horizontal, source="manual") or {}
            info.update(manual_info)
        if manual_vertical:
            vertical_value = f"EPSG:{manual_vertical}" if manual_vertical.isdigit() else manual_vertical
            info["vertical_crs"] = vertical_value
            info["vertical_epsg"] = vertical_value
            info["vertical_projection"] = vertical_value
        return info or None

    def crs_info_by_source_path():
        result = {}
        for path in state["sources"]:
            info = crs_info_for_path(path)
            if info:
                result[path] = info
        return result

    def project_crs_info():
        for path in state["sources"]:
            info = crs_info_for_path(path)
            if info and (info.get("value") or info.get("projection")):
                return info
        return {}

    def model_placement_status() -> str:
        return "Georeferenzierung aus GLB"

    def render_models():
        model_list.clear()
        for path in state["models"]:
            name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or path
            status = model_placement_status()
            result = state["model_results"].get(path, {})
            optimization_status = str(result.get("optimization_status") or "bereit")
            output_size = result.get("output_size")
            result_size = f"{int(output_size)} Bytes" if output_size is not None else "wird ermittelt"
            sidecar_path = state["model_sidecars"].get(model_key(path), "")
            sidecar = "model.json" if sidecar_path else "keiner"
            item = QtWidgets.QListWidgetItem(
                f"{name}   ·   {format_file_size(path)}   ·   Platzierung: {status}   ·   "
                f"Sidecar: {sidecar}   ·   Optimierung: {optimization_status}   ·   Ergebnisgröße: {result_size}"
            )
            item.setData(QtCore.Qt.UserRole, path)
            item.setToolTip(path)
            model_list.addItem(item)
        count = len(state["models"])
        model_count.setText("Keine Modelle" if count == 0 else ("1 Modell" if count == 1 else f"{count} Modelle"))

    def remove_selected_models():
        if state["running"]:
            return
        selected = {item.data(QtCore.Qt.UserRole) for item in model_list.selectedItems()}
        if not selected:
            return
        state["models"] = [path for path in state["models"] if path not in selected]
        for path in selected:
            state["model_results"].pop(path, None)
            state["model_sidecars"].pop(model_key(path), None)
        render_models()

    model_handlers["remove"] = remove_selected_models

    def browse_model_files():
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            page,
            "GLB-Modelle auswählen",
            "",
            "GLB-Modelle (*.glb);;Alle Dateien (*)",
        )
        add_models(paths)

    def browse_model_sidecar():
        selected = model_list.selectedItems()
        if len(selected) != 1:
            show_error("Bitte genau ein GLB markieren, bevor ein model.json-Sidecar zugeordnet wird.")
            return
        model_path = str(selected[0].data(QtCore.Qt.UserRole) or "")
        sidecar_path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            page,
            "model.json für das markierte GLB auswählen",
            "",
            "model.json (model.json);;JSON-Dateien (*.json);;Alle Dateien (*)",
        )
        if not sidecar_path:
            return
        try:
            _model_path, verified_sidecar_path = explicit_glb_model_json_pair((model_path, sidecar_path)) or ("", "")
        except ValueError as error:
            show_error(str(error))
            return
        if not verified_sidecar_path:
            show_error("Nur eine Datei mit dem Namen model.json kann als Sidecar zugeordnet werden.")
            return
        state["model_sidecars"][model_key(model_path)] = verified_sidecar_path
        render_models()

    def build_model_inputs():
        """Build native-GLB inputs; coordinates and CRS stay entirely in the data."""

        if state["mode"] != UPLOAD_MODE_UPLOAD:
            return ()
        from dronautix_uploader.core.contracts import ModelUploadInput

        if not state["models"]:
            return ()

        project_crs = project_crs_info()
        horizontal = str(project_crs.get("value") or project_crs.get("projection") or "")
        vertical = str(project_crs.get("vertical_crs") or project_crs.get("vertical_epsg") or "")
        if not horizontal or not vertical:
            raise ValueError("Projekt-CRS und Höhenbezug der Punktwolke sind für 3D-Modelle erforderlich.")
        return tuple(
            ModelUploadInput(source_path=path, model_json_path=state["model_sidecars"].get(model_key(path), ""))
            for path in state["models"]
        )

    def render_sources():
        source_list.clear()
        for path in state["sources"]:
            fmt = source_format_label(path)
            handling = source_handling_label(path)
            crs = crs_display_for_path(path)
            name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or path
            item = QtWidgets.QListWidgetItem(f"{name}   ·   {fmt} → {handling}   ·   CRS: {crs}")
            item.setData(QtCore.Qt.UserRole, path)
            item.setToolTip(path)
            source_list.addItem(item)
        count = len(state["sources"])
        sources_count.setText("Keine Quelle" if count == 0 else ("1 Quelle" if count == 1 else f"{count} Quellen"))

    def remove_selected_sources():
        if state["running"]:
            return
        selected = {item.data(QtCore.Qt.UserRole) for item in source_list.selectedItems()}
        if not selected:
            return
        state["sources"] = [path for path in state["sources"] if path not in selected]
        render_sources()
        render_models()

    source_handlers["remove"] = remove_selected_sources

    def browse_files():
        if state["mode"] == UPLOAD_MODE_CONVERT:
            path, _f = QtWidgets.QFileDialog.getOpenFileName(
                page, "LAS/LAZ-Datei auswählen", "", "Punktwolken (*.las *.laz);;Alle Dateien (*)"
            )
            add_sources([path] if path else [])
            return
        paths, _f = QtWidgets.QFileDialog.getOpenFileNames(
            page, "Punktwolken auswählen", "", "Punktwolken (*.las *.laz *.copc.laz);;Alle Dateien (*)"
        )
        add_sources(paths)

    def browse_folder():
        path = QtWidgets.QFileDialog.getExistingDirectory(page, "Ordner auswählen")
        add_sources([path] if path else [])

    def browse_output():
        path = QtWidgets.QFileDialog.getExistingDirectory(page, "Ausgabeordner auswählen")
        if path:
            output_input.setText(path)

    def prefill_advanced_defaults():
        defaults = current_defaults()
        if not output_input.text().strip():
            output_input.setText(str(getattr(defaults, "output_base_dir", "") or ""))

    def resolved_converter_path():
        return str(getattr(current_defaults(), "converter_path", "") or "")

    def apply_mode(mode):
        state["mode"] = mode
        is_convert = mode == UPLOAD_MODE_CONVERT
        customer_input.setEnabled(not is_convert)
        project_input.setEnabled(not is_convert)
        vertical_crs_input.setEnabled(not is_convert)
        set_output_row_visible(is_convert)
        start_button.setText("Konvertieren" if is_convert else "Hochladen")
        subtitle.setText(
            "LAS/LAZ lokal in ein Potree-Projekt konvertieren, ohne Upload."
            if is_convert
            else "Punktwolken konvertieren und zu S3 hochladen."
        )
        if is_convert and len(state["sources"]) > 1:
            state["sources"] = state["sources"][-1:]
        models_panel.setVisible(not is_convert)
        render_sources()
        render_models()

    def read_form() -> UploadFormInputs:
        return UploadFormInputs(
            mode=state["mode"],
            customer=customer_input.text(),
            project=project_input.text(),
            source_paths=tuple(state["sources"]),
            converter_path=resolved_converter_path(),
            output_base_dir=output_input.text(),
            horizontal_crs=horizontal_crs_input.text(),
            vertical_crs=vertical_crs_input.text(),
            overwrite=overwrite_input.isChecked(),
        )

    def show_error(message: str):
        if not message:
            error_label.hide()
            error_label.setText("")
            return
        error_label.setText(message)
        error_label.show()

    def set_running(running: bool):
        state["running"] = running
        for widget in (
            mode_upload_button,
            mode_convert_button,
            customer_input,
            project_input,
            source_list,
            files_button,
            folder_button,
            remove_button,
            model_list,
            model_files_button,
            model_sidecar_button,
            model_remove_button,
            output_input,
            horizontal_crs_input,
            vertical_crs_input,
            overwrite_input,
            output_browse,
            start_button,
        ):
            widget.setEnabled(not running)
        if running:
            show_error("")
            set_status("Wird vorbereitet...")
            is_upload = state["mode"] == UPLOAD_MODE_UPLOAD
            needs_conversion = any(
                str(path).lower().endswith((".las", ".laz"))
                and not str(path).lower().endswith(".copc.laz")
                for path in state["sources"]
            )
            required_phases = {"conversion"} if not is_upload else {"preparation", "upload", "index"}
            if is_upload and needs_conversion:
                required_phases.add("conversion")
            if is_upload and state["models"]:
                required_phases.add("optimization")
            for phase, widgets in phase_rows.items():
                visible = is_upload or phase == "conversion"
                for widget in widgets:
                    widget.setVisible(visible)
                bar = phase_bars[phase]
                bar.setRange(0, 100)
                if visible and phase not in required_phases:
                    bar.setValue(100)
                    bar.setFormat("Nicht erforderlich")
                    phase_statuses[phase].setText("Übersprungen")
                else:
                    bar.setValue(0)
                    bar.setFormat("Wartet...")
                    phase_statuses[phase].setText("Wartet")
            phase_panel.show()
            progress_bar.setRange(0, 0)
            progress_bar.setFormat("Wird vorbereitet...")
            progress_bar.show()
            cancel_button.setEnabled(on_cancel is not None)
            cancel_button.setVisible(on_cancel is not None)
            log_view.clear()
            log_view.show()
        else:
            start_button.setEnabled(on_start is not None)
            progress_bar.hide()
            phase_panel.hide()
            cancel_button.hide()

    def set_status(text: str):
        text = str(text or "").strip()
        if text:
            status_line.setText(text)
            status_line.show()
        else:
            status_line.hide()
            status_line.setText("")

    def append_log(text: str):
        line = str(text or "")
        if line:
            log_view.show()
            log_view.appendPlainText(line)

    def handle_progress(event):
        message = str(getattr(event, "message", "") or "")
        kind = str(getattr(event, "kind", "") or "")
        step = getattr(event, "step", None)
        total = getattr(event, "total_steps", None)
        if message:
            log_view.appendPlainText(message)
            # Show high-level phase messages prominently; skip noisy converter detail lines.
            if not message.startswith("[POTREE]"):
                if step is not None and total:
                    set_status(f"{message} ({int(step)}/{int(total)})")
                else:
                    set_status(message)
        percent = getattr(event, "percent", None)
        step = getattr(event, "step", None)
        total = getattr(event, "total_steps", None)
        phase = str(getattr(event, "phase", "") or "")
        detail = str(getattr(event, "detail", "") or "")
        if phase == "optimization" and detail.startswith("{"):
            try:
                model_result = json.loads(detail)
            except json.JSONDecodeError:
                model_result = None
            if isinstance(model_result, dict):
                model_path = str(model_result.get("model_path") or "")
                if model_path in state["models"]:
                    state["model_results"][model_path] = model_result
                    render_models()
        if phase in phase_bars:
            phase_bar = phase_bars[phase]
            phase_status = phase_statuses[phase]
            if percent is not None:
                value = normalize_progress_value(percent)
                phase_bar.setRange(0, 100)
                phase_bar.setValue(value)
                phase_bar.setFormat("%p%")
                phase_status.setText("Fertig" if value >= 100 else "Läuft")
            elif step is not None and total:
                phase_bar.setRange(0, int(total))
                phase_bar.setValue(max(0, min(int(total), int(step))))
                phase_bar.setFormat(f"{int(step)}/{int(total)}")
                phase_status.setText("Läuft")
            elif message:
                phase_bar.setRange(0, 0)
                phase_bar.setFormat("Läuft...")
                phase_status.setText("Läuft")
        if percent is not None:
            progress_bar.setRange(0, 100)
            progress_bar.setValue(normalize_progress_value(percent))
            progress_bar.setFormat("%p%")
        elif step is not None and total:
            progress_bar.setRange(0, int(total))
            progress_bar.setValue(max(0, min(int(total), int(step))))
            progress_bar.setFormat(f"{int(step)}/{int(total)}")
        else:
            progress_bar.setRange(0, 0)
            if message:
                progress_bar.setFormat(message[:60])

    def request_cancel():
        if on_cancel is None:
            return
        cancel_button.setEnabled(False)
        set_status("Wird abgebrochen...")
        on_cancel()

    files_button.clicked.connect(browse_files)
    folder_button.clicked.connect(browse_folder)
    remove_button.clicked.connect(remove_selected_sources)
    model_files_button.clicked.connect(browse_model_files)
    model_sidecar_button.clicked.connect(browse_model_sidecar)
    model_remove_button.clicked.connect(remove_selected_models)
    output_browse.clicked.connect(browse_output)
    horizontal_crs_input.textChanged.connect(lambda _text: (render_sources(), render_models()))
    vertical_crs_input.textChanged.connect(lambda _text: (render_sources(), render_models()))
    mode_upload_button.clicked.connect(lambda checked=False: apply_mode(UPLOAD_MODE_UPLOAD))
    mode_convert_button.clicked.connect(lambda checked=False: apply_mode(UPLOAD_MODE_CONVERT))
    start_button.clicked.connect(lambda checked=False: on_start() if on_start else None)
    cancel_button.clicked.connect(lambda checked=False: request_cancel())

    prefill_advanced_defaults()
    set_output_row_visible(False)
    render_sources()
    render_models()

    page.read_form = read_form
    page.set_running = set_running
    page.handle_progress = handle_progress
    page.set_status = set_status
    page.append_log = append_log
    page.show_error = show_error
    page.prefill_advanced_defaults = prefill_advanced_defaults
    page.crs_info_by_source_path = crs_info_by_source_path
    page.model_inputs = build_model_inputs
    page.add_model_paths = add_models
    page.add_source_paths = add_sources
    page.focus_default = lambda: customer_input.setFocus()
    return page


def _create_source_drop_list(
    QtCore,
    QtWidgets,
    on_paths_dropped: Callable[[tuple[str, ...]], None],
    on_delete: Callable[[], None] | None = None,
):
    class SourceDropList(QtWidgets.QListWidget):
        def __init__(self):
            super().__init__()
            self.setAcceptDrops(True)
            self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

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


ProjectProvider = Callable[[], Iterable[ProjectPreview]]
ProjectActionCallback = Callable[..., None]
ActivityProvider = Callable[[], ActivityPreview | Iterable[ActivityLogEntry]]


def create_projects_page(
    QtCore,
    QtGui,
    QtWidgets,
    on_placeholder_action: Callable[[str], None] | None = None,
    *,
    project_previews: Iterable[ProjectPreview] | None = None,
    project_provider: ProjectProvider | None = None,
    on_project_action: ProjectActionCallback | None = None,
):
    projects = _resolve_project_previews(project_previews, project_provider)
    action_callback = on_project_action or on_placeholder_action
    project_role = QtCore.Qt.UserRole + 1
    disabled_role = QtCore.Qt.UserRole + 2
    pointcloud_role = QtCore.Qt.UserRole + 3
    search_role = QtCore.Qt.UserRole + 4
    sort_role = QtCore.Qt.UserRole + 5
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
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

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
            painter.setPen(QtGui.QColor("#2ecc71" if checked else "#e74c3c"))
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
    search.textChanged.connect(proxy_model.setFilterFixedString)
    status_filter.currentTextChanged.connect(proxy_model.set_status)

    table.setModel(proxy_model)
    table.resizeColumnsToContents()
    content.addWidget(table)

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
    status_badge.setObjectName("PreviewBadgeLight")
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
    info_field_keys = ("Kunde", "Format", "Punktwolken", "Erstellt am", "Viewer-Link", "S3-Pfad")
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
        ACTION_REPLACE_ALL_POINTCLOUDS,
        ACTION_ADD_POINTCLOUDS,
        ACTION_REPLACE_SINGLE_POINTCLOUD,
        ACTION_REMOVE_POINTCLOUD,
        ACTION_DUPLICATE,
        ACTION_DOWNLOAD,
        ACTION_DISABLE_LINK,
        ACTION_ENABLE_LINK,
        ACTION_DELETE,
    )
    actions = QtWidgets.QHBoxLayout()
    actions.setSpacing(10)
    action_buttons = {}
    for action_id in primary_action_ids:
        button = QtWidgets.QPushButton(action_by_id(action_id).label)
        button.setObjectName("PrimaryButton" if action_id == ACTION_OPEN_LINK else "ActionButton")
        button.setEnabled(False)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.clicked.connect(
            lambda checked=False, selected_action_id=action_id: _handle_project_action_click(selected_action_id)
        )
        actions.addWidget(button)
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
        if action_id in {ACTION_REMOVE_POINTCLOUD, ACTION_DELETE}:
            edit_menu.addSeparator()
        menu_action = edit_menu.addAction(action_by_id(action_id).label)
        menu_action.triggered.connect(
            lambda checked=False, selected_action_id=action_id: _handle_project_action_click(selected_action_id)
        )
        edit_actions[action_id] = menu_action
    edit_button.setMenu(edit_menu)
    actions.addWidget(edit_button)
    actions.addStretch(1)
    detail_layout.addLayout(actions)
    content.addWidget(detail_panel)
    content.setSizes([760, 380])

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

    def reload_projects():
        nonlocal projects
        selected = selected_project()
        selected_project_id = selected.project_id if selected is not None else ""
        projects = _resolve_project_previews(
            None if project_provider is not None else project_previews,
            project_provider,
        )
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
        table.resizeColumnsToContents()
        if selected_project_id:
            _select_project_by_id(selected_project_id)
        _select_first_visible_project_if_needed()
        update_detail_panel()

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
        pointcloud = None if project_override is not None else selected_pointcloud()
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
            ACTION_DUPLICATE,
            ACTION_DOWNLOAD,
            ACTION_DISABLE_LINK,
            ACTION_ENABLE_LINK,
            ACTION_DELETE,
            ACTION_REPLACE_ALL_POINTCLOUDS,
            ACTION_ADD_POINTCLOUDS,
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

    def update_action_buttons():
        project = selected_project()
        pointcloud = selected_pointcloud()
        for action_id, button in action_buttons.items():
            button.setEnabled(is_action_available(action_id, project, pointcloud))
        any_edit_available = False
        for action_id, menu_action in edit_actions.items():
            available = is_action_available(action_id, project, pointcloud)
            menu_action.setEnabled(available)
            menu_action.setVisible(available)
            any_edit_available = any_edit_available or available
        edit_button.setEnabled(any_edit_available)

    def update_detail_panel():
        project = selected_project()
        update_action_buttons()
        cloud_list.clear()
        if project is None:
            detail_title.setText("Kein Projekt ausgewählt")
            status_badge.hide()
            detail_hint.show()
            info_container.hide()
            cloud_label.hide()
            cloud_list.hide()
            history_label.hide()
            history_log.hide()
            return

        detail_title.setText(project.project)
        status_badge.setText("Inaktiv" if project.disabled else "Aktiv")
        status_badge.setObjectName("PreviewBadgeDanger" if project.disabled else "PreviewBadgeLight")
        status_badge.style().unpolish(status_badge)
        status_badge.style().polish(status_badge)
        status_badge.show()
        detail_hint.hide()
        info_container.show()

        info_values["Kunde"].setText(project.customer or "-")
        info_values["Format"].setText(project.format or "-")
        info_values["Punktwolken"].setText(str(len(project.pointclouds)))
        info_values["Erstellt am"].setText(project.created or "-")
        _set_viewer_link(info_values["Viewer-Link"], project)
        info_values["S3-Pfad"].setText(project.s3_path or "-")

        cloud_label.show()
        cloud_list.show()
        for pointcloud in project.pointclouds:
            item = QtWidgets.QListWidgetItem(
                f"{pointcloud.name} - {pointcloud.format} - {pointcloud.points} - CRS: {pointcloud.crs}"
            )
            item.setData(pointcloud_role, pointcloud)
            if pointcloud.s3_path:
                item.setToolTip(pointcloud.s3_path)
            cloud_list.addItem(item)

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

    table.selectionModel().selectionChanged.connect(lambda selected, deselected: update_detail_panel())
    table.doubleClicked.connect(lambda index: open_selected_project_if_available())
    cloud_list.itemSelectionChanged.connect(update_action_buttons)
    cloud_list.itemDoubleClicked.connect(lambda item: replace_double_clicked_pointcloud())
    table.customContextMenuRequested.connect(show_project_context_menu)
    cloud_list.customContextMenuRequested.connect(show_pointcloud_context_menu)
    search.textChanged.connect(lambda text: (_select_first_visible_project_if_needed(), update_detail_panel()))
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
            return True
        return False

    _select_first_visible_project_if_needed()
    update_detail_panel()
    page.reload_projects = reload_projects
    page.focus_search = focus_search
    page.clear_search = clear_search
    page.focus_default = focus_search
    return page


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
    badge.setObjectName("PreviewBadgeLight")
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
        )
    )


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
