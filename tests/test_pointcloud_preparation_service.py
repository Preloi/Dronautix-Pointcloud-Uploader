import os
import json
from pathlib import Path
import struct

import pytest

from dronautix_uploader.core.contracts import PointcloudSource, ProgressEvent
from dronautix_uploader.core.local_conversion_service import build_local_output_dir
from dronautix_uploader.core.pointcloud_preparation_service import (
    PointcloudPreparationRequest,
    classify_pointcloud_source,
    prepare_pointcloud_sources,
)


def write_valid_potree(directory, *, legacy=False, name="Cloud"):
    directory.mkdir(parents=True, exist_ok=True)
    if legacy:
        (directory / "cloud.js").write_text(
            'cloud.js = {"version":"1.7","octreeDir":"octree","points":1,"pointAttributes":["POSITION_CARTESIAN"],"boundingBox":{"lx":0,"ly":0,"lz":0,"ux":1,"uy":1,"uz":1}};', encoding="utf-8"
        )
        data = directory / "octree" / "r"
        data.mkdir(parents=True)
        (data / "r.hrc").write_bytes(b"h")
        (data / "r.bin").write_bytes(b"o")
    else:
        (directory / "metadata.json").write_text(
            json.dumps({"version":"2.0","encoding":"BROTLI","points":1,"offset":[0,0,0],"scale":[0.001,0.001,0.001],"hierarchy":{"firstChunkSize":22,"stepSize":4,"depth":0},"attributes":[{"name":"position","type":"int32","numElements":3,"elementSize":4,"size":12}],"boundingBox":{"min":[0,0,0],"max":[1,1,1]},"name":name}),
            encoding="utf-8",
        )
        (directory / "octree.bin").write_bytes(b"o")
        (directory / "hierarchy.bin").write_bytes(struct.pack("<BBIQQ", 1, 0, 1, 0, 1))


def test_prepare_pointcloud_sources_accepts_potree_and_converts_raw(tmp_path):
    potree_dir = tmp_path / "Potree Cloud"
    write_valid_potree(potree_dir)
    raw = tmp_path / "Scan.laz"
    raw.write_bytes(b"laz")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    output_base = tmp_path / "converted"
    events = []
    runner_calls = []

    def fake_runner(source_file, converter_path, output_dir, on_progress):
        runner_calls.append((source_file, converter_path, output_dir, on_progress))
        write_valid_potree(Path(output_dir))
        if on_progress:
            on_progress(ProgressEvent(kind="log", message="runner progress"))

    prepared = prepare_pointcloud_sources(
        PointcloudPreparationRequest(
            sources=(str(potree_dir), str(raw)),
            converter_path=str(converter),
            output_base_dir=str(output_base),
            overwrite=True,
        ),
        on_progress=events.append,
        converter_runner=fake_runner,
    )

    assert prepared == (
        PointcloudSource(
            source_path=str(potree_dir),
            name="Potree Cloud",
            slug="potree_cloud",
            input_format="potree",
            source_type="potree_dir",
        ),
        PointcloudSource(
            source_path=build_local_output_dir(str(raw), str(output_base), unique_name="scan"),
            name="Scan",
            slug="scan",
            input_format="potree",
            source_type="potree_dir",
        ),
    )
    assert runner_calls[0][:2] == (str(raw), str(converter))
    assert Path(runner_calls[0][2]).parent == output_base
    assert runner_calls[0][3] == events.append
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
        write_valid_potree(Path(output_dir))

    prepared = prepare_pointcloud_sources(
        PointcloudPreparationRequest(
            sources=(str(first), str(second)),
            converter_path=str(converter),
            output_base_dir=str(output_base),
            overwrite=True,
        ),
        converter_runner=fake_runner,
    )

    assert len(set(runner_output_dirs)) == 2
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
        write_valid_potree(Path(output_dir), name="BUME~1")

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


def test_classify_pointcloud_source_accepts_potree2_and_rejects_potree1(tmp_path):
    metadata_dir = tmp_path / "metadata-potree"
    write_valid_potree(metadata_dir)
    cloud_js_dir = tmp_path / "cloud-js-potree"
    write_valid_potree(cloud_js_dir, legacy=True)
    copc = tmp_path / "cloud.copc.laz"
    copc.write_bytes(b"copc")
    raw = tmp_path / "cloud.las"
    raw.write_bytes(b"las")

    assert classify_pointcloud_source(str(metadata_dir)) == "potree"
    with pytest.raises(ValueError, match=r"Potree 1.*PotreeConverter 2\.x.*metadata\.json"):
        classify_pointcloud_source(str(cloud_js_dir))
    with pytest.raises(ValueError, match="COPC-Dateien werden nicht unterstuetzt"):
        classify_pointcloud_source(str(copc))
    assert classify_pointcloud_source(str(raw)) == "raw"


def test_classify_rejects_metadata_without_potree_data(tmp_path):
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "metadata.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="kein Potree-Projekt"):
        classify_pointcloud_source(str(invalid))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("encoding", "x"),
        ("offset", [0, 0]),
        ("scale", [0.0, 0.001, 0.001]),
        ("attributes", []),
        ("boundingBox", {}),
        ("hierarchy", {"firstChunkSize": 44, "stepSize": 4, "depth": 0}),
    ),
)
def test_classify_rejects_structurally_invalid_potree2_even_with_data_files(tmp_path, field, value):
    invalid = tmp_path / field
    write_valid_potree(invalid)
    metadata = json.loads((invalid / "metadata.json").read_text(encoding="utf-8"))
    metadata[field] = value
    (invalid / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="kein Potree-Projekt"):
        classify_pointcloud_source(str(invalid))


def test_classify_rejects_potree2_hierarchy_referencing_truncated_octree(tmp_path):
    invalid = tmp_path / "truncated"
    write_valid_potree(invalid)
    (invalid / "hierarchy.bin").write_bytes(struct.pack("<BBIQQ", 1, 0, 1, 0, 64))

    with pytest.raises(ValueError, match="kein Potree-Projekt"):
        classify_pointcloud_source(str(invalid))


def test_prepare_pointcloud_sources_rejects_copc_before_conversion(tmp_path):
    source = tmp_path / "cloud.copc.laz"
    source.write_bytes(b"copc")

    with pytest.raises(ValueError, match="COPC-Dateien werden nicht unterstuetzt"):
        prepare_pointcloud_sources(PointcloudPreparationRequest(sources=(str(source),)))
