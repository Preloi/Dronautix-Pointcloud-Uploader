import os
import json

import pytest

from dronautix_uploader.core.contracts import PointcloudSource, ProgressEvent
from dronautix_uploader.core.local_conversion_service import build_local_output_dir
from dronautix_uploader.core.pointcloud_preparation_service import (
    PointcloudPreparationRequest,
    classify_pointcloud_source,
    prepare_pointcloud_sources,
)


def test_prepare_pointcloud_sources_classifies_mixed_sources_and_converts_only_raw(tmp_path):
    copc = tmp_path / "Scan.copc.laz"
    copc.write_bytes(b"copc")
    potree_dir = tmp_path / "Potree Cloud"
    potree_dir.mkdir()
    (potree_dir / "metadata.json").write_text("{}", encoding="utf-8")
    raw = tmp_path / "Scan.laz"
    raw.write_bytes(b"laz")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    output_base = tmp_path / "converted"
    events = []
    runner_calls = []

    def fake_runner(source_file, converter_path, output_dir, on_progress):
        runner_calls.append((source_file, converter_path, output_dir, on_progress))
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "cloud.js"), "w", encoding="utf-8") as file:
            file.write("cloud.js = {};")
        if on_progress:
            on_progress(ProgressEvent(kind="log", message="runner progress"))

    prepared = prepare_pointcloud_sources(
        PointcloudPreparationRequest(
            sources=(str(copc), str(potree_dir), str(raw)),
            converter_path=str(converter),
            output_base_dir=str(output_base),
            overwrite=True,
        ),
        on_progress=events.append,
        converter_runner=fake_runner,
    )

    assert prepared == (
        PointcloudSource(
            source_path=str(copc),
            name="Scan",
            slug="scan",
            input_format="copc",
            source_type="raw_file",
        ),
        PointcloudSource(
            source_path=str(potree_dir),
            name="Potree Cloud",
            slug="potree_cloud",
            input_format="potree",
            source_type="potree_dir",
        ),
        PointcloudSource(
            source_path=build_local_output_dir(str(raw), str(output_base)),
            name="Scan",
            slug="scan_2",
            input_format="potree",
            source_type="potree_dir",
        ),
    )
    assert runner_calls == [
        (
            str(raw),
            str(converter),
            build_local_output_dir(str(raw), str(output_base)),
            events.append,
        )
    ]
    assert any(event.message == "runner progress" for event in events)
    assert events[-1] == ProgressEvent(kind="progress", percent=1.0, phase="preparation")


def test_prepare_pointcloud_sources_uses_build_local_output_dir_and_stable_slugs(tmp_path):
    first = tmp_path / "Floor 1.las"
    second = tmp_path / "Floor 1.laz"
    first.write_bytes(b"las")
    second.write_bytes(b"laz")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    output_base = tmp_path / "potree-output"
    runner_output_dirs = []

    def fake_runner(_source_file, _converter_path, output_dir, _on_progress):
        runner_output_dirs.append(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as file:
            file.write("{}")

    prepared = prepare_pointcloud_sources(
        PointcloudPreparationRequest(
            sources=(str(first), str(second)),
            converter_path=str(converter),
            output_base_dir=str(output_base),
            overwrite=True,
        ),
        converter_runner=fake_runner,
    )

    assert runner_output_dirs == [
        build_local_output_dir(str(first), str(output_base)),
        build_local_output_dir(str(second), str(output_base)),
    ]
    assert [source.name for source in prepared] == ["Floor 1", "Floor 1"]
    assert [source.slug for source in prepared] == ["floor_1", "floor_1_2"]
    assert all(source.input_format == "potree" for source in prepared)
    assert all(source.source_type == "potree_dir" for source in prepared)


def test_prepare_unicode_source_restores_original_name_in_potree_metadata(tmp_path):
    source = tmp_path / "Bäume.las"
    source.write_bytes(b"las")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    output_base = tmp_path / "converted"

    def fake_runner(_source_file, _converter_path, output_dir, _on_progress):
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as file:
            json.dump({"name": "BUME~1", "points": 1}, file)

    prepared = prepare_pointcloud_sources(
        PointcloudPreparationRequest(
            sources=(str(source),),
            converter_path=str(converter),
            output_base_dir=str(output_base),
            overwrite=True,
        ),
        converter_runner=fake_runner,
    )

    metadata_path = output_base / "baeume_potree" / "metadata.json"
    assert prepared[0].name == "Bäume"
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["name"] == "Bäume"


@pytest.mark.parametrize(
    ("source_name", "missing_field", "expected_message"),
    (
        ("cloud.las", "converter_path", "Potree Converter"),
        ("cloud.laz", "output_base_dir", "Ausgabeordner"),
    ),
)
def test_prepare_pointcloud_sources_rejects_raw_without_converter_or_output_base(
    tmp_path, source_name, missing_field, expected_message
):
    raw = tmp_path / source_name
    raw.write_bytes(b"raw")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    kwargs = {
        "sources": (str(raw),),
        "converter_path": str(converter),
        "output_base_dir": str(tmp_path / "out"),
    }
    kwargs[missing_field] = ""

    with pytest.raises(ValueError, match=expected_message):
        prepare_pointcloud_sources(PointcloudPreparationRequest(**kwargs))


def test_prepare_pointcloud_sources_rejects_missing_and_unsupported_sources_with_context(tmp_path):
    missing = tmp_path / "missing.copc.laz"
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not a pointcloud", encoding="utf-8")
    plain_dir = tmp_path / "plain-dir"
    plain_dir.mkdir()

    with pytest.raises(ValueError, match="missing\\.copc\\.laz"):
        prepare_pointcloud_sources(PointcloudPreparationRequest(sources=(str(missing),)))

    with pytest.raises(ValueError, match="notes\\.txt"):
        prepare_pointcloud_sources(PointcloudPreparationRequest(sources=(str(unsupported),)))

    with pytest.raises(ValueError, match="plain-dir"):
        prepare_pointcloud_sources(PointcloudPreparationRequest(sources=(str(plain_dir),)))


def test_classify_pointcloud_source_accepts_potree_metadata_or_cloud_js(tmp_path):
    metadata_dir = tmp_path / "metadata-potree"
    metadata_dir.mkdir()
    (metadata_dir / "metadata.json").write_text("{}", encoding="utf-8")
    cloud_js_dir = tmp_path / "cloud-js-potree"
    cloud_js_dir.mkdir()
    (cloud_js_dir / "cloud.js").write_text("cloud.js = {};", encoding="utf-8")
    copc = tmp_path / "cloud.copc.laz"
    copc.write_bytes(b"copc")
    raw = tmp_path / "cloud.las"
    raw.write_bytes(b"las")

    assert classify_pointcloud_source(str(metadata_dir)) == "potree"
    assert classify_pointcloud_source(str(cloud_js_dir)) == "potree"
    assert classify_pointcloud_source(str(copc)) == "copc"
    assert classify_pointcloud_source(str(raw)) == "raw"
