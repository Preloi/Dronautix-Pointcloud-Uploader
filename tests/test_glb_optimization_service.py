import hashlib
import json
from pathlib import Path
import struct
import base64
import zlib
from dataclasses import replace

import pytest

import dronautix_uploader.core.glb_optimization_service as optimization_module
from dronautix_uploader.core.contracts import ModelUploadInput, OperationCancelledError, PointcloudSource
from dronautix_uploader.core.glb_optimization_service import (
    BundledGLBOptimizationToolchain,
    BundledGLBCompressedAssetDecoder,
    GLBOptimizationService,
    GLBValidationError,
    _read_glb_document,
    build_model_index_entry,
    cleanup_prepared_model_uploads,
)
from dronautix_uploader.core.glb_toolchain import GLBToolchainStatus
from dronautix_uploader.core.project_operations import build_new_project_upload


PROJECT_CRS = {"value": "EPSG:25833", "vertical_crs": "EPSG:7837"}
MATRIX = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 281491.17, 5402060.19, 429.0, 1]
POSITIONS = (0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 3.0, 4.0)


def write_glb(path, document, positions=POSITIONS, binary=None):
    raw_json = json.dumps(document, separators=(",", ":")).encode()
    raw_json += b" " * (-len(raw_json) % 4)
    if binary is None:
        binary = struct.pack("<" + "f" * len(positions), *positions)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(raw_json), 0x4E4F534A) + raw_json + struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks)


def one_pixel_png(rgba, compression_level=6, color_type=6):
    components = {2: 3, 6: 4}[color_type]
    raw = bytes((0, *rgba[:components]))
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, level=compression_level)) + chunk(b"IEND", b"")


def png_data_uri(rgba, **kwargs):
    return "data:image/png;base64," + base64.b64encode(one_pixel_png(rgba, **kwargs)).decode("ascii")


def native_document(**overrides):
    document = {
        "asset": {"version": "2.0", "extras": {"dronautix_georeferencing": {
            "model_to_project_column_major": MATRIX,
            "crs": {"horizontal": {"epsg": 25833}, "vertical": {"epsg": 7837}}, "unit": "m",
        }}},
        "buffers": [{"byteLength": 36}],
        "bufferViews": [{"buffer": 0, "byteLength": 36}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [2, 3, 4]}],
        "materials": [{"name": "red", "pbrMetallicRoughness": {"metallicFactor": 0.5}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
        "nodes": [{"name": "mast", "mesh": 0, "translation": [5, -2, 1]}],
        "scenes": [{"nodes": [0]}], "scene": 0,
    }
    document.update(overrides)
    return document


def quantized_sparse_position_document(last_y=3000):
    """KHR_mesh_quantization: uint16 POSITION with stride and sparse data."""

    base = b"".join(struct.pack("<HHH", *point) + b"\0\0" for point in ((0, 0, 0), (2000, 0, 0), (0, 0, 0)))
    binary = base + b"\x02\0\0\0" + struct.pack("<HHH", 0, last_y, 4000)
    document = native_document(
        extensionsUsed=["KHR_mesh_quantization"],
        buffers=[{"byteLength": len(binary)}],
        bufferViews=[
            {"buffer": 0, "byteOffset": 0, "byteLength": len(base), "byteStride": 8},
            {"buffer": 0, "byteOffset": len(base), "byteLength": 1},
            {"buffer": 0, "byteOffset": len(base) + 4, "byteLength": 6},
        ],
        accessors=[{
            "bufferView": 0, "componentType": 5123, "count": 3, "type": "VEC3",
            "min": [0, 0, 0], "max": [2000, last_y, 4000],
            "sparse": {
                "count": 1,
                "indices": {"bufferView": 1, "componentType": 5121},
                "values": {"bufferView": 2},
            },
        }],
    )
    document["nodes"][0]["scale"] = [0.001, 0.001, 0.001]
    return document, binary


def model_input(path, **kwargs):
    return ModelUploadInput(source_path=str(path), **kwargs)


def enabled_status():
    return GLBToolchainStatus("compressed_optimization", True, True, (), "", {})


def test_stages_native_georeference_actual_bounds_and_control_points(tmp_path):
    source = tmp_path / "mast.glb"
    write_glb(source, native_document())
    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage",
        project_viewer_root="kunde/id/projekt", project_s3_prefix="pointclouds/kunde/id/projekt",
    )
    manifest = json.loads(Path(prepared.manifest_path).read_text(encoding="utf-8"))
    assert prepared.model_to_project == tuple(float(value) for value in MATRIX)
    assert manifest["model_to_project"] == MATRIX
    assert manifest["bounds"] == {"min": [281496.17, 5402058.19, 430.0], "max": [281498.17, 5402061.19, 434.0]}
    assert len(manifest["optimization"]["control_points"]) == 3
    assert prepared.optimization.original_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["original_sha256"] == prepared.optimization.original_sha256
    assert manifest["optimization"]["output_sha256"] == prepared.optimization.output_sha256
    output_sha256 = prepared.optimization.output_sha256
    assert prepared.output_sha256 == output_sha256
    assert prepared.package_sha256 == prepared.data_version
    assert prepared.package_sha256 != output_sha256
    assert prepared.index_entry.viewer_path.endswith(f"/models/mast/versions/{prepared.package_sha256}/model.json")
    assert prepared.index_entry.s3_path.endswith(f"/models/mast/versions/{prepared.package_sha256}")
    assert not prepared.index_entry.s3_path.endswith("scene.glb")
    cleanup_prepared_model_uploads((prepared,))


def test_local_live_viewer_contract_from_native_glb_to_manifest_and_models_index(tmp_path):
    source = tmp_path / "halle.glb"
    copc = tmp_path / "scan.copc.laz"
    write_glb(source, native_document())
    copc.write_bytes(b"copc")
    project_crs = {
        "value": "EPSG:25833",
        "crs_name": "ETRS89 / UTM zone 33N",
        "vertical_crs": "EPSG:7837",
        "vertical_datum": "DHHN2016 height",
    }
    viewer_root = "kunde/project/projekt"
    s3_root = "pointclouds/kunde/project/projekt"

    prepared = GLBOptimizationService().prepare(
        model_input(source),
        project_crs_info=project_crs,
        staging_root=tmp_path / "stage",
        project_viewer_root=viewer_root,
        project_s3_prefix=s3_root,
    )
    upload = build_new_project_upload(
        sources=(PointcloudSource(str(copc), name="Scan", input_format="copc", crs_info=project_crs),),
        timestamp="2026-08-20T12:00:00",
        kunde="Kunde",
        projekt="Projekt",
        project_id="project",
        project_url="https://viewer.invalid/?id=project",
        project_viewer_root=viewer_root,
        project_s3_prefix=s3_root,
        models=(prepared,),
    )

    manifest = json.loads(Path(prepared.manifest_path).read_text(encoding="utf-8"))
    model = upload.project_metadata["models"][0]
    version_root = f"{s3_root}/models/halle/versions/{prepared.package_sha256}"
    assert manifest["entrypoint"] == "scene.glb"
    assert manifest["crs"] == model["crs"] == "EPSG:25833"
    assert manifest["vertical_crs"] == model["vertical_crs"] == "EPSG:7837"
    assert manifest["crs_name"] == model["crs_name"] == "ETRS89 / UTM zone 33N"
    assert manifest["vertical_datum"] == model["vertical_datum"] == "DHHN2016 height"
    assert "(" not in manifest["vertical_crs"]
    assert manifest["model_to_project"] == MATRIX
    assert manifest["bounds"] == {"min": [281496.17, 5402058.19, 430.0], "max": [281498.17, 5402061.19, 434.0]}
    assert model["viewer_path"] == f"{viewer_root}/models/halle/versions/{prepared.package_sha256}/model.json"
    assert model["s3_path"] == version_root
    assert [key for _path, key in upload.files_to_upload[-2:]] == [
        f"{version_root}/scene.glb",
        f"{version_root}/model.json",
    ]
    cleanup_prepared_model_uploads((prepared,))


def test_glb_without_georeferencing_uses_identity_in_project_metres(tmp_path):
    source = tmp_path / "los3.glb"
    write_glb(source, native_document(asset={"version": "2.0"}))

    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage",
    )
    manifest = json.loads(Path(prepared.manifest_path).read_text(encoding="utf-8"))

    assert prepared.model_to_project == (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    assert manifest["bounds"] == {"min": [5.0, -2.0, 1.0], "max": [7.0, 1.0, 5.0]}
    assert manifest["crs"] == "EPSG:25833"
    assert manifest["vertical_crs"] == "EPSG:7837"
    cleanup_prepared_model_uploads((prepared,))


def test_embedded_matrix_is_written_unchanged(tmp_path):
    source = tmp_path / "los3.glb"
    matrix = [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 281491.17, 5402060.19, 429.0, 1]
    document = native_document()
    document["asset"]["extras"]["dronautix_georeferencing"]["model_to_project_column_major"] = matrix
    write_glb(source, document)

    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage",
    )
    manifest = json.loads(Path(prepared.manifest_path).read_text(encoding="utf-8"))

    assert prepared.model_to_project == tuple(float(value) for value in matrix)
    assert manifest["model_to_project"] == matrix
    cleanup_prepared_model_uploads((prepared,))


def test_model_json_matrix_places_glb_without_embedded_georeferencing(tmp_path):
    source = tmp_path / "los3.glb"
    write_glb(source, native_document(asset={"version": "2.0"}))
    sidecar = tmp_path / "model.json"
    sidecar.write_text(json.dumps({
        "schema_version": 1, "format": "glb", "coordinate_space": "project_local", "entrypoint": source.name,
        "model_to_project": MATRIX,
        "bounds": {"min": [281496.17, 5402058.19, 430], "max": [281498.17, 5402061.19, 434]},
    }), encoding="utf-8")

    prepared = GLBOptimizationService().prepare(
        model_input(source, model_json_path=str(sidecar)), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage",
    )

    assert prepared.model_to_project == tuple(float(value) for value in MATRIX)
    assert prepared.crs_info == {
        "value": "EPSG:25833", "projection": "EPSG:25833", "epsg": "EPSG:25833", "code": "25833",
        "vertical_crs": "EPSG:7837", "vertical_epsg": "EPSG:7837", "vertical_projection": "EPSG:7837",
    }
    cleanup_prepared_model_uploads((prepared,))


def test_explicit_model_json_without_matrix_fails_closed(tmp_path):
    source = tmp_path / "los3.glb"
    write_glb(source, native_document(asset={"version": "2.0"}))
    sidecar = tmp_path / "model.json"
    sidecar.write_text(json.dumps({
        "schema_version": 1, "format": "glb", "coordinate_space": "project_local", "entrypoint": source.name,
        "bounds": {"min": [5, -2, 1], "max": [7, 1, 5]},
    }), encoding="utf-8")

    with pytest.raises(GLBValidationError, match="16 Werte"):
        GLBOptimizationService().validate_model_upload_input(
            model_input(source, model_json_path=str(sidecar)), project_crs_info=PROJECT_CRS,
        )


def test_embedded_crs_fields_inherit_project_when_missing_and_reject_mismatch(tmp_path):
    source = tmp_path / "los3.glb"
    document = native_document()
    georeferencing = document["asset"]["extras"]["dronautix_georeferencing"]
    georeferencing.pop("crs")
    write_glb(source, document)

    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage",
    )
    assert prepared.crs_info["value"] == "EPSG:25833"
    assert prepared.crs_info["vertical_crs"] == "EPSG:7837"
    cleanup_prepared_model_uploads((prepared,))

    georeferencing["crs"] = {"horizontal": {"epsg": 25833}}
    write_glb(source, document)
    GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)

    georeferencing["crs"] = {"vertical": {"epsg": 9999}}
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match="Punktwolke"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)

    document = native_document()
    document["accessors"][0]["max"] = [2, 3, 9]
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match="Vertexdaten"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)


def test_decodes_quantized_position_with_normalized_minmax_sparse_stride_and_node_transform(tmp_path):
    source = tmp_path / "quantized.glb"
    # Quantize() emits normalized integer POSITION values in [0, 1] and puts
    # the exact counter-transform on the mesh node.
    binary = b"".join(struct.pack("<BBB", *point) + b"\0" for point in ((0, 0, 0), (255, 0, 0), (0, 255, 255)))
    document = native_document(
        extensionsUsed=["KHR_mesh_quantization"],
        buffers=[{"byteLength": len(binary)}],
        bufferViews=[{"buffer": 0, "byteLength": len(binary), "byteStride": 4}],
        accessors=[{
            "bufferView": 0, "componentType": 5121, "normalized": True, "count": 3, "type": "VEC3",
            # glTF stores extrema in the integer representation; the service
            # must compare them after normalization, not as metres.
            "min": [0, 0, 0], "max": [255, 255, 255],
        }],
    )
    document["nodes"][0]["scale"] = [2.0, 3.0, 4.0]
    write_glb(source, document, binary=binary)

    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage",
    )
    manifest = json.loads(Path(prepared.manifest_path).read_text(encoding="utf-8"))
    assert manifest["bounds"] == {"min": [281496.17, 5402058.19, 430.0], "max": [281498.17, 5402061.19, 434.0]}
    assert len(prepared.optimization.control_points) == 3
    cleanup_prepared_model_uploads((prepared,))


def test_quantized_position_supports_sparse_stride_and_rejects_over_one_millimetre_change(tmp_path):
    source = tmp_path / "quantized.glb"
    source_document, source_binary = quantized_sparse_position_document()
    source_document["asset"]["extras"]["padding"] = "x" * 1000
    write_glb(source, source_document, binary=source_binary)

    class MovedQuantizedCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "moved.glb"
            document, binary = quantized_sparse_position_document(last_y=3002)
            write_glb(candidate, document, binary=binary)
            return (("moved", candidate),)

    service = GLBOptimizationService(toolchain=MovedQuantizedCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "original"
    assert any("Bounds" in warning or "Kontrollpunkt" in warning for warning in prepared.optimization.warnings)
    cleanup_prepared_model_uploads((prepared,))


def test_quantized_position_requires_declared_extension_and_valid_normalized_flag(tmp_path):
    source = tmp_path / "invalid-quantized.glb"
    document, binary = quantized_sparse_position_document()
    document.pop("extensionsUsed")
    write_glb(source, document, binary=binary)
    with pytest.raises(GLBValidationError, match="KHR_mesh_quantization"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)

    document, binary = quantized_sparse_position_document()
    document["accessors"][0]["normalized"] = "yes"
    write_glb(source, document, binary=binary)
    with pytest.raises(GLBValidationError, match="normalized"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)


@pytest.mark.parametrize("document, message", [
    (native_document(asset={"version": "2.0", "extras": {"dronautix_georeferencing": {"unit": "cm"}}}), "Einheit Meter"),
])
def test_rejects_non_metre_embedded_georeference(tmp_path, document, message):
    source = tmp_path / "invalid.glb"
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match=message):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)


def test_rejects_wrong_pointcloud_crs_and_stale_accessor_bounds(tmp_path):
    source = tmp_path / "invalid.glb"
    document = native_document()
    document["asset"]["extras"]["dronautix_georeferencing"]["crs"]["horizontal"]["epsg"] = 25832
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match="Punktwolke"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)


@pytest.mark.parametrize(
    "project_crs",
    (
        {"value": "EPSG:25833"},
        {"value": "ETRS89 / UTM zone 33N", "vertical_crs": "EPSG:7837"},
        {"value": "EPSG:25833", "vertical_crs": "DHHN2016 height"},
    ),
)
def test_rejects_missing_or_free_project_crs_before_staging(tmp_path, project_crs):
    source = tmp_path / "invalid-project-crs.glb"
    write_glb(source, native_document())
    staging_root = tmp_path / "stage"

    with pytest.raises(GLBValidationError, match="technische|eindeutig"):
        GLBOptimizationService().prepare(
            model_input(source),
            project_crs_info=project_crs,
            staging_root=staging_root,
        )

    assert not tuple(staging_root.glob(".glb-upload-*"))


def test_non_epsg_authority_references_are_preserved_in_manifest(tmp_path):
    source = tmp_path / "authority.glb"
    document = native_document()
    georef_crs = document["asset"]["extras"]["dronautix_georeferencing"]["crs"]
    georef_crs["horizontal"] = {"urn": "urn:ogc:def:crs:IGNF::LAMB93"}
    georef_crs["vertical"] = {"uri": "https://www.opengis.net/def/crs/IGNF/0/NGF-IGN69"}
    write_glb(source, document)
    project_crs = {
        "value": "urn:ogc:def:crs:IGNF::LAMB93",
        "vertical_crs": "urn:ogc:def:crs:IGNF:0:NGF-IGN69",
    }

    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=project_crs, staging_root=tmp_path / "stage"
    )
    manifest = json.loads(Path(prepared.manifest_path).read_text(encoding="utf-8"))

    assert manifest["crs"] == "urn:ogc:def:crs:IGNF::LAMB93"
    assert manifest["vertical_crs"] == "urn:ogc:def:crs:IGNF:0:NGF-IGN69"
    cleanup_prepared_model_uploads((prepared,))


def test_embedded_and_model_json_matrices_must_match(tmp_path):
    source = tmp_path / "scene.glb"
    write_glb(source, native_document())
    sidecar = tmp_path / "manifest.json"
    payload = {
        "schema_version": 1, "format": "glb", "coordinate_space": "project_local", "entrypoint": "scene.glb",
        "model_to_project": MATRIX,
        "bounds": {"min": [281496.17, 5402058.19, 430], "max": [281498.17, 5402061.19, 434]},
        "crs": "EPSG:25833", "vertical_crs": "EPSG:7837",
    }
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    GLBOptimizationService().validate_model_upload_input(model_input(source, model_json_path=str(sidecar)), project_crs_info=PROJECT_CRS)
    payload["model_to_project"][12] += 0.01
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GLBValidationError, match="weicht"):
        GLBOptimizationService().validate_model_upload_input(model_input(source, model_json_path=str(sidecar)), project_crs_info=PROJECT_CRS)


def test_precision_localization_must_prove_embedded_placement_and_inverse(tmp_path):
    source = tmp_path / "localized.glb"
    document = native_document()
    georef = document["asset"]["extras"]["dronautix_georeferencing"]
    embedded_matrix = list(MATRIX)
    georef["model_to_project_column_major"] = embedded_matrix
    georef["precision_localization"] = {
        "local_to_native_column_major": list(embedded_matrix),
        "native_to_local_column_major": [
            1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
            -embedded_matrix[12], -embedded_matrix[13], -embedded_matrix[14], 1,
        ],
    }
    write_glb(source, document)
    GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)

    georef["precision_localization"]["local_to_native_column_major"][12] += 10
    georef["precision_localization"]["native_to_local_column_major"][12] -= 10
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match="exakt model_to_project"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)

    georef["precision_localization"]["local_to_native_column_major"] = list(embedded_matrix)
    georef["precision_localization"]["native_to_local_column_major"] = [
        1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
        -embedded_matrix[12], -embedded_matrix[13], -embedded_matrix[14], 1,
    ]
    georef["precision_localization"]["native_to_local_column_major"][12] = -9.99
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match="Gegenmatrix"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)

    georef.pop("model_to_project_column_major")
    georef["precision_localization"] = {
        "local_to_native_column_major": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "native_to_local_column_major": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    }
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match="eingebettete model_to_project"):
        GLBOptimizationService().validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)


def test_candidate_vertex_change_is_rejected_from_actual_stable_control_points(tmp_path):
    source = tmp_path / "source.glb"
    source_document = native_document(extras={"padding": "x" * 1000})
    source_document["buffers"][0]["byteLength"] = source_document["bufferViews"][0]["byteLength"] = 48
    source_document["accessors"][0]["count"] = 4
    write_glb(source, source_document, (0, 0, 0, 2, 0, 0, 0, 3, 4, 2, 3, 4))

    class MovingCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "moved.glb"
            document = native_document()
            document["buffers"][0]["byteLength"] = document["bufferViews"][0]["byteLength"] = 48
            document["accessors"][0]["count"] = 4
            write_glb(candidate, document, (0, 0, 0, 2, 0, 0.002, 0, 3, 4, 2, 3, 4))
            return (("moved", candidate),)

    service = GLBOptimizationService(toolchain=MovingCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "original"
    assert any("Kontrollpunkt" in warning for warning in prepared.optimization.warnings), prepared.optimization.warnings
    cleanup_prepared_model_uploads((prepared,))


def test_full_triangle_signature_rejects_non_control_vertex_change(tmp_path):
    source = tmp_path / "source.glb"
    points = (0, 0, 0, 10, 10, 0, 10, 0, 0, 0, 10, 0, 5, 5, 0, 4, 5, 0)
    source_document = native_document(extras={"padding": "x" * 2000})
    source_document["buffers"][0]["byteLength"] = source_document["bufferViews"][0]["byteLength"] = 72
    source_document["accessors"][0].update({"count": 6, "max": [10, 10, 0]})
    write_glb(source, source_document, points)

    class MovingInteriorVertex:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "changed.glb"
            document = native_document()
            document["buffers"][0]["byteLength"] = document["bufferViews"][0]["byteLength"] = 72
            document["accessors"][0].update({"count": 6, "max": [10, 10, 0]})
            write_glb(candidate, document, (0, 0, 0, 10, 10, 0, 10, 0, 0, 0, 10, 0, 5.02, 5, 0, 4, 5, 0))
            return (("changed", candidate),)

    service = GLBOptimizationService(toolchain=MovingInteriorVertex())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "original"
    assert any("Vertex-Attribute" in warning for warning in prepared.optimization.warnings)
    cleanup_prepared_model_uploads((prepared,))


def test_full_triangle_signature_allows_triangle_reordering(tmp_path):
    source = tmp_path / "source.glb"
    points = (0, 0, 0, 10, 10, 0, 10, 0, 0, 0, 10, 0, 5, 5, 0, 4, 5, 0)
    source_document = native_document(extras={"padding": "x" * 2000})
    source_document["buffers"][0]["byteLength"] = source_document["bufferViews"][0]["byteLength"] = 72
    source_document["accessors"][0].update({"count": 6, "max": [10, 10, 0]})
    write_glb(source, source_document, points)

    class ReorderedTriangles:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "reordered.glb"
            document = native_document()
            document["buffers"][0]["byteLength"] = document["bufferViews"][0]["byteLength"] = 72
            document["accessors"][0].update({"count": 6, "max": [10, 10, 0]})
            write_glb(candidate, document, points[9:] + points[:9])
            return (("reordered", candidate),)

    service = GLBOptimizationService(toolchain=ReorderedTriangles())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "reordered"
    cleanup_prepared_model_uploads((prepared,))


def test_candidate_selection_checks_smallest_first_and_reuses_its_inspection(tmp_path, monkeypatch):
    source = tmp_path / "source.glb"
    source_document = native_document()
    source_document["asset"]["extras"]["padding"] = "x" * 4000
    write_glb(source, source_document)

    class UnsortedCandidates:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            larger = output_dir / "larger.glb"
            larger_document = native_document()
            larger_document["asset"]["extras"]["padding"] = "x" * 2000
            write_glb(larger, larger_document)
            smallest = output_dir / "smallest.glb"
            write_glb(smallest, native_document())
            return (("larger", larger), ("smallest", smallest))

    service = GLBOptimizationService(toolchain=UnsortedCandidates())
    service._toolchain_status = enabled_status()
    inspected = []
    original_inspect = service._inspect_glb

    def record_inspection(path, *args, **kwargs):
        inspected.append(Path(path).name)
        return original_inspect(path, *args, **kwargs)

    monkeypatch.setattr(service, "_inspect_glb", record_inspection)
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")

    assert prepared.optimization.selected_candidate == "smallest"
    assert "smallest.glb" in inspected
    assert "larger.glb" not in inspected
    assert "scene.glb" not in inspected
    cleanup_prepared_model_uploads((prepared,))


def test_original_semantic_signature_is_computed_once_for_multiple_rejected_candidates(tmp_path, monkeypatch):
    source = tmp_path / "source.glb"
    source_document = native_document()
    source_document["asset"]["extras"]["padding"] = "x" * 4000
    write_glb(source, source_document)

    class InvalidCandidates:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            result = []
            for name in ("changed-a", "changed-b"):
                candidate = output_dir / f"{name}.glb"
                document = native_document(materials=[{"name": name}])
                write_glb(candidate, document)
                result.append((name, candidate))
            return tuple(result)

    calls = []
    original_signature = optimization_module._preservation_signature

    def record_signature(inspection):
        calls.append(Path(inspection.path).name)
        return original_signature(inspection)

    monkeypatch.setattr(optimization_module, "_preservation_signature", record_signature)
    service = GLBOptimizationService(toolchain=InvalidCandidates())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")

    assert prepared.optimization.selected_candidate == "original"
    assert calls.count("original.glb") == 1
    cleanup_prepared_model_uploads((prepared,))


def test_textureless_model_skips_ktx2_candidate_validation(tmp_path):
    source = tmp_path / "source.glb"
    source_document = native_document()
    source_document["asset"]["extras"]["padding"] = "x" * 4000
    write_glb(source, source_document)

    class TexturelessCandidates:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            ktx2 = output_dir / "ktx2.glb"
            conservative = output_dir / "conservative.glb"
            write_glb(ktx2, native_document())
            write_glb(conservative, native_document(extras={"padding": "x" * 100}))
            return (("ktx2", ktx2), ("conservative", conservative))

    service = GLBOptimizationService(toolchain=TexturelessCandidates())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")

    assert prepared.optimization.selected_candidate == "conservative"
    cleanup_prepared_model_uploads((prepared,))


def test_non_triangle_primitive_candidate_fails_closed_on_changed_positions(tmp_path):
    source = tmp_path / "points.glb"
    points = (0, 0, 0, 10, 10, 0, 10, 0, 0, 0, 10, 0, 5, 5, 0, 4, 5, 0)
    source_document = native_document(extras={"padding": "x" * 2000})
    source_document["buffers"][0]["byteLength"] = source_document["bufferViews"][0]["byteLength"] = 72
    source_document["accessors"][0].update({"count": 6, "max": [10, 10, 0]})
    source_document["meshes"][0]["primitives"][0]["mode"] = 0
    write_glb(source, source_document, points)

    class MovingPointCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "changed-points.glb"
            document = native_document()
            document["buffers"][0]["byteLength"] = document["bufferViews"][0]["byteLength"] = 72
            document["accessors"][0].update({"count": 6, "max": [10, 10, 0]})
            document["meshes"][0]["primitives"][0]["mode"] = 0
            write_glb(candidate, document, (0, 0, 0, 10, 10, 0, 10, 0, 0, 0, 10, 0, 5.02, 5, 0, 4, 5, 0))
            return (("changed", candidate),)

    service = GLBOptimizationService(toolchain=MovingPointCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "original"
    assert any("Vertex-Attribute" in warning for warning in prepared.optimization.warnings)
    cleanup_prepared_model_uploads((prepared,))


def test_candidate_with_equivalent_renumbered_material_texture_arrays_is_accepted(tmp_path):
    source = tmp_path / "source.glb"
    original = native_document(extras={"padding": "x" * 2000})
    original.update({
        "images": [{"uri": "data:application/octet-stream;base64,AQID"}, {"uri": "data:application/octet-stream;base64,BAUG"}],
        "textures": [{"source": 0}, {"source": 1}],
        "materials": [
            {"name": "unused", "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}},
            {"name": "red", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
        ],
    })
    original["meshes"][0]["primitives"][0]["material"] = 1
    write_glb(source, original)

    class RenumberingCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "renumbered.glb"
            document = native_document()
            document.update({
                "images": [original["images"][1], original["images"][0]],
                "textures": [{"source": 0}, {"source": 1}],
                "materials": [original["materials"][1], original["materials"][0]],
            })
            document["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["index"] = 1
            document["materials"][1]["pbrMetallicRoughness"]["baseColorTexture"]["index"] = 0
            document["meshes"][0]["primitives"][0]["material"] = 0
            write_glb(candidate, document)
            return (("renumbered", candidate),)

    service = GLBOptimizationService(toolchain=RenumberingCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "renumbered"
    cleanup_prepared_model_uploads((prepared,))


def test_candidate_may_losslessly_bake_a_one_pixel_base_color_swatch(tmp_path):
    source = tmp_path / "source.glb"
    source_document = native_document(extras={"padding": "x" * 2000})
    encoded = base64.b64encode(one_pixel_png((255, 255, 0, 255))).decode("ascii")
    source_document["images"] = [{"mimeType": "image/png", "uri": f"data:image/png;base64,{encoded}"}]
    source_document["textures"] = [{"source": 0}]
    source_document["materials"] = [{"name": "yellow", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "baseColorTexture": {"index": 0}}}]
    write_glb(source, source_document)

    class BakedCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "baked.glb"
            document = native_document(materials=[{"name": "yellow", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 0, 1]}}])
            write_glb(candidate, document)
            return (("baked", candidate),)

    service = GLBOptimizationService(toolchain=BakedCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "baked"
    cleanup_prepared_model_uploads((prepared,))


def test_candidate_that_drops_animation_skin_or_morph_semantics_is_rejected(tmp_path):
    source = tmp_path / "animated.glb"
    document = native_document(extras={"padding": "x" * 2000})
    document["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
    document["nodes"][0]["skin"] = 0
    document["skins"] = [{"joints": [0]}]
    document["animations"] = [{"samplers": [], "channels": []}]
    write_glb(source, document)

    class DroppingCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "dropped.glb"
            write_glb(candidate, native_document())
            return (("dropped", candidate),)

    service = GLBOptimizationService(toolchain=DroppingCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "original"
    assert any("Animationen, Skins, Morph Targets" in warning for warning in prepared.optimization.warnings)
    cleanup_prepared_model_uploads((prepared,))


@pytest.mark.parametrize("extension", ["KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_texture_basisu"])
def test_compressed_input_requires_sealed_bundled_decoder(tmp_path, extension):
    source = tmp_path / "compressed.glb"
    write_glb(source, native_document(extensionsUsed=[extension]))
    capabilities = tmp_path / "viewer-capabilities.json"
    capabilities.write_text(json.dumps({
        "schema_version": 1,
        "decoders": {"draco": True, "meshopt": True, "ktx2_basisu": True, "webp": True},
        "supported_extensions": [extension],
    }), encoding="utf-8")
    with pytest.raises(GLBValidationError, match="versiegelter gebündelter Decoder|Decoder-Runner"):
        GLBOptimizationService(resource_root=tmp_path, capabilities_path=capabilities).validate_model_upload_input(
            model_input(source), project_crs_info=PROJECT_CRS,
        )


@pytest.mark.parametrize(("required_extensions", "decoders", "message"), [
    (
        ["KHR_draco_mesh_compression", "VENDOR_unknown_required"],
        {"draco": True, "meshopt": True, "ktx2_basisu": True, "webp": True},
        "nicht bestätigte Erweiterung",
    ),
    (
        ["KHR_draco_mesh_compression"],
        {"draco": False, "meshopt": True, "ktx2_basisu": True, "webp": True},
        "nicht unterstützt",
    ),
])
def test_compressed_original_is_capability_checked_before_decoder(
    tmp_path, required_extensions, decoders, message,
):
    source = tmp_path / "compressed.glb"
    write_glb(source, native_document(
        extensionsUsed=required_extensions,
        extensionsRequired=required_extensions,
    ))
    capabilities = tmp_path / "viewer-capabilities.json"
    capabilities.write_text(json.dumps({
        "schema_version": 1,
        "decoders": decoders,
        "supported_extensions": ["KHR_draco_mesh_compression"],
    }), encoding="utf-8")

    class MustNotDecode:
        def decode(self, *args, **kwargs):
            pytest.fail("Decoder darf vor der Capability-Prüfung nicht laufen")

    service = GLBOptimizationService(capabilities_path=capabilities, compressed_decoder=MustNotDecode())
    with pytest.raises(GLBValidationError, match=message):
        service.validate_model_upload_input(model_input(source), project_crs_info=PROJECT_CRS)


def test_meshopt_virtual_buffer_is_accepted_only_with_bounded_compressed_source(tmp_path):
    source = tmp_path / "meshopt.glb"
    document = native_document(
        extensionsUsed=["EXT_meshopt_compression"],
        extensionsRequired=["EXT_meshopt_compression"],
    )
    document["buffers"] = [
        {"byteLength": 36},
        {"byteLength": 36, "extensions": {"EXT_meshopt_compression": {"fallback": True}}},
    ]
    document["bufferViews"] = [{
        "buffer": 1,
        "byteLength": 36,
        "extensions": {"EXT_meshopt_compression": {
            "buffer": 0, "byteOffset": 0, "byteLength": 36,
            "byteStride": 12, "count": 3, "mode": "ATTRIBUTES",
        }},
    }]
    write_glb(source, document)

    assert _read_glb_document(source)["buffers"][1]["extensions"]["EXT_meshopt_compression"]["fallback"] is True

    document["bufferViews"][0]["extensions"]["EXT_meshopt_compression"]["byteLength"] = 37
    write_glb(source, document)
    with pytest.raises(GLBValidationError, match="außerhalb des komprimierten Buffers"):
        _read_glb_document(source)


def test_bundled_optimizer_offers_conservative_then_each_explicit_codec(tmp_path, monkeypatch):
    source = tmp_path / "source.glb"
    source.write_bytes(b"source")
    seen = []

    def run(_root, runner, arguments, _cancel):
        assert runner == "optimizer"
        seen.append(arguments[0])
        Path(arguments[-1]).write_bytes(arguments[0].encode("ascii"))

    monkeypatch.setattr(optimization_module, "get_glb_toolchain_status", lambda _root: enabled_status())
    monkeypatch.setattr(optimization_module, "_run_bundled_runner", run)
    candidates = tuple(BundledGLBOptimizationToolchain(tmp_path).optimize_candidates(source, tmp_path))

    assert seen == ["conservative", "meshopt", "draco", "ktx2"]
    assert [name for name, _path in candidates] == seen


def test_bundled_decoder_passes_extensions_to_local_decoder_runner(tmp_path, monkeypatch):
    source = tmp_path / "compressed.glb"
    source.write_bytes(b"source")
    runner = tmp_path / "decode-glb.mjs"
    runner.write_text("// local runner", encoding="utf-8")
    seen = []

    def run(_root, runner_id, arguments, _cancel):
        assert runner_id == "decoder"
        seen.append(arguments)
        Path(arguments[-1]).write_bytes(b"decoded")

    monkeypatch.setattr(optimization_module, "get_glb_toolchain_status", lambda _root: enabled_status())
    monkeypatch.setattr(optimization_module, "get_bundled_runner_path", lambda _runner, _root: runner)
    monkeypatch.setattr(optimization_module, "_run_bundled_runner", run)
    decoded = BundledGLBCompressedAssetDecoder(tmp_path).decode(
        source,
        ("KHR_draco_mesh_compression", "KHR_texture_basisu"),
        tmp_path,
    )

    assert decoded.read_bytes() == b"decoded"
    assert seen == [("decode", "KHR_draco_mesh_compression,KHR_texture_basisu", str(source), str(decoded))]


def test_cancellation_cleans_stage(tmp_path):
    source = tmp_path / "source.glb"
    write_glb(source, native_document())
    calls = []
    def cancelled():
        calls.append(1)
        return len(calls) >= 2
    with pytest.raises(OperationCancelledError):
        GLBOptimizationService().prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage", cancel_requested=cancelled)
    assert list((tmp_path / "stage").glob(".glb-upload-*")) == []


def test_model_index_requires_safe_roots(tmp_path):
    source = tmp_path / "source.glb"
    write_glb(source, native_document())
    prepared = GLBOptimizationService().prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    with pytest.raises(GLBValidationError, match="nicht sicher"):
        build_model_index_entry(prepared, project_viewer_root="../bad", project_s3_prefix="pointclouds/kunde")
    cleanup_prepared_model_uploads((prepared,))


@pytest.mark.parametrize("invalid_hash", ("", "a" * 63, "g" * 64))
def test_model_index_rejects_missing_or_noncanonical_data_version_before_upload(tmp_path, invalid_hash):
    source = tmp_path / "source.glb"
    write_glb(source, native_document())
    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage"
    )
    invalid = replace(
        prepared,
        data_version=invalid_hash,
        index_entry=None,
    )
    with pytest.raises(ValueError, match="data_version"):
        build_model_index_entry(
            invalid,
            project_viewer_root="kunde/id/projekt",
            project_s3_prefix="pointclouds/kunde/id/projekt",
        )
    cleanup_prepared_model_uploads((prepared,))


def test_same_output_content_reuses_hash_path_and_changed_content_gets_new_path(tmp_path):
    source = tmp_path / "source.glb"
    write_glb(source, native_document())
    service = GLBOptimizationService()
    first = service.prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage-a",
        project_viewer_root="kunde/id/projekt", project_s3_prefix="pointclouds/kunde/id/projekt",
    )
    same = service.prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage-b",
        project_viewer_root="kunde/id/projekt", project_s3_prefix="pointclouds/kunde/id/projekt",
    )
    changed_positions = (0.0, 0.0, 0.0, 2.01, 0.0, 0.0, 0.0, 3.0, 4.0)
    changed_document = native_document()
    changed_document["accessors"][0]["max"] = [2.01, 3, 4]
    write_glb(source, changed_document, positions=changed_positions)
    changed = service.prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage-c",
        project_viewer_root="kunde/id/projekt", project_s3_prefix="pointclouds/kunde/id/projekt",
    )

    assert first.output_sha256 == same.output_sha256
    assert first.index_entry == same.index_entry
    assert changed.output_sha256 != first.output_sha256
    assert changed.index_entry.s3_path != first.index_entry.s3_path
    cleanup_prepared_model_uploads((first, same, changed))


def test_model_package_hash_is_canonical_lowercase(tmp_path):
    source = tmp_path / "source.glb"
    write_glb(source, native_document())
    prepared = GLBOptimizationService().prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage"
    )
    uppercase = replace(
        prepared,
        data_version="A" * 64,
        index_entry=None,
    )
    entry = build_model_index_entry(
        uppercase,
        project_viewer_root="kunde/id/projekt",
        project_s3_prefix="pointclouds/kunde/id/projekt",
    )
    assert uppercase.package_sha256 == "a" * 64
    assert entry.s3_path.endswith("/versions/" + "a" * 64)
    cleanup_prepared_model_uploads((prepared,))


def test_manifest_only_crs_change_gets_new_package_path_with_identical_scene(tmp_path):
    source = tmp_path / "source.glb"
    write_glb(source, native_document(asset={"version": "2.0"}))
    service = GLBOptimizationService()
    first = service.prepare(
        model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage-a",
        project_viewer_root="kunde/id/projekt", project_s3_prefix="pointclouds/kunde/id/projekt",
    )
    changed = service.prepare(
        model_input(source),
        project_crs_info={"value": "EPSG:25832", "vertical_crs": "EPSG:7837"},
        staging_root=tmp_path / "stage-b",
        project_viewer_root="kunde/id/projekt", project_s3_prefix="pointclouds/kunde/id/projekt",
    )

    assert first.output_sha256 == changed.output_sha256
    assert first.package_sha256 != changed.package_sha256
    assert first.index_entry.s3_path != changed.index_entry.s3_path
    cleanup_prepared_model_uploads((first, changed))


def test_bundled_adapter_resolves_relative_source_and_output_paths(monkeypatch, tmp_path):
    import dronautix_uploader.core.glb_optimization_service as module

    monkeypatch.chdir(tmp_path)
    Path("input.glb").write_bytes(b"input")
    Path("out").mkdir()
    recorded = []

    def runner(resource_root, runner_id, arguments, cancel_requested):
        del resource_root, runner_id, cancel_requested
        recorded.append(arguments)
        Path(arguments[-1]).write_bytes(b"output")

    monkeypatch.setattr(module, "get_glb_toolchain_status", lambda _root: enabled_status())
    monkeypatch.setattr(module, "_run_bundled_runner", runner)
    candidates = tuple(BundledGLBOptimizationToolchain(tmp_path).optimize_candidates(Path("input.glb"), Path("out")))
    assert len(candidates) == 4
    assert all(Path(arguments[1]).is_absolute() and Path(arguments[2]).is_absolute() for arguments in recorded)


def test_candidate_accepts_same_decoded_texture_pixels_in_a_different_png_container(tmp_path):
    source = tmp_path / "source.glb"
    source_document = native_document(extras={"padding": "x" * 2000})
    rgba_png = one_pixel_png((128, 128, 255, 255), compression_level=1, color_type=6)
    rgb_png = one_pixel_png((128, 128, 255, 255), compression_level=9, color_type=2)
    assert rgba_png != rgb_png
    source_document.update({
        "images": [{"mimeType": "image/png", "uri": "data:image/png;base64," + base64.b64encode(rgba_png).decode("ascii")}],
        "textures": [{"source": 0}],
        "materials": [{
            "name": "normal-mapped",
            "normalTexture": {"index": 0, "extensions": {"KHR_texture_transform": {"offset": [0.25, 0.5], "scale": [0.5, 0.5]}}},
            "pbrMetallicRoughness": {},
        }],
    })
    write_glb(source, source_document)

    class EquivalentPixelsCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            candidate = output_dir / "equivalent-pixels.glb"
            document = native_document()
            document.update({
                "images": [{"mimeType": "image/png", "uri": "data:image/png;base64," + base64.b64encode(rgb_png).decode("ascii")}],
                "textures": [{"source": 0}],
                "materials": [{
                    "name": "normal-mapped",
                    "normalTexture": {"index": 0, "extensions": {"KHR_texture_transform": {"offset": [0.25, 0.5], "scale": [0.5, 0.5]}}},
                    "pbrMetallicRoughness": {},
                }],
            })
            write_glb(candidate, document)
            return (("equivalent-pixels", candidate),)

    service = GLBOptimizationService(toolchain=EquivalentPixelsCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "equivalent-pixels"
    cleanup_prepared_model_uploads((prepared,))


def test_candidate_rejects_black_alpha_or_normal_map_texture_loss(tmp_path):
    source = tmp_path / "source.glb"
    source_document = native_document(extras={"padding": "x" * 2000})
    source_document.update({
        "images": [
            {"mimeType": "image/png", "uri": png_data_uri((0, 0, 0, 0))},
            {"mimeType": "image/png", "uri": png_data_uri((128, 128, 255, 255))},
        ],
        "textures": [{"source": 0}, {"source": 1}],
        "materials": [{
            "name": "transparent-black-normal-mapped",
            "normalTexture": {"index": 1},
            "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
        }],
    })
    write_glb(source, source_document)

    class LossyTexturesCandidate:
        def optimize_candidates(self, source_path, output_dir, cancel_requested=None):
            alpha_loss = output_dir / "alpha-loss.glb"
            alpha_document = native_document()
            alpha_document.update({
                "images": [
                    {"mimeType": "image/png", "uri": png_data_uri((0, 0, 0, 255))},
                    source_document["images"][1],
                ],
                "textures": [{"source": 0}, {"source": 1}],
                "materials": [source_document["materials"][0]],
            })
            write_glb(alpha_loss, alpha_document)

            normal_loss = output_dir / "normal-loss.glb"
            normal_document = native_document()
            normal_document.update({
                "images": source_document["images"],
                "textures": source_document["textures"],
                "materials": [{
                    "name": "transparent-black-normal-mapped",
                    "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
                }],
            })
            write_glb(normal_loss, normal_document)
            return (("alpha-loss", alpha_loss), ("normal-loss", normal_loss))

    service = GLBOptimizationService(toolchain=LossyTexturesCandidate())
    service._toolchain_status = enabled_status()
    prepared = service.prepare(model_input(source), project_crs_info=PROJECT_CRS, staging_root=tmp_path / "stage")
    assert prepared.optimization.selected_candidate == "original"
    assert any("alpha-loss" in warning for warning in prepared.optimization.warnings)
    assert any("normal-loss" in warning for warning in prepared.optimization.warnings)
    cleanup_prepared_model_uploads((prepared,))
