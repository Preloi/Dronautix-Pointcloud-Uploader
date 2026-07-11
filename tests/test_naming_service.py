from dronautix_uploader.core.naming_service import build_project_paths, sanitize_folder_name


def test_sanitize_folder_name_matches_legacy_umlaut_behavior():
    assert sanitize_folder_name(" München Süd / Projekt 1 ") == "muenchen_sued_projekt_1"


def test_build_project_paths_keeps_viewer_and_s3_shapes():
    paths = build_project_paths("Kunde A", "Projekt X", "abc123ef")

    assert paths.project_viewer_root == "kunde_a/abc123ef/projekt_x"
    assert paths.s3_prefix == "pointclouds/kunde_a/abc123ef/projekt_x"
    assert paths.project_url.endswith("?id=abc123ef")
