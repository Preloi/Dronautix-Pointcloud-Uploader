from dronautix_uploader.qt_app.dashboard_settings_model import (
    STATUS_ERROR,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARNING,
    UPDATE_CHANNELS,
    UPDATE_CHANNEL_MANUAL,
    UPDATE_CHANNEL_STABLE,
    build_cutover_readiness,
    converter_status,
    credential_status,
    example_settings_preview,
    output_folder_status,
    settings_status_action_id,
    status_level_label,
    update_channel_status,
)


def test_credential_status_distinguishes_ready_and_missing_credentials():
    ready = credential_status(True, True, "dronautix")
    missing = credential_status(False, False)

    assert ready.level == STATUS_OK
    assert ready.status == "Bereit"
    assert missing.level == STATUS_ERROR
    assert missing.action == "Credentials eintragen"


def test_converter_status_prefers_override_over_bundle():
    override = converter_status(True, "D:/Tools/PotreeConverter.exe")
    bundle = converter_status(True)
    missing = converter_status(False)

    assert override.status == "Override aktiv"
    assert override.level == STATUS_WARNING
    assert bundle.status == "Bereit"
    assert bundle.level == STATUS_OK
    assert missing.level == STATUS_ERROR


def test_output_folder_and_update_channel_statuses_are_explicit():
    assert output_folder_status("", False).level == STATUS_WARNING
    assert output_folder_status("C:/Output", True).status == "Schreibbar"
    assert output_folder_status("C:/Output", False).level == STATUS_WARNING
    assert output_folder_status("C:/Output", True).action == "Ordner ändern"

    stable_channel = update_channel_status(UPDATE_CHANNEL_STABLE)
    manual_channel = update_channel_status(UPDATE_CHANNEL_MANUAL, "1.7.10")
    legacy_preview_value = update_channel_status("Preview")

    assert stable_channel.level == STATUS_OK
    assert manual_channel.level == STATUS_INFO
    assert "1.7.10" in manual_channel.detail
    # Alte Configs mit dem frueheren Preview-Kanal werden als Stable angezeigt.
    assert legacy_preview_value.status == UPDATE_CHANNEL_STABLE


def test_update_channels_offer_stable_and_manual_only():
    assert UPDATE_CHANNELS == (UPDATE_CHANNEL_STABLE, UPDATE_CHANNEL_MANUAL)


def test_settings_status_action_ids_match_page_actions():
    assert settings_status_action_id(credential_status(True, True, "dronautix")) == "test_connection"
    assert settings_status_action_id(credential_status(False, False)) == "edit"
    assert settings_status_action_id(converter_status(True)) == "edit"
    assert settings_status_action_id(output_folder_status("C:/Output", True)) == "edit"
    assert settings_status_action_id(update_channel_status(UPDATE_CHANNEL_STABLE)) == "check_update"


def test_example_settings_preview_covers_all_status_rows():
    settings = example_settings_preview()

    assert {item.name for item in settings.settings_status} == {
        "AWS Credentials",
        "Converter",
        "Output-Ordner",
        "Updates",
    }
    assert settings.output_folder


def test_cutover_readiness_requires_explicit_gates_not_only_credentials():
    blocked = build_cutover_readiness(runtime_connected=True, golden_ready=False)

    assert not blocked.ready
    assert blocked.completed_required_count == 2
    assert blocked.required_count == 8
    assert blocked.first_open_item.name == "Golden Masters"

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


def test_status_level_label_falls_back_to_info():
    assert status_level_label(STATUS_OK) == "OK"
    assert status_level_label("unexpected") == "Info"
