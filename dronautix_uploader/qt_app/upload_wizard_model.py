"""UI-free preview data helpers for the upload wizard."""

from __future__ import annotations

from dataclasses import dataclass
import os

from dronautix_uploader.core.upload_workflow_service import NewProjectUploadWorkflowRequest

from .upload_dialog_models import UploadDialogState, validate_upload_dialog_state


STEP_PROJECT = "project"
STEP_SOURCES = "sources"
STEP_CRS_FORMAT = "crs_format"
STEP_REVIEW = "review"
STEP_UPLOAD_LOG = "upload_log"

STEP_DONE = "done"
STEP_CURRENT = "current"
STEP_UPCOMING = "upcoming"
WIZARD_STEP_ORDER = (STEP_PROJECT, STEP_SOURCES, STEP_CRS_FORMAT, STEP_REVIEW, STEP_UPLOAD_LOG)


@dataclass(frozen=True)
class UploadWizardStep:
    number: int
    key: str
    title: str
    description: str
    state: str


@dataclass(frozen=True)
class UploadSourcePreview:
    name: str
    path: str
    size: str
    format: str
    handling: str


@dataclass(frozen=True)
class CrsFormatReview:
    horizontal_crs: str
    vertical_crs: str
    output_format: str
    upload_mode: str
    metadata_target: str


@dataclass(frozen=True)
class UploadLogEntry:
    level: str
    message: str


@dataclass(frozen=True)
class UploadWizardPreview:
    project_name: str
    customer: str
    viewer_path: str
    current_step: str
    steps: tuple[UploadWizardStep, ...]
    sources: tuple[UploadSourcePreview, ...]
    crs_format: CrsFormatReview
    log_entries: tuple[UploadLogEntry, ...]

    @property
    def source_count_label(self) -> str:
        return format_source_count(len(self.sources))


@dataclass(frozen=True)
class UploadWizardState:
    customer: str = ""
    project: str = ""
    source_paths: tuple[str, ...] = ()
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    horizontal_crs: str = ""
    vertical_crs: str = ""
    current_step: str = STEP_PROJECT
    log_entries: tuple[UploadLogEntry, ...] = ()


def build_wizard_steps(current_step: str = STEP_REVIEW) -> tuple[UploadWizardStep, ...]:
    """Return the five upload steps with preview state markers."""

    definitions = (
        (STEP_PROJECT, "Projekt", "Projektname, Kunde und Viewer-Pfad festlegen."),
        (STEP_SOURCES, "Quellen", "LAS, LAZ, COPC oder Potree-Ordner sammeln."),
        (STEP_CRS_FORMAT, "CRS / Format", "Koordinatensysteme und Zielformat prüfen."),
        (STEP_REVIEW, "Review", "Metadaten, Pfade und Upload-Plan kontrollieren."),
        (STEP_UPLOAD_LOG, "Upload / Log", "Fortschritt und Protokoll im Blick behalten."),
    )
    keys = [definition[0] for definition in definitions]
    current_index = keys.index(current_step) if current_step in keys else keys.index(STEP_REVIEW)

    steps = []
    for index, (key, title, description) in enumerate(definitions):
        if index < current_index:
            state = STEP_DONE
        elif index == current_index:
            state = STEP_CURRENT
        else:
            state = STEP_UPCOMING
        steps.append(
            UploadWizardStep(
                number=index + 1,
                key=key,
                title=title,
                description=description,
                state=state,
            )
        )
    return tuple(steps)


def validate_wizard_step(state: UploadWizardState, step: str | None = None) -> tuple[str, ...]:
    selected_step = step or state.current_step
    errors: list[str] = []
    source_paths = normalized_source_paths(state)
    if selected_step in {STEP_PROJECT, STEP_SOURCES, STEP_CRS_FORMAT, STEP_REVIEW, STEP_UPLOAD_LOG}:
        if not state.customer.strip():
            errors.append("Kunde darf nicht leer sein.")
        if not state.project.strip():
            errors.append("Projektname darf nicht leer sein.")
    if selected_step in {STEP_SOURCES, STEP_CRS_FORMAT, STEP_REVIEW, STEP_UPLOAD_LOG} and not source_paths:
        errors.append("Mindestens eine Punktwolkenquelle auswählen.")
    if selected_step in {STEP_CRS_FORMAT, STEP_REVIEW, STEP_UPLOAD_LOG} and any(
        source_needs_conversion(path) for path in source_paths
    ):
        if not state.converter_path.strip():
            errors.append("Potree Converter ist für LAS/LAZ-Quellen erforderlich.")
        if not state.output_base_dir.strip():
            errors.append("Ausgabeordner ist für LAS/LAZ-Quellen erforderlich.")
    if selected_step in {STEP_REVIEW, STEP_UPLOAD_LOG}:
        try:
            build_upload_request_from_wizard_state(state)
        except ValueError as error:
            message = str(error)
            if message and message not in errors:
                errors.append(message)
    return tuple(errors)


def can_advance_wizard(state: UploadWizardState) -> bool:
    return not validate_wizard_step(state)


def advance_wizard(state: UploadWizardState) -> UploadWizardState:
    if not can_advance_wizard(state):
        return state
    current_index = _step_index(state.current_step)
    next_index = min(current_index + 1, len(WIZARD_STEP_ORDER) - 1)
    return UploadWizardState(
        customer=state.customer,
        project=state.project,
        source_paths=state.source_paths,
        converter_path=state.converter_path,
        output_base_dir=state.output_base_dir,
        overwrite=state.overwrite,
        horizontal_crs=state.horizontal_crs,
        vertical_crs=state.vertical_crs,
        current_step=WIZARD_STEP_ORDER[next_index],
        log_entries=state.log_entries,
    )


def retreat_wizard(state: UploadWizardState) -> UploadWizardState:
    current_index = _step_index(state.current_step)
    previous_index = max(current_index - 1, 0)
    return UploadWizardState(
        customer=state.customer,
        project=state.project,
        source_paths=state.source_paths,
        converter_path=state.converter_path,
        output_base_dir=state.output_base_dir,
        overwrite=state.overwrite,
        horizontal_crs=state.horizontal_crs,
        vertical_crs=state.vertical_crs,
        current_step=WIZARD_STEP_ORDER[previous_index],
        log_entries=state.log_entries,
    )


def build_upload_request_from_wizard_state(state: UploadWizardState) -> NewProjectUploadWorkflowRequest:
    return validate_upload_dialog_state(
        UploadDialogState(
            customer=state.customer,
            project=state.project,
            source_paths=normalized_source_paths(state),
            converter_path=state.converter_path,
            output_base_dir=state.output_base_dir,
            overwrite=state.overwrite,
            horizontal_crs=state.horizontal_crs,
            vertical_crs=state.vertical_crs,
        )
    )


def build_upload_wizard_preview(state: UploadWizardState) -> UploadWizardPreview:
    source_paths = normalized_source_paths(state)
    sources = tuple(upload_source_preview_from_path(path) for path in source_paths)
    errors = validate_wizard_step(state)
    log_entries = state.log_entries or tuple(UploadLogEntry("Warnung", error) for error in errors)
    if not log_entries and state.current_step == STEP_REVIEW:
        log_entries = (UploadLogEntry("Info", "Upload-Request ist bereit für Review."),)
    return UploadWizardPreview(
        project_name=state.project.strip() or "Unbenanntes Projekt",
        customer=state.customer.strip() or "Ohne Kunde",
        viewer_path="Wird beim Upload aus Kunde, Projekt und Projekt-ID erzeugt.",
        current_step=state.current_step if state.current_step in WIZARD_STEP_ORDER else STEP_PROJECT,
        steps=build_wizard_steps(state.current_step),
        sources=sources,
        crs_format=CrsFormatReview(
            horizontal_crs=state.horizontal_crs.strip() or "Nicht gesetzt",
            vertical_crs=state.vertical_crs.strip() or "Nicht gesetzt",
            output_format=format_output_mode(sources),
            upload_mode="S3-Projektupload mit gemeinsamer Convert+Upload-Pipeline",
            metadata_target="projects_index.json, metadata.json, cloud.js",
        ),
        log_entries=log_entries,
    )


def normalized_source_paths(state: UploadWizardState) -> tuple[str, ...]:
    return tuple(path.strip() for path in state.source_paths if path.strip())


def upload_source_preview_from_path(source_path: str) -> UploadSourcePreview:
    name = os.path.basename(source_path.rstrip("/\\")) or source_path
    source_format = source_format_label(source_path)
    return UploadSourcePreview(
        name=name,
        path=source_path,
        size="-",
        format=source_format,
        handling=source_handling_label(source_path),
    )


def source_format_label(source_path: str) -> str:
    lower_path = source_path.lower()
    if lower_path.endswith(".copc.laz"):
        return "COPC"
    if lower_path.endswith(".laz"):
        return "LAZ"
    if lower_path.endswith(".las"):
        return "LAS"
    return "Potree"


def source_handling_label(source_path: str) -> str:
    if source_needs_conversion(source_path):
        return "Potree-Konvertierung"
    if source_path.lower().endswith(".copc.laz"):
        return "Direktupload"
    return "Vorhandener Potree-Ordner"


def source_needs_conversion(source_path: str) -> bool:
    lower_path = source_path.lower()
    return (lower_path.endswith(".las") or lower_path.endswith(".laz")) and not lower_path.endswith(".copc.laz")


def format_output_mode(sources: tuple[UploadSourcePreview, ...]) -> str:
    formats = {source.format for source in sources}
    if not formats:
        return "Keine Quellen"
    if formats == {"COPC"}:
        return "COPC-Direktupload"
    if formats <= {"LAS", "LAZ", "Potree"}:
        return "Potree"
    return "Gemischt: Potree + COPC"


def example_upload_wizard_preview() -> UploadWizardPreview:
    """Return representative data for the disconnected Qt upload preview."""

    sources = (
        UploadSourcePreview(
            name="Bestand_EG.laz",
            path="D:/Projekte/Nord/Bestand_EG.laz",
            size="18.2 Mio. Punkte",
            format="LAZ",
            handling="Potree-Konvertierung",
        ),
        UploadSourcePreview(
            name="Fassade_Nord.copc.laz",
            path="D:/Projekte/Nord/Fassade_Nord.copc.laz",
            size="7.4 Mio. Punkte",
            format="COPC",
            handling="Direktupload",
        ),
        UploadSourcePreview(
            name="Dachaufmass.las",
            path="D:/Projekte/Nord/Dachaufmass.las",
            size="3.1 Mio. Punkte",
            format="LAS",
            handling="Potree-Konvertierung",
        ),
    )
    return UploadWizardPreview(
        project_name="Beispielprojekt Nord",
        customer="Dronautix",
        viewer_path="viewer/projekte/beispielprojekt-nord",
        current_step=STEP_REVIEW,
        steps=build_wizard_steps(STEP_REVIEW),
        sources=sources,
        crs_format=CrsFormatReview(
            horizontal_crs="EPSG:25832 - ETRS89 / UTM Zone 32N",
            vertical_crs="DHHN2016 / NHN",
            output_format="Gemischt: Potree + COPC",
            upload_mode="S3-Projektupload mit Metadatenupdate",
            metadata_target="projects_index.json, metadata.json, cloud.js",
        ),
        log_entries=(
            UploadLogEntry("Info", "Projekt- und Kundendaten vollständig."),
            UploadLogEntry("Info", "3 Quellen erkannt, davon 1 COPC-Direktupload."),
            UploadLogEntry("Info", "Review bereit; Upload startet nach expliziter Bestätigung im Dialog."),
        ),
    )


def format_source_count(source_count: int) -> str:
    """Return a compact German count label for selected source files."""

    if source_count == 1:
        return "1 Quelle"
    return f"{source_count} Quellen"


def format_source_summary(source: UploadSourcePreview) -> str:
    """Return the one-line source summary used by tests and UI previews."""

    return f"{source.name} - {source.format} - {source.size} - {source.handling}"


def _step_index(step: str) -> int:
    return WIZARD_STEP_ORDER.index(step) if step in WIZARD_STEP_ORDER else 0


__all__ = [
    "CrsFormatReview",
    "STEP_CRS_FORMAT",
    "STEP_CURRENT",
    "STEP_DONE",
    "STEP_PROJECT",
    "STEP_REVIEW",
    "STEP_SOURCES",
    "STEP_UPCOMING",
    "STEP_UPLOAD_LOG",
    "UploadWizardState",
    "UploadLogEntry",
    "UploadSourcePreview",
    "UploadWizardPreview",
    "UploadWizardStep",
    "WIZARD_STEP_ORDER",
    "advance_wizard",
    "build_upload_request_from_wizard_state",
    "build_upload_wizard_preview",
    "build_wizard_steps",
    "can_advance_wizard",
    "example_upload_wizard_preview",
    "format_source_count",
    "format_source_summary",
    "normalized_source_paths",
    "retreat_wizard",
    "source_format_label",
    "source_handling_label",
    "source_needs_conversion",
    "validate_wizard_step",
]
