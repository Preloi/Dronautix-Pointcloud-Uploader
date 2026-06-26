"""UI-free preview helpers for the local Potree conversion page."""

from __future__ import annotations

from dataclasses import dataclass


STEP_SELECT_SOURCE = "select_source"
STEP_TARGET = "target"
STEP_CONVERT = "convert"
STEP_VERIFY = "verify"
STEP_DONE = "done"


@dataclass(frozen=True)
class LocalConversionStep:
    number: int
    key: str
    title: str
    detail: str
    state: str


@dataclass(frozen=True)
class LocalConversionPreview:
    source_file: str
    output_dir: str
    converter_path: str
    supported_formats: tuple[str, ...]
    steps: tuple[LocalConversionStep, ...]
    log_entries: tuple[str, ...]


def build_local_conversion_preview(
    *,
    source_file: str = "",
    output_dir: str = "",
    converter_path: str = "",
    current_step: str = STEP_SELECT_SOURCE,
) -> LocalConversionPreview:
    source_label = str(source_file or "").strip() or "Keine Quelle ausgewählt"
    output_label = str(output_dir or "").strip() or "Kein Zielordner ausgewählt"
    converter_label = str(converter_path or "").strip() or "Bundle oder Override aus den Einstellungen"
    log_entries = [
        "[BEREIT] Quelle, Zielordner und Converter werden im Dialog validiert.",
        "[PIPELINE] PotreeConverter wird mit den eingefrorenen CLI-Flags gestartet.",
    ]
    if output_dir:
        log_entries.append("[DEFAULT] Ausgabeordner aus den Einstellungen übernommen.")
    if converter_path:
        log_entries.append("[DEFAULT] PotreeConverter aus den Einstellungen übernommen.")
    return LocalConversionPreview(
        source_file=source_label,
        output_dir=output_label,
        converter_path=converter_label,
        supported_formats=(".las", ".laz"),
        steps=build_local_conversion_steps(current_step),
        log_entries=tuple(log_entries),
    )


def build_local_conversion_steps(current_step: str = STEP_TARGET) -> tuple[LocalConversionStep, ...]:
    definitions = (
        (STEP_SELECT_SOURCE, "Quelle", "LAS/LAZ-Datei auswählen oder ablegen."),
        (STEP_TARGET, "Zielordner", "Lokalen Potree-Ausgabeordner prüfen."),
        (STEP_CONVERT, "Konvertierung", "PotreeConverter mit eingefrorenen Flags starten."),
        (STEP_VERIFY, "Prüfung", "metadata.json und Ergebnisordner kontrollieren."),
        (STEP_DONE, "Fertig", "Lokales Potree-Projekt bereitstellen."),
    )
    keys = [definition[0] for definition in definitions]
    current_index = keys.index(current_step) if current_step in keys else keys.index(STEP_TARGET)

    steps = []
    for index, (key, title, detail) in enumerate(definitions):
        if index < current_index:
            state = "done"
        elif index == current_index:
            state = "current"
        else:
            state = "upcoming"
        steps.append(LocalConversionStep(index + 1, key, title, detail, state))
    return tuple(steps)


def example_local_conversion_preview() -> LocalConversionPreview:
    return build_local_conversion_preview()


def format_supported_formats(formats: tuple[str, ...]) -> str:
    return ", ".join(formats)


__all__ = [
    "LocalConversionPreview",
    "LocalConversionStep",
    "STEP_CONVERT",
    "STEP_DONE",
    "STEP_SELECT_SOURCE",
    "STEP_TARGET",
    "STEP_VERIFY",
    "build_local_conversion_preview",
    "build_local_conversion_steps",
    "example_local_conversion_preview",
    "format_supported_formats",
]
