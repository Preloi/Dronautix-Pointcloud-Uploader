from dronautix_uploader.qt_app.local_conversion_model import (
    STEP_CONVERT,
    STEP_SELECT_SOURCE,
    STEP_TARGET,
    build_local_conversion_preview,
    build_local_conversion_steps,
    example_local_conversion_preview,
    format_supported_formats,
)


def test_local_conversion_steps_mark_current_state_without_qt():
    steps = build_local_conversion_steps(STEP_CONVERT)

    assert [step.number for step in steps] == [1, 2, 3, 4, 5]
    assert steps[0].state == "done"
    assert steps[2].state == "current"
    assert steps[-1].state == "upcoming"


def test_unknown_local_conversion_step_falls_back_to_target():
    steps = build_local_conversion_steps("missing")

    assert next(step for step in steps if step.state == "current").key == STEP_TARGET


def test_example_local_conversion_preview_contains_legacy_converter_contract():
    preview = example_local_conversion_preview()

    assert preview.supported_formats == (".las", ".laz")
    assert preview.source_file == "Keine Quelle ausgewählt"
    assert "eingefrorenen CLI-Flags" in "\n".join(preview.log_entries)
    assert next(step for step in preview.steps if step.state == "current").key == STEP_SELECT_SOURCE
    assert format_supported_formats(preview.supported_formats) == ".las, .laz"


def test_build_local_conversion_preview_uses_runtime_defaults():
    preview = build_local_conversion_preview(
        output_dir=" C:/Pointclouds/Potree ",
        converter_path=" C:/Tools/PotreeConverter.exe ",
    )

    assert preview.output_dir == "C:/Pointclouds/Potree"
    assert preview.converter_path == "C:/Tools/PotreeConverter.exe"
    assert preview.source_file == "Keine Quelle ausgewählt"
    assert "[DEFAULT] Ausgabeordner aus den Einstellungen übernommen." in preview.log_entries
    assert "[DEFAULT] PotreeConverter aus den Einstellungen übernommen." in preview.log_entries
