"""Upload page: new projects to S3 or local-only Potree conversion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from ..crs_detection_worker import start_crs_detection
from ..activity_model import normalize_progress_value
from dronautix_uploader.core.crs_detection import detect_pointcloud_crs, normalize_crs_value
from dronautix_uploader.core.crs_service import get_crs_display_value, get_vertical_crs_display_value
from ..glb_upload_model import append_unique_glb_paths, explicit_glb_model_json_pair, format_file_size
from ..upload_wizard_model import source_format_label, source_handling_label
from .widgets import _create_source_drop_list, _fit_drop_list_height

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


def create_upload_page(
    QtCore,
    QtWidgets,
    *,
    on_start: Callable[[], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
    defaults_provider: Callable[[], object] | None = None,
    crs_detector: Callable[[str], dict | None] | None = None,
):
    """Single-screen upload + local conversion form (no modal, no stepper)."""

    state = {
        "mode": UPLOAD_MODE_UPLOAD,
        "running": False,
        "sources": [],
        "mode_sources": {},
        "detected_crs": {},
        # Per-path generation tokens: a result is only applied if it belongs to
        # the latest request for that path (removed/re-added paths, late results).
        "crs_generation": {},
        "crs_pending": {},
        "models": [],
        "model_sidecars": {},
        "model_results": {},
    }

    page = QtWidgets.QWidget()
    page.setObjectName("Page")
    page_root = QtWidgets.QVBoxLayout(page)
    page_root.setContentsMargins(32, 28, 32, 28)
    page_root.setSpacing(16)

    class CrsResultEmitter(QtCore.QObject):
        # Emitted from the detection thread; the connection to a slot of an
        # object living in the GUI thread is queued automatically.
        detected = QtCore.Signal(str, int, object)

    crs_emitter = CrsResultEmitter(page)
    form_scroll = QtWidgets.QScrollArea()
    form_scroll.setObjectName("UploadFormScrollArea")
    form_scroll.setWidgetResizable(True)
    form_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    form_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    form_content = QtWidgets.QWidget()
    form_content.setObjectName("UploadFormContent")
    root = QtWidgets.QVBoxLayout(form_content)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(16)
    form_scroll.setWidget(form_content)
    page_root.addWidget(form_scroll, 1)

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

    # Segment control: chooses the mode, it does not start anything. It must
    # not look like (or be labelled like) the primary "Hochladen" button.
    mode_upload_button = QtWidgets.QPushButton("Upload zu S3")
    mode_upload_button.setObjectName("ModeSegment")
    mode_upload_button.setProperty("segment", "first")
    mode_upload_button.setCheckable(True)
    mode_upload_button.setChecked(True)
    mode_upload_button.setCursor(QtCore.Qt.PointingHandCursor)
    mode_upload_button.setToolTip("Punktwolken konvertieren und zu S3 hochladen")
    mode_convert_button = QtWidgets.QPushButton("Nur lokal konvertieren")
    mode_convert_button.setObjectName("ModeSegment")
    mode_convert_button.setProperty("segment", "last")
    mode_convert_button.setCheckable(True)
    mode_convert_button.setCursor(QtCore.Qt.PointingHandCursor)
    mode_convert_button.setToolTip("LAS/LAZ nur lokal in ein Potree-Projekt umwandeln, ohne Upload")
    mode_group = QtWidgets.QButtonGroup(page)
    mode_group.setExclusive(True)
    mode_group.addButton(mode_upload_button)
    mode_group.addButton(mode_convert_button)
    mode_segment = QtWidgets.QHBoxLayout()
    mode_segment.setSpacing(0)
    mode_segment.addWidget(mode_upload_button)
    mode_segment.addWidget(mode_convert_button)
    header.addLayout(mode_segment)
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
    sources_panel.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
    sources_layout = QtWidgets.QVBoxLayout(sources_panel)
    sources_layout.setContentsMargins(20, 16, 20, 16)
    sources_layout.setSpacing(10)
    sources_header = QtWidgets.QHBoxLayout()
    sources_title = QtWidgets.QLabel("Punktwolken (LAS/LAZ)")
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
        placeholder="LAS/LAZ-Dateien oder Potree-Ordner hierher ziehen oder über „Dateien“ / „Ordner“ auswählen",
    )
    source_list.setObjectName("UploadSourceList")
    drop_list_height = 104
    source_list.setFixedHeight(drop_list_height)
    source_list.setToolTip("Dateien/Ordner hierher ziehen. Markieren und 'Entf' entfernt Quellen.")
    sources_layout.addWidget(source_list)

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
    root.addWidget(sources_panel)

    # --- Optional GLB models ----------------------------------------------
    models_panel = QtWidgets.QFrame()
    models_panel.setObjectName("UploadModelsPanel")
    models_panel.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
    models_layout = QtWidgets.QVBoxLayout(models_panel)
    models_layout.setContentsMargins(20, 16, 20, 16)
    models_layout.setSpacing(10)
    models_header = QtWidgets.QHBoxLayout()
    models_title = QtWidgets.QLabel("3D-Modelle (GLB)")
    models_title.setObjectName("PanelTitle")
    models_header.addWidget(models_title)
    models_header.addStretch(1)
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
    model_list.setFixedHeight(drop_list_height)
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
    advanced_toggle.setText("Erweitert (CRS)")
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
    root.addStretch(1)

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
    page_root.addLayout(action_row)

    log_view = QtWidgets.QPlainTextEdit()
    log_view.setObjectName("UploadLogView")
    log_view.setReadOnly(True)
    log_view.setMinimumHeight(120)
    # Converter output is chatty; keep memory and repaint cost bounded.
    log_view.setMaximumBlockCount(5000)
    log_view.setPlaceholderText("Das Upload-Protokoll erscheint hier.")
    page_root.addWidget(log_view)

    # --- Behaviour ----------------------------------------------------------
    def current_defaults():
        if defaults_provider is None:
            return None
        try:
            return defaults_provider()
        except Exception:
            return None

    def detect_sources_crs():
        """Start background detection for sources without a (pending) result."""

        requests = []
        for path in state["sources"]:
            if path in state["detected_crs"] or path in state["crs_pending"]:
                continue
            generation = state["crs_generation"].get(path, 0) + 1
            state["crs_generation"][path] = generation
            state["crs_pending"][path] = generation
            requests.append((path, generation))
        if requests:
            start_crs_detection(
                requests,
                crs_emitter.detected.emit,
                detector=crs_detector or detect_pointcloud_crs,
            )
        update_start_availability()

    def forget_crs(paths):
        for path in paths:
            state["crs_generation"][path] = state["crs_generation"].get(path, 0) + 1
            state["crs_pending"].pop(path, None)
            state["detected_crs"].pop(path, None)

    def crs_detection_pending() -> bool:
        return any(path in state["crs_pending"] for path in state["sources"])

    def apply_crs_result(path, generation, info):
        if state["crs_generation"].get(path) != generation:
            return  # removed, re-added or superseded meanwhile
        state["crs_pending"].pop(path, None)
        state["detected_crs"][path] = dict(info) if isinstance(info, dict) else {}
        for row in range(source_list.count()):
            item = source_list.item(row)
            if item.data(QtCore.Qt.UserRole) == path:
                item.setText(source_item_text(path))
        render_models()
        update_start_availability()

    crs_emitter.detected.connect(apply_crs_result)

    def update_start_availability():
        pending = crs_detection_pending()
        if not state["running"]:
            start_button.setEnabled(on_start is not None and not pending)
        start_button.setToolTip("CRS der Punktwolken wird noch erkannt ..." if pending else "")
        count = len(state["sources"])
        text = "Keine Quelle" if count == 0 else ("1 Quelle" if count == 1 else f"{count} Quellen")
        sources_count.setText(f"{text} · CRS wird erkannt ..." if pending else text)

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
        _fit_drop_list_height(model_list, drop_list_height)
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

    def source_item_text(path: str) -> str:
        fmt = source_format_label(path)
        handling = source_handling_label(path)
        crs = crs_display_for_path(path)
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or path
        return f"{name}   ·   {fmt} → {handling}   ·   CRS: {crs}"

    def render_sources():
        source_list.clear()
        for path in state["sources"]:
            item = QtWidgets.QListWidgetItem(source_item_text(path))
            item.setData(QtCore.Qt.UserRole, path)
            item.setToolTip(path)
            source_list.addItem(item)
        _fit_drop_list_height(source_list, drop_list_height)
        update_start_availability()

    def remove_selected_sources():
        if state["running"]:
            return
        selected = {item.data(QtCore.Qt.UserRole) for item in source_list.selectedItems()}
        if not selected:
            return
        state["sources"] = [path for path in state["sources"] if path not in selected]
        forget_crs(selected)
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
            page, "Punktwolken auswählen", "", "Punktwolken (*.las *.laz);;Alle Dateien (*)"
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

    def switch_mode_sources(previous_mode, mode):
        # Jeder Modus merkt sich seine eigene Quellenliste, damit der Wechsel zu
        # "Nur konvertieren" (genau eine Quelle) keine Upload-Auswahl verwirft.
        if previous_mode == mode:
            return
        saved = state["mode_sources"]
        saved[previous_mode] = list(state["sources"])
        if mode in saved:
            state["sources"] = list(saved[mode])
        elif mode == UPLOAD_MODE_CONVERT and state["sources"]:
            selected = [item.data(QtCore.Qt.UserRole) for item in source_list.selectedItems()]
            state["sources"] = [selected[-1] if selected else state["sources"][-1]]

    def apply_mode(mode):
        switch_mode_sources(state["mode"], mode)
        state["mode"] = mode
        is_convert = mode == UPLOAD_MODE_CONVERT
        customer_input.setEnabled(not is_convert)
        project_input.setEnabled(not is_convert)
        vertical_crs_input.setEnabled(not is_convert)
        set_output_row_visible(is_convert)
        start_button.setText("Konvertieren" if is_convert else "Hochladen")
        advanced_toggle.setText("Erweitert (CRS, Ausgabeordner)" if is_convert else "Erweitert (CRS)")
        subtitle.setText(
            "LAS/LAZ lokal in ein Potree-Projekt konvertieren, ohne Upload."
            if is_convert
            else "Punktwolken konvertieren und zu S3 hochladen."
        )
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
            update_start_availability()
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
    page.crs_detection_pending = crs_detection_pending
    page.model_inputs = build_model_inputs
    page.add_model_paths = add_models
    page.add_source_paths = add_sources
    page.focus_default = lambda: customer_input.setFocus()
    return page
