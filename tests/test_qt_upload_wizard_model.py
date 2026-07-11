from dronautix_uploader.qt_app.upload_wizard_model import (
    STEP_CURRENT,
    STEP_CRS_FORMAT,
    STEP_DONE,
    STEP_PROJECT,
    STEP_REVIEW,
    STEP_SOURCES,
    STEP_UPLOAD_LOG,
    STEP_UPCOMING,
    UploadWizardState,
    advance_wizard,
    build_upload_request_from_wizard_state,
    build_upload_wizard_preview,
    build_wizard_steps,
    can_advance_wizard,
    example_upload_wizard_preview,
    format_source_count,
    format_source_summary,
    retreat_wizard,
    source_format_label,
    source_handling_label,
    validate_wizard_step,
)


def test_wizard_steps_mark_done_current_and_upcoming_states():
    steps = build_wizard_steps(STEP_REVIEW)

    assert [step.number for step in steps] == [1, 2, 3, 4, 5]
    assert steps[0].state == STEP_DONE
    assert steps[2].state == STEP_DONE
    assert steps[3].state == STEP_CURRENT
    assert steps[4].state == STEP_UPCOMING


def test_unknown_current_step_falls_back_to_review():
    steps = build_wizard_steps("does-not-exist")

    assert [step.state for step in steps].count(STEP_CURRENT) == 1
    assert next(step for step in steps if step.state == STEP_CURRENT).key == STEP_REVIEW


def test_example_preview_contains_five_steps_sources_crs_and_log_data():
    preview = example_upload_wizard_preview()

    assert len(preview.steps) == 5
    assert preview.source_count_label == "3 Quellen"
    assert any(source.format == "COPC" for source in preview.sources)
    assert "EPSG:25832" in preview.crs_format.horizontal_crs
    assert preview.log_entries[-1].level == "Info"
    assert "Review bereit" in preview.log_entries[-1].message


def test_source_labels_are_qt_free_and_stable():
    preview = example_upload_wizard_preview()

    assert format_source_count(1) == "1 Quelle"
    assert format_source_count(2) == "2 Quellen"
    assert format_source_summary(preview.sources[0]) == (
        "Bestand_EG.laz - LAZ - 18.2 Mio. Punkte - Potree-Konvertierung"
    )
    assert build_wizard_steps(STEP_SOURCES)[1].state == STEP_CURRENT


def test_upload_wizard_state_validates_each_step_before_advancing():
    empty = UploadWizardState(current_step=STEP_PROJECT)
    project_ready = UploadWizardState(customer="Kunde", project="Projekt", current_step=STEP_PROJECT)
    sources_missing = UploadWizardState(customer="Kunde", project="Projekt", current_step=STEP_SOURCES)
    raw_without_converter = UploadWizardState(
        customer="Kunde",
        project="Projekt",
        source_paths=("scan.laz",),
        current_step=STEP_CRS_FORMAT,
    )
    ready = UploadWizardState(
        customer="Kunde",
        project="Projekt",
        source_paths=("scan.laz", "direct.copc.laz"),
        converter_path="PotreeConverter.exe",
        output_base_dir="out",
        current_step=STEP_CRS_FORMAT,
    )

    assert validate_wizard_step(empty) == ("Kunde darf nicht leer sein.", "Projektname darf nicht leer sein.")
    assert can_advance_wizard(project_ready)
    assert advance_wizard(project_ready).current_step == STEP_SOURCES
    assert validate_wizard_step(sources_missing) == ("Mindestens eine Punktwolkenquelle auswählen.",)
    assert "Potree Converter" in validate_wizard_step(raw_without_converter)[0]
    assert can_advance_wizard(ready)
    assert advance_wizard(ready).current_step == STEP_REVIEW
    assert retreat_wizard(advance_wizard(ready)).current_step == STEP_CRS_FORMAT
    assert retreat_wizard(empty).current_step == STEP_PROJECT


def test_upload_wizard_state_builds_core_upload_request_with_crs_metadata():
    state = UploadWizardState(
        customer=" Kunde ",
        project=" Projekt ",
        source_paths=(" scan.laz ", " direct.copc.laz "),
        converter_path=" converter.exe ",
        output_base_dir=" out ",
        overwrite=True,
        horizontal_crs="EPSG:25832",
        vertical_crs="DHHN2016",
        current_step=STEP_REVIEW,
    )

    request = build_upload_request_from_wizard_state(state)

    assert request.kunde == "Kunde"
    assert request.projekt == "Projekt"
    assert request.source_paths == ("scan.laz", "direct.copc.laz")
    assert request.converter_path == "converter.exe"
    assert request.output_base_dir == "out"
    assert request.overwrite is True
    assert request.crs_info_by_source_path["scan.laz"]["projection"] == "EPSG:25832"
    assert request.crs_info_by_source_path["direct.copc.laz"]["vertical_crs"] == "DHHN2016"


def test_upload_wizard_preview_is_derived_from_state_not_only_example_data():
    state = UploadWizardState(
        customer="Kunde",
        project="Projekt",
        source_paths=("scan.laz", "direct.copc.laz", "C:/potree/out"),
        converter_path="PotreeConverter.exe",
        output_base_dir="out",
        horizontal_crs="EPSG:25832",
        current_step=STEP_REVIEW,
    )

    preview = build_upload_wizard_preview(state)

    assert preview.project_name == "Projekt"
    assert preview.customer == "Kunde"
    assert preview.source_count_label == "3 Quellen"
    assert [source.format for source in preview.sources] == ["LAZ", "COPC", "Potree"]
    assert [source.handling for source in preview.sources] == [
        "Potree-Konvertierung",
        "Direktupload",
        "Vorhandener Potree-Ordner",
    ]
    assert preview.crs_format.output_format == "Gemischt: Potree + COPC"
    assert preview.log_entries == (preview.log_entries[0],)
    assert preview.log_entries[0].level == "Info"


def test_upload_wizard_preview_surfaces_validation_errors_as_log_entries():
    preview = build_upload_wizard_preview(UploadWizardState(current_step=STEP_UPLOAD_LOG))

    assert preview.current_step == STEP_UPLOAD_LOG
    assert preview.log_entries
    assert preview.log_entries[0].level == "Warnung"
    assert "Kunde" in preview.log_entries[0].message


def test_upload_source_format_and_handling_labels_are_stable():
    assert source_format_label("scan.copc.laz") == "COPC"
    assert source_format_label("scan.laz") == "LAZ"
    assert source_format_label("scan.las") == "LAS"
    assert source_format_label("C:/potree/out") == "Potree"
    assert source_handling_label("scan.copc.laz") == "Direktupload"
    assert source_handling_label("scan.laz") == "Potree-Konvertierung"
    assert source_handling_label("C:/potree/out") == "Vorhandener Potree-Ordner"
