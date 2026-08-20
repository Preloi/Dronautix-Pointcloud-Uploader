def test_glb_selection_keeps_supported_paths_once_case_insensitively():
    from dronautix_uploader.qt_app.glb_upload_model import append_unique_glb_paths

    assert append_unique_glb_paths(
        ("C:/Daten/Bestand.GLB",),
        ("c:/daten/bestand.glb", " C:/Daten/Neu.glb ", "C:/Daten/nicht.obj"),
    ) == ("C:/Daten/Bestand.GLB", "C:/Daten/Neu.glb")


def test_model_file_size_is_shown_without_touching_the_source(tmp_path):
    from dronautix_uploader.qt_app.glb_upload_model import format_file_size

    glb_path = tmp_path / "Haus.glb"
    glb_path.write_bytes(b"glTF")

    assert format_file_size(str(glb_path)) == "4 B"


def test_explicit_glb_model_json_pair_requires_a_single_intentional_pair():
    from dronautix_uploader.qt_app.glb_upload_model import explicit_glb_model_json_pair

    assert explicit_glb_model_json_pair(("C:/Daten/Haus.glb",)) is None
    assert explicit_glb_model_json_pair(("C:/Daten/Haus.glb", "C:/Daten/model.json")) == (
        "C:/Daten/Haus.glb",
        "C:/Daten/model.json",
    )


def test_explicit_glb_model_json_pair_rejects_ambiguous_or_unpaired_sidecars():
    import pytest

    from dronautix_uploader.qt_app.glb_upload_model import explicit_glb_model_json_pair

    with pytest.raises(ValueError, match="genau einem GLB"):
        explicit_glb_model_json_pair(("C:/Daten/model.json",))
    with pytest.raises(ValueError, match="genau einem GLB"):
        explicit_glb_model_json_pair(("C:/Daten/A.glb", "C:/Daten/B.glb", "C:/Daten/model.json"))
