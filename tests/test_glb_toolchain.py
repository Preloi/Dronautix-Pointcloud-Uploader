import base64
import json
from pathlib import Path
import struct
import subprocess

import pytest

import dronautix_uploader.core.glb_toolchain as toolchain_module
from dronautix_uploader.core.glb_toolchain import (
    REQUIRED_DECODER_IDS,
    REQUIRED_RUNNER_IDS,
    REQUIRED_TOOL_IDS,
    UNCOMPRESSED_FALLBACK_MODE,
    get_glb_toolchain_status,
    get_bundled_runner_path,
    get_bundled_tool_path,
    load_toolchain_manifest,
    load_viewer_capabilities,
    validate_glb_toolchain_for_packaging,
)
from dronautix_uploader.core.glb_optimization_service import BundledGLBCompressedAssetDecoder, GLBValidationError


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_unsealed_toolchain_fails_closed_without_using_global_node_or_npm(tmp_path):
    source_bundle = REPO_ROOT / "bundled_tools" / "GLBToolchain"
    bundle = tmp_path / "bundled_tools" / "GLBToolchain"
    bundle.mkdir(parents=True)
    (bundle / "viewer-capabilities.v1.json").write_text(
        (source_bundle / "viewer-capabilities.v1.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = json.loads((source_bundle / "toolchain-manifest.v1.json").read_text(encoding="utf-8"))
    manifest["bundle_state"] = "unsealed"
    (bundle / "toolchain-manifest.v1.json").write_text(json.dumps(manifest), encoding="utf-8")

    status = get_glb_toolchain_status(tmp_path)

    assert status.mode == UNCOMPRESSED_FALLBACK_MODE
    assert status.use_uncompressed_fallback is True
    assert status.toolchain_available is False
    assert status.missing_tools == REQUIRED_TOOL_IDS
    assert status.integrity_errors == ("Bundle ist nicht versiegelt.",)
    assert "unveraendertes, selbststaendiges GLB" in status.fallback_reason

    with pytest.raises(GLBValidationError, match="versiegelter gebündelter Decoder"):
        BundledGLBCompressedAssetDecoder(tmp_path).decode(tmp_path / "input.glb", (), tmp_path)


def test_toolchain_integrity_and_selftest_are_cached_once_per_resource_root(monkeypatch, tmp_path):
    """Separate bundled roots must be tested once each, not once per caller."""

    source_bundle = REPO_ROOT / "bundled_tools" / "GLBToolchain"
    roots = (tmp_path / "first", tmp_path / "second")
    for root in roots:
        bundle = root / "bundled_tools" / "GLBToolchain"
        bundle.mkdir(parents=True)
        for name in ("viewer-capabilities.v1.json", "toolchain-manifest.v1.json"):
            (bundle / name).write_bytes((source_bundle / name).read_bytes())

    calls = {"integrity": 0, "selftest": 0}

    def integrity(_manifest, _toolchain_dir):
        calls["integrity"] += 1
        return ()

    def selftest(_manifest, _toolchain_dir):
        calls["selftest"] += 1
        return ()

    monkeypatch.setattr(toolchain_module, "_missing_tool_ids", lambda _manifest, _toolchain_dir: ())
    monkeypatch.setattr(toolchain_module, "_verify_manifest_integrity", integrity)
    monkeypatch.setattr(toolchain_module, "_run_local_self_tests", selftest)
    toolchain_module._reset_glb_toolchain_status_cache_for_tests()
    try:
        first = get_glb_toolchain_status(roots[0])
        assert first is get_glb_toolchain_status(roots[0] / ".")
        second = get_glb_toolchain_status(roots[1])
        assert second is get_glb_toolchain_status(roots[1])
    finally:
        toolchain_module._reset_glb_toolchain_status_cache_for_tests()

    assert first.toolchain_available is True
    assert second.toolchain_available is True
    assert calls == {"integrity": 2, "selftest": 2}


def test_integrity_rejects_files_that_are_not_in_the_seal(tmp_path):
    bundle = tmp_path / "bundled_tools" / "GLBToolchain"
    bundle.mkdir(parents=True)
    declared = bundle / "declared.txt"
    declared.write_text("sealed", encoding="utf-8")
    integrity = {
        "schema_version": 1,
        "toolchain_version": "test",
        "files": [{"relative_path": "declared.txt", "sha256": toolchain_module._sha256(declared)}],
    }
    (bundle / "toolchain-integrity.v1.json").write_text(json.dumps(integrity), encoding="utf-8")
    (bundle / "unexpected.js").write_text("not sealed", encoding="utf-8")
    manifest = {
        "bundle_state": "sealed",
        "platform": {"os": "win32", "arch": "x64"},
        "toolchain_version": "test",
        "tools": [],
        "runners": [],
        "integrity_file": "toolchain-integrity.v1.json",
    }

    errors = toolchain_module._verify_manifest_integrity(manifest, bundle)

    assert "Nicht versiegelte Datei im Toolchain-Bundle: unexpected.js." in errors


def test_viewer_capabilities_match_glb_asset_loader_at_b4f7674():
    capabilities = load_viewer_capabilities(REPO_ROOT)

    assert capabilities["schema_version"] == 1
    assert capabilities["capability_version"] == "b4f7674"
    assert capabilities["viewer_commit"] == "b4f7674720746253dbeb6986bfb94c7459502ac3"
    assert capabilities["decoders"] == {decoder: True for decoder in REQUIRED_DECODER_IDS}
    assert set(capabilities["decoder_extensions"]) == {
        "KHR_draco_mesh_compression",
        "KHR_texture_basisu",
        "EXT_meshopt_compression",
        "KHR_meshopt_compression",
    }
    assert {
        "KHR_materials_unlit",
        "KHR_mesh_quantization",
        "EXT_mesh_gpu_instancing",
        "EXT_texture_avif",
        "EXT_texture_webp",
    }.issubset(capabilities["supported_extensions"])


def test_manifest_pins_the_local_runtime_transform_codecs_and_validator():
    manifest = load_toolchain_manifest(REPO_ROOT)
    tools = {entry["id"]: entry for entry in manifest["tools"]}
    runners = {entry["id"]: entry for entry in manifest["runners"]}

    assert manifest["bundle_state"] in {"unsealed", "sealed"}
    assert manifest["platform"] == {"os": "win32", "arch": "x64"}
    assert manifest["viewer_contract"]["commit"] == "b4f7674720746253dbeb6986bfb94c7459502ac3"
    assert tools["node"]["version"] == "22.17.0"
    assert tools["node"]["sha256"] == "39d45b5933f339d3ebdebd76474893dab5d7da1038920f65cf5bbcf0f20f3636"
    assert tools["gltf-transform"]["version"] == "4.4.2"
    assert tools["meshoptimizer"]["version"] == "1.0.1"
    assert tools["draco"]["version"] == "1.5.7"
    assert tools["sharp"]["version"] == "0.34.5"
    assert tools["ktx2_basisu"]["version"] == "4.4.2"
    assert tools["ktx2_basisu"]["source_url"].endswith("KTX-Software-4.4.2-Windows-x64.exe")
    assert tools["ktx2_basisu"]["source_sha256"] == "1f323b0fec19794f5e6c0425a61d4b1da396872a10be862d105f4f4b2d2957fe"
    assert tools["gltf-validator"]["version"] == "2.0.0-dev.3.10"
    assert tuple(tools) == REQUIRED_TOOL_IDS
    assert tuple(runners) == REQUIRED_RUNNER_IDS
    assert all(entry["relative_path"].startswith("runners/") for entry in runners.values())


def test_runner_contract_uses_explicit_pipeline_and_decodes_compressed_extensions():
    root = REPO_ROOT / "bundled_tools" / "GLBToolchain" / "runners"
    optimizer = (root / "optimize-glb.mjs").read_text(encoding="utf-8")
    decoder = (root / "decode-glb.mjs").read_text(encoding="utf-8")
    validator = (root / "validate-glb.mjs").read_text(encoding="utf-8")

    assert "[\"dedup\"" in optimizer
    assert "[\"prune\"" in optimizer
    assert "[\"reorder\"" in optimizer
    assert '"meshopt", source, destination' in optimizer and '"--level", "medium"' in optimizer
    assert '"--quantize-position", "16"' in optimizer
    assert '"draco", source, destination' in optimizer and '"--quantize-position", "20"' in optimizer
    assert '"--encode", "uastc", "--uastc-quality", "2"' in optimizer
    assert "E_NOT_STATIC" in optimizer
    assert "ktxDirectory, system32" in optimizer
    assert "ktxDirectory, system32" in decoder
    assert "assertKtxPolicyMatrix" in optimizer
    assert "E_KTX_AMBIGUOUS_COLORSPACE" in optimizer
    assert "E_KTX_AMBIGUOUS_TEXTURE_SOURCE" in optimizer
    assert "documentTextures.length !== images.length" in optimizer
    assert "documentTextures[imageIndex]" in optimizer
    assert "image/webp" in optimizer and "image/avif" in optimizer
    assert "KHR_texture_transform" in optimizer
    assert "input.hasAlpha" in optimizer
    assert "requiresOnePixelKtxExpansion" in optimizer
    assert "[3, 5], [7, 9], [1023, 513]" in optimizer
    assert "width % 4" not in optimizer
    assert 'kernel: "lanczos3"' in optimizer
    assert "gltf-transform optimize" not in optimizer
    assert "[\"copy\"" in decoder
    assert "[\"ktxdecompress\"" in decoder
    assert "EXT_meshopt_compression" in decoder
    assert "validateBytes" in validator


def _read_glb_json(path: Path) -> dict:
    payload = path.read_bytes()
    length = struct.unpack_from("<I", payload, 12)[0]
    return json.loads(payload[20 : 20 + length].decode("utf-8").rstrip(" \t\r\n\x00"))


def _read_glb_image_payload(path: Path, image_index: int) -> bytes:
    payload = path.read_bytes()
    document = _read_glb_json(path)
    offset = 12
    binary = None
    while offset < len(payload):
        chunk_length, chunk_type = struct.unpack_from("<II", payload, offset)
        offset += 8
        if chunk_type == 0x004E4942:
            binary = payload[offset : offset + chunk_length]
            break
        offset += chunk_length
    assert binary is not None
    image = document["images"][image_index]
    view = document["bufferViews"][image["bufferView"]]
    return binary[view.get("byteOffset", 0) : view.get("byteOffset", 0) + view["byteLength"]]


def _texture_image_index(document: dict, material_index: int, texture_slot: str) -> int:
    texture_index = document["materials"][material_index][texture_slot]["index"]
    texture = document["textures"][texture_index]
    if "source" in texture:
        return texture["source"]
    return texture["extensions"]["KHR_texture_basisu"]["source"]


def _write_texture_matrix_glb(path: Path, images_dir: Path) -> dict:
    assets = [
        ("base.png", "image/png"),
        ("orm.jpg", "image/jpeg"),
        ("normal.webp", "image/webp"),
        ("emissive.avif", "image/avif"),
        ("fallback-normal.jpg", "image/jpeg"),
        ("fallback-emissive.png", "image/png"),
    ]
    images = [
        {"mimeType": mime_type, "uri": f"data:{mime_type};base64," + base64.b64encode((images_dir / name).read_bytes()).decode("ascii")}
        for name, mime_type in assets
    ]
    document = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_texture_transform", "EXT_texture_webp", "EXT_texture_avif"],
        "buffers": [{"byteLength": 60}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 24}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [2, 3, 4]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2", "min": [0, 0], "max": [1, 1]},
        ],
        "images": images,
        "textures": [
            {"source": 0, "sampler": 0}, {"source": 1, "sampler": 1},
            {"source": 0, "sampler": 2}, {"source": 1, "sampler": 3},
            {"source": 4, "sampler": 4, "extensions": {"EXT_texture_webp": {"source": 2}}},
            {"source": 5, "sampler": 5, "extensions": {"EXT_texture_avif": {"source": 3}}},
        ],
        "samplers": [
            {"magFilter": 9728, "minFilter": 9728}, {"magFilter": 9729, "minFilter": 9987},
            {"wrapS": 33071}, {"wrapT": 33071}, {"magFilter": 9729, "wrapS": 33648}, {"minFilter": 9985, "wrapT": 33648},
        ],
        "materials": [{
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0, "extensions": {"KHR_texture_transform": {"offset": [0.2, 0.3], "scale": [0.7, 0.8]}}},
                "metallicRoughnessTexture": {"index": 1},
            },
            "normalTexture": {"index": 4},
            "emissiveTexture": {"index": 5},
        }, {"pbrMetallicRoughness": {"baseColorTexture": {"index": 2}}}],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "material": 0},
            {"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "material": 1},
        ]}],
        "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0,
    }
    raw_json = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw_json += b" " * (-len(raw_json) % 4)
    binary = struct.pack("<9f6f", 0, 0, 0, 2, 0, 0, 0, 3, 4, 0, 0, 1, 0, 0, 1)
    chunks = struct.pack("<II", len(raw_json), 0x4E4F534A) + raw_json + struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks)
    return document


def test_sealed_toolchain_runs_generic_texture_matrix_and_compressed_combinations(tmp_path):
    status = get_glb_toolchain_status(REPO_ROOT)
    if not status.toolchain_available:
        pytest.skip("requires the sealed bundled GLB toolchain")
    bundle = REPO_ROOT / "bundled_tools" / "GLBToolchain"
    node = get_bundled_tool_path("node", REPO_ROOT)
    optimizer = get_bundled_runner_path("optimizer", REPO_ROOT)
    decoder = get_bundled_runner_path("decoder", REPO_ROOT)
    validator = get_bundled_runner_path("validator", REPO_ROOT)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    generation_script = """
const sharp = require('sharp');
const path = require('path');
const output = process.argv[1];
const tasks = [
  ['base.png', 1, 1, 4, {r: 255, g: 32, b: 16, alpha: 128}, 'png'],
  ['orm.jpg', 3, 5, 3, {r: 90, g: 120, b: 180}, 'jpeg'],
  ['normal.webp', 7, 9, 4, {r: 128, g: 128, b: 255, alpha: 255}, 'webp'],
  ['emissive.avif', 1023, 513, 3, {r: 40, g: 180, b: 70}, 'avif'],
  ['fallback-normal.jpg', 13, 17, 3, {r: 255, g: 0, b: 0}, 'jpeg'],
  ['fallback-emissive.png', 19, 23, 4, {r: 0, g: 0, b: 255, alpha: 255}, 'png'],
];
Promise.all(tasks.map(([name, width, height, channels, background, format]) =>
  sharp({create: {width, height, channels, background}})[format]().toFile(path.join(output, name))
)).catch((error) => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run([str(node), "-e", generation_script, str(image_dir)], cwd=bundle, check=True, timeout=60)
    source = tmp_path / "texture-matrix.glb"
    document = _write_texture_matrix_glb(source, image_dir)
    ktx2 = tmp_path / "matrix-ktx2.glb"
    subprocess.run([str(node), str(optimizer), "ktx2", str(source), str(ktx2)], cwd=bundle, check=True, timeout=60)
    report = subprocess.run([str(node), str(validator), str(ktx2)], cwd=bundle, capture_output=True, text=True, check=True, timeout=60)
    assert json.loads(report.stdout)["dronautix_policy"]["blocking_error_count"] == 0
    ktx_document = _read_glb_json(ktx2)
    assert "KHR_texture_basisu" in ktx_document["extensionsUsed"]
    assert ktx_document["extensionsRequired"] == ["KHR_texture_basisu"]
    assert "KHR_texture_transform" in ktx_document["extensionsUsed"]
    assert all("uri" not in image for image in ktx_document["images"])
    for slot in ("normalTexture", "emissiveTexture"):
        selected_image = _texture_image_index(ktx_document, 0, slot)
        assert ktx_document["images"][selected_image]["mimeType"] == "image/ktx2"
    assert len(ktx_document["textures"]) >= 4  # Duplicate source images retain distinct samplers.

    plain_decoded = tmp_path / "matrix-ktx2-decoded.glb"
    subprocess.run([str(node), str(decoder), "decode", "KHR_texture_basisu", str(ktx2), str(plain_decoded)], cwd=bundle, check=True, timeout=60)
    decoded_plain_document = _read_glb_json(plain_decoded)
    metadata_script = """
const fs = require('fs'); const sharp = require('sharp');
sharp(fs.readFileSync(process.argv[1])).metadata().then((value) => console.log(JSON.stringify(value)));
"""
    for slot, expected_size in (("normalTexture", (7, 9)), ("emissiveTexture", (1023, 513))):
        selected_image = _texture_image_index(decoded_plain_document, 0, slot)
        image_path = tmp_path / f"decoded-{slot}.png"
        image_path.write_bytes(_read_glb_image_payload(plain_decoded, selected_image))
        metadata = subprocess.run([str(node), "-e", metadata_script, str(image_path)], cwd=bundle, capture_output=True, text=True, check=True, timeout=60)
        dimensions = json.loads(metadata.stdout)
        assert (dimensions["width"], dimensions["height"]) == expected_size

    for geometry_codec, extension in (("meshopt", "EXT_meshopt_compression"), ("draco", "KHR_draco_mesh_compression")):
        combined = tmp_path / f"matrix-{geometry_codec}.glb"
        decoded = tmp_path / f"matrix-{geometry_codec}-decoded.glb"
        subprocess.run([str(node), str(optimizer), geometry_codec, str(ktx2), str(combined)], cwd=bundle, check=True, timeout=60)
        combined_document = _read_glb_json(combined)
        assert extension in combined_document["extensionsUsed"]
        assert "KHR_texture_basisu" in combined_document["extensionsUsed"]
        subprocess.run([str(node), str(decoder), "decode", f"{extension},KHR_texture_basisu", str(combined), str(decoded)], cwd=bundle, check=True, timeout=60)
        decoded_document = _read_glb_json(decoded)
        assert not {"EXT_meshopt_compression", "KHR_draco_mesh_compression", "KHR_texture_basisu"} & set(decoded_document.get("extensionsUsed", []))

    ambiguous = tmp_path / "shared-image-ambiguous.glb"
    # Different raw texture definitions may share the same source image. The
    # colour role is still ambiguous, even though their samplers differ.
    document["materials"][0]["normalTexture"] = {"index": 2}
    raw_json = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw_json += b" " * (-len(raw_json) % 4)
    binary = struct.pack("<9f6f", 0, 0, 0, 2, 0, 0, 0, 3, 4, 0, 0, 1, 0, 0, 1)
    ambiguous.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(raw_json) + len(binary) + 16) + struct.pack("<II", len(raw_json), 0x4E4F534A) + raw_json + struct.pack("<II", len(binary), 0x004E4942) + binary)
    rejected = subprocess.run([str(node), str(optimizer), "ktx2", str(ambiguous), str(tmp_path / "shared-image-ambiguous-ktx2.glb")], cwd=bundle, capture_output=True, text=True, timeout=60)
    assert rejected.returncode != 0 and "E_KTX_AMBIGUOUS_COLORSPACE" in rejected.stderr

    existing = subprocess.run([str(node), str(optimizer), "ktx2", str(ktx2), str(tmp_path / "again.glb")], cwd=bundle, capture_output=True, text=True, timeout=60)
    assert existing.returncode != 0 and "E_KTX_ALREADY_COMPRESSED" in existing.stderr


def test_build_and_installer_include_and_gate_the_entire_toolchain_contract():
    build_script = (REPO_ROOT / "build_exe.py").read_text(encoding="utf-8")
    candidate_script = (REPO_ROOT / "build_v2_final_candidate.py").read_text(encoding="utf-8")
    spec = (REPO_ROOT / "Dronautix_Pointcloud_Uploader.spec").read_text(encoding="utf-8")
    installer = (REPO_ROOT / "Dronautix_Pointcloud_Uploader.iss").read_text(encoding="utf-8")

    assert 'os.path.join("bundled_tools", "GLBToolchain")' in build_script
    assert '"toolchain-integrity.v1.json"' in build_script
    assert '"toolchain-integrity.v1.json"' in candidate_script
    assert "validate_glb_toolchain_for_packaging" in build_script
    assert "validate_glb_toolchain_for_packaging" in candidate_script
    assert "('bundled_tools/GLBToolchain', 'bundled_tools/GLBToolchain')" in spec
    assert "including bundled_tools\\GLBToolchain" in installer
    packaging_issues = validate_glb_toolchain_for_packaging(REPO_ROOT)
    if load_toolchain_manifest(REPO_ROOT)["bundle_state"] == "sealed":
        assert packaging_issues == ()
    else:
        assert packaging_issues


def test_sealed_bundle_inventory_is_complete_for_production_packaging():
    bundle = REPO_ROOT / "bundled_tools" / "GLBToolchain"
    integrity = json.loads((bundle / "toolchain-integrity.v1.json").read_text(encoding="utf-8"))
    actual_files = tuple(path for path in bundle.rglob("*") if path.is_file())

    # The integrity manifest cannot hash itself; all other bundled files are
    # declared and its presence is enforced by both production build gates.
    assert len(actual_files) == 5260
    assert len(integrity["files"]) + 1 == len(actual_files)
