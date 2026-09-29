from dronautix_uploader.core.naming_service import build_project_paths, sanitize_folder_name


def test_sanitize_folder_name_matches_legacy_umlaut_behavior():
    assert sanitize_folder_name(" München Süd / Projekt 1 ") == "muenchen_sued_projekt_1"


def test_build_project_paths_keeps_viewer_and_s3_shapes():
    paths = build_project_paths("Kunde A", "Projekt X", "abc123ef")

    assert paths.project_viewer_root == "kunde_a/abc123ef/projekt_x"
    assert paths.s3_prefix == "pointclouds/kunde_a/abc123ef/projekt_x"
    assert paths.project_url.endswith("?id=abc123ef")


def test_build_project_paths_rejects_names_without_usable_characters():
    import pytest

    from dronautix_uploader.core.naming_service import build_project_paths

    with pytest.raises(ValueError, match="Kunde"):
        build_project_paths("Москва", "Projekt", "abcd1234")
    with pytest.raises(ValueError, match="Projektname"):
        build_project_paths("Kunde", "---", "abcd1234")
    with pytest.raises(ValueError, match="Projekt-ID"):
        build_project_paths("Kunde", "Projekt", "../x")


def test_build_project_paths_never_contains_empty_segments():
    from dronautix_uploader.core.naming_service import build_project_paths

    paths = build_project_paths("Netz NÖ", "Brücke A1 km 213,4", "abcd1234")

    assert "//" not in paths.s3_prefix and not paths.project_viewer_root.startswith("/")
    assert paths.project_viewer_root == "netz_noe/abcd1234/bruecke_a1_km_213_4"
