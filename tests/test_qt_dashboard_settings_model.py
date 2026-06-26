from dronautix_uploader.qt_app.dashboard_settings_model import (
    STATUS_ERROR,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARNING,
    UPDATE_CHANNEL_MANUAL,
    UPDATE_CHANNEL_PREVIEW,
    build_dashboard_preview,
    build_cutover_readiness,
    converter_status,
    credential_status,
    example_dashboard_preview,
    example_settings_preview,
    make_cutover_hints,
    make_preview_hints,
    output_folder_status,
    settings_status_action_id,
    status_level_label,
    update_channel_status,
)


def test_credential_status_distinguishes_ready_profile_and_missing_credentials():
    ready = credential_status(True, True, "dronautix")
    profile_only = credential_status(False, False, "dronautix")
    missing = credential_status(False, False)

    assert ready.level == STATUS_OK
    assert ready.status == "Bereit"
    assert profile_only.level == STATUS_WARNING
    assert profile_only.status == "Profil prüfen"
    assert missing.level == STATUS_ERROR
    assert missing.action == "Credentials eintragen"


def test_converter_status_prefers_override_over_bundle():
    override = converter_status(True, "D:/Tools/PotreeConverter.exe")
    bundle = converter_status(True)
    missing = converter_status(False)

    assert override.status == "Override aktiv"
    assert override.level == STATUS_WARNING
    assert bundle.status == "Bundle bereit"
    assert bundle.level == STATUS_OK
    assert missing.level == STATUS_ERROR


def test_output_folder_and_update_channel_statuses_are_explicit():
    assert output_folder_status("", False).level == STATUS_ERROR
    assert output_folder_status("C:/Output", True).status == "Schreibbar"
    assert output_folder_status("C:/Output", False).level == STATUS_WARNING

    preview_channel = update_channel_status(UPDATE_CHANNEL_PREVIEW)
    manual_channel = update_channel_status(UPDATE_CHANNEL_MANUAL, "1.7.10")

    assert preview_channel.level == STATUS_WARNING
    assert manual_channel.level == STATUS_INFO
    assert "1.7.10" in manual_channel.detail
    assert output_folder_status("C:/Output", True).action == "Ordner ändern"


def test_settings_status_action_ids_match_page_actions():
    assert settings_status_action_id(credential_status(True, True, "dronautix")) == "test_connection"
    assert settings_status_action_id(credential_status(False, False)) == "edit"
    assert settings_status_action_id(converter_status(True)) == "edit"
    assert settings_status_action_id(output_folder_status("C:/Output", True)) == "edit"
    assert settings_status_action_id(update_channel_status(UPDATE_CHANNEL_PREVIEW)) == "edit"


def test_example_previews_include_dashboard_settings_and_cutover_hints():
    dashboard = example_dashboard_preview()
    settings = example_settings_preview()

    assert len(dashboard.status_cards) == 4
    assert {item.name for item in dashboard.settings_status} == {
        "AWS Credentials",
        "Converter",
        "Output-Ordner",
        "Update-Kanal",
    }
    assert settings.output_folder
    assert make_preview_hints(False)[0].title == "Preview-Modus"
    assert make_preview_hints(True)[0].level == STATUS_OK


def test_cutover_readiness_requires_explicit_gates_not_only_credentials():
    blocked = build_cutover_readiness(runtime_connected=True, golden_ready=False)

    assert not blocked.ready
    assert blocked.completed_required_count == 2
    assert blocked.required_count == 8
    assert blocked.first_open_item.name == "Golden Masters"
    assert make_cutover_hints(blocked)[0].title == "Cutover blockiert"

    ready = build_cutover_readiness(
        runtime_connected=True,
        golden_ready=True,
        v2_golden_comparison_ready=True,
        preview_packaging_ready=True,
        final_packaging_ready=True,
        real_s3_acceptance_passed=True,
        github_asset_sha_verified=True,
        altversion_update_verified=True,
    )

    assert ready.ready
    assert make_cutover_hints(ready)[0].title == "Cutover bereit"


def test_build_dashboard_preview_uses_project_settings_and_activity_state():
    settings = example_settings_preview()

    class Project:
        def __init__(self, disabled=False):
            self.disabled = disabled

    class ActivitySummary:
        running = 2
        warnings = 1
        failed = 0

    class ActivityPreview:
        status_summary = ActivitySummary()

    dashboard = build_dashboard_preview(
        projects=(Project(False), Project(True), Project(False)),
        settings_preview=settings,
        activity_preview=ActivityPreview(),
        runtime_status="S3 verbunden",
    )

    cards = {card.title: card for card in dashboard.status_cards}
    assert cards["Aktive Projekte"].value == "2"
    assert cards["Aktive Projekte"].detail == "1 deaktiviert"
    assert cards["Operationen"].value == "2"
    assert cards["Operationen"].level == STATUS_WARNING
    assert cards["Update"].value == settings.update_channel
    assert dashboard.settings_status == settings.settings_status
    assert dashboard.cutover_hints[0].detail == "S3 verbunden"
    assert dashboard.cutover_hints[1].title == "Cutover blockiert"


def test_status_level_label_falls_back_to_info():
    assert status_level_label(STATUS_OK) == "OK"
    assert status_level_label("unexpected") == "Info"
