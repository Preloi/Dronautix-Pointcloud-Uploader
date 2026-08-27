"""Fail-closed contract for the bundled, offline GLB toolchain.

The uploader must never fall back to a Node/npm installation found on the
machine. Optimisation is enabled only after a release has sealed every bundled
file with a SHA-256 entry and the bundled Node runtime completed its local
runner self-tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from threading import RLock
from typing import Any, Mapping

from .converter_bundle import bundled_resource_root


BUNDLED_GLB_TOOLCHAIN_DIR = ("bundled_tools", "GLBToolchain")
VIEWER_CAPABILITIES_FILE = "viewer-capabilities.v1.json"
TOOLCHAIN_MANIFEST_FILE = "toolchain-manifest.v1.json"
TOOLCHAIN_INTEGRITY_FILE = "toolchain-integrity.v1.json"
TOOLCHAIN_SCHEMA_VERSION = 1
UNCOMPRESSED_FALLBACK_MODE = "uncompressed_fallback"
COMPRESSED_OPTIMIZATION_MODE = "compressed_optimization"
REQUIRED_TOOL_IDS = (
    "node",
    "gltf-transform",
    "meshoptimizer",
    "draco",
    "sharp",
    "ktx2_basisu",
    "gltf-validator",
)
REQUIRED_RUNNER_IDS = ("optimizer", "validator", "decoder")
REQUIRED_DECODER_IDS = ("draco", "meshopt", "ktx2_basisu", "webp")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOOLCHAIN_STATUS_LOCK = RLock()


def _hidden_process_options(creationflags: int = 0) -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": creationflags | getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


@dataclass(frozen=True)
class GLBToolchainStatus:
    """The safe output decision before an optimisation starts."""

    mode: str
    toolchain_available: bool
    viewer_supports_compressed_output: bool
    missing_tools: tuple[str, ...]
    fallback_reason: str
    viewer_capabilities: dict[str, Any]
    toolchain_version: str = ""
    toolchain_dir: str = ""
    integrity_errors: tuple[str, ...] = ()

    @property
    def use_uncompressed_fallback(self) -> bool:
        return self.mode == UNCOMPRESSED_FALLBACK_MODE


def get_bundled_glb_toolchain_dir(resource_root: str | Path | None = None) -> Path:
    root = Path(resource_root) if resource_root is not None else bundled_resource_root()
    return root.joinpath(*BUNDLED_GLB_TOOLCHAIN_DIR)


def get_viewer_capabilities_path(resource_root: str | Path | None = None) -> Path:
    return get_bundled_glb_toolchain_dir(resource_root) / VIEWER_CAPABILITIES_FILE


def get_toolchain_manifest_path(resource_root: str | Path | None = None) -> Path:
    return get_bundled_glb_toolchain_dir(resource_root) / TOOLCHAIN_MANIFEST_FILE


def get_toolchain_integrity_path(resource_root: str | Path | None = None) -> Path:
    return get_bundled_glb_toolchain_dir(resource_root) / TOOLCHAIN_INTEGRITY_FILE


def load_viewer_capabilities(resource_root: str | Path | None = None) -> dict[str, Any]:
    data, _error = _load_json(get_viewer_capabilities_path(resource_root))
    return data


def load_toolchain_manifest(resource_root: str | Path | None = None) -> dict[str, Any]:
    data, _error = _load_json(get_toolchain_manifest_path(resource_root))
    return data


def get_glb_toolchain_status(resource_root: str | Path | None = None) -> GLBToolchainStatus:
    """Verify the local bundle and return a strictly safe mode.

    Only files below the resource root are checked. The code never reads PATH,
    ``npm``, or user configuration to locate an optimisation tool. The complete
    SHA-256 scan and runner self-tests are deliberately done once per resolved
    resource root and app process; a 5260-file bundle must not be revalidated
    for each optimizer or decoder call.
    """

    with _TOOLCHAIN_STATUS_LOCK:
        return _get_glb_toolchain_status_cached(_resource_root_cache_key(resource_root))


@lru_cache(maxsize=None)
def _get_glb_toolchain_status_cached(resource_root_key: str) -> GLBToolchainStatus:
    return _get_glb_toolchain_status_uncached(Path(resource_root_key))


def _get_glb_toolchain_status_uncached(resource_root: Path) -> GLBToolchainStatus:
    capabilities_path = get_viewer_capabilities_path(resource_root)
    capabilities, capabilities_error = _load_json(capabilities_path)
    manifest_path = get_toolchain_manifest_path(resource_root)
    manifest, manifest_error = _load_json(manifest_path)
    toolchain_dir = get_bundled_glb_toolchain_dir(resource_root)
    decoder_support = _viewer_supports_compressed_output(capabilities)
    version = str(manifest.get("toolchain_version", "") or "")

    if capabilities_error:
        return _fallback_status(
            (),
            f"Viewer-Capability-Datei fehlt oder ist ungueltig: {capabilities_path}",
            capabilities,
            decoder_support,
            toolchain_version=version,
            toolchain_dir=toolchain_dir,
        )
    if manifest_error:
        return _fallback_status(
            REQUIRED_TOOL_IDS,
            f"Lokale GLB-Toolchain fehlt oder ist ungueltig: {manifest_path}",
            capabilities,
            decoder_support,
            toolchain_version=version,
            toolchain_dir=toolchain_dir,
        )

    missing_tools = _missing_tool_ids(manifest, toolchain_dir)
    integrity_errors = _verify_manifest_integrity(manifest, toolchain_dir)
    if missing_tools or integrity_errors:
        details = "; ".join(integrity_errors[:3])
        suffix = f" ({details})" if details else ""
        return _fallback_status(
            missing_tools,
            "Lokale GLB-Toolchain ist nicht vollstaendig versiegelt; unveraendertes, selbststaendiges GLB verwenden."
            + suffix,
            capabilities,
            decoder_support,
            toolchain_version=version,
            toolchain_dir=toolchain_dir,
            integrity_errors=integrity_errors,
        )
    if not decoder_support:
        return _fallback_status(
            (),
            "Produktiver Viewer aktiviert die benoetigten GLB-Decoder nicht; unkomprimiertes GLB verwenden.",
            capabilities,
            False,
            toolchain_available=True,
            toolchain_version=version,
            toolchain_dir=toolchain_dir,
        )

    runtime_errors = _run_local_self_tests(manifest, toolchain_dir)
    if runtime_errors:
        return _fallback_status(
            (),
            "Lokale GLB-Toolchain-Selbstpruefung ist fehlgeschlagen; unveraendertes, selbststaendiges GLB verwenden."
            + f" ({'; '.join(runtime_errors[:3])})",
            capabilities,
            decoder_support,
            toolchain_version=version,
            toolchain_dir=toolchain_dir,
            integrity_errors=runtime_errors,
        )
    return GLBToolchainStatus(
        mode=COMPRESSED_OPTIMIZATION_MODE,
        toolchain_available=True,
        viewer_supports_compressed_output=True,
        missing_tools=(),
        fallback_reason="",
        viewer_capabilities=capabilities,
        toolchain_version=version,
        toolchain_dir=str(toolchain_dir),
    )


def _resource_root_cache_key(resource_root: str | Path | None) -> str:
    root = Path(resource_root) if resource_root is not None else bundled_resource_root()
    return os.path.normcase(str(root.resolve()))


def _reset_glb_toolchain_status_cache_for_tests() -> None:
    """Test-only cache reset so temporary resource roots cannot leak state."""

    with _TOOLCHAIN_STATUS_LOCK:
        _get_glb_toolchain_status_cached.cache_clear()


def validate_glb_toolchain_for_packaging(resource_root: str | Path | None = None) -> tuple[str, ...]:
    """Return release-blocking errors; used by the build/installer gates."""

    status = get_glb_toolchain_status(resource_root)
    if status.toolchain_available and status.viewer_supports_compressed_output:
        return ()
    errors = list(status.integrity_errors)
    if status.missing_tools:
        errors.append("Fehlende GLB-Tools: " + ", ".join(status.missing_tools))
    if not errors:
        errors.append(status.fallback_reason or "GLB-Toolchain ist nicht produktionsbereit.")
    return tuple(errors)


def get_bundled_tool_path(tool_id: str, resource_root: str | Path | None = None) -> Path:
    """Resolve one declared local tool without consulting global PATH."""

    manifest = load_toolchain_manifest(resource_root)
    toolchain_dir = get_bundled_glb_toolchain_dir(resource_root)
    entry = _entries_by_id(manifest.get("tools")).get(tool_id, {})
    relative_path = _safe_relative_path(entry.get("relative_path"))
    return toolchain_dir / relative_path if relative_path is not None else toolchain_dir / "__missing__"


def get_bundled_runner_path(runner_id: str, resource_root: str | Path | None = None) -> Path:
    manifest = load_toolchain_manifest(resource_root)
    toolchain_dir = get_bundled_glb_toolchain_dir(resource_root)
    entry = _entries_by_id(manifest.get("runners")).get(runner_id, {})
    relative_path = _safe_relative_path(entry.get("relative_path"))
    return toolchain_dir / relative_path if relative_path is not None else toolchain_dir / "__missing__"


def get_bundled_toolchain_environment(resource_root: str | Path | None = None) -> dict[str, str]:
    """Return an environment whose executable search path contains only KTX."""

    return _isolated_toolchain_environment(get_bundled_glb_toolchain_dir(resource_root))


def _fallback_status(
    missing_tools: tuple[str, ...],
    reason: str,
    capabilities: dict[str, Any],
    decoder_support: bool,
    *,
    toolchain_available: bool = False,
    toolchain_version: str = "",
    toolchain_dir: Path | str = "",
    integrity_errors: tuple[str, ...] = (),
) -> GLBToolchainStatus:
    return GLBToolchainStatus(
        mode=UNCOMPRESSED_FALLBACK_MODE,
        toolchain_available=toolchain_available,
        viewer_supports_compressed_output=decoder_support,
        missing_tools=missing_tools,
        fallback_reason=reason,
        viewer_capabilities=capabilities,
        toolchain_version=toolchain_version,
        toolchain_dir=str(toolchain_dir),
        integrity_errors=integrity_errors,
    )


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, str(exc)
    if not isinstance(data, dict) or data.get("schema_version") != TOOLCHAIN_SCHEMA_VERSION:
        return {}, f"schema_version {TOOLCHAIN_SCHEMA_VERSION} erwartet"
    return data, ""


def _entries_by_id(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for entry in value:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("id", "") or "").strip()
        if identifier and identifier not in entries:
            entries[identifier] = entry
    return entries


def _missing_tool_ids(manifest: Mapping[str, Any], toolchain_dir: Path) -> tuple[str, ...]:
    entries = _entries_by_id(manifest.get("tools"))
    missing: list[str] = []
    for tool_id in REQUIRED_TOOL_IDS:
        entry = entries.get(tool_id)
        relative_path = _safe_relative_path(entry.get("relative_path") if entry else None)
        if relative_path is None or not _is_file_within(toolchain_dir, relative_path):
            missing.append(tool_id)
    return tuple(missing)


def _verify_manifest_integrity(manifest: Mapping[str, Any], toolchain_dir: Path) -> tuple[str, ...]:
    if manifest.get("bundle_state") != "sealed":
        return ("Bundle ist nicht versiegelt.",)
    if manifest.get("platform") != {"os": "win32", "arch": "x64"}:
        return ("Bundle ist nicht fuer win32-x64 festgelegt.",)

    errors: list[str] = []
    entries = _entries_by_id(manifest.get("tools"))
    entries.update(_entries_by_id(manifest.get("runners")))
    for identifier in (*REQUIRED_TOOL_IDS, *REQUIRED_RUNNER_IDS):
        entry = entries.get(identifier)
        if entry is None:
            errors.append(f"Eintrag {identifier} fehlt.")
            continue
        _verify_entry(entry, identifier, toolchain_dir, errors)

    integrity_name = _safe_relative_path(manifest.get("integrity_file", TOOLCHAIN_INTEGRITY_FILE))
    if integrity_name is None:
        errors.append("Integrity-Dateipfad ist unsicher.")
        return tuple(errors)
    integrity_path = toolchain_dir / integrity_name
    integrity, integrity_error = _load_json(integrity_path)
    if integrity_error:
        errors.append("Integrity-Datei fehlt oder ist ungueltig.")
        return tuple(errors)
    if integrity.get("toolchain_version") != manifest.get("toolchain_version"):
        errors.append("Integrity-Datei hat eine andere Toolchain-Version.")
    files = integrity.get("files")
    if not isinstance(files, list) or not files:
        errors.append("Integrity-Datei enthält keine Dateien.")
        return tuple(errors)
    declared_paths: set[str] = set()
    for file_entry in files:
        if not isinstance(file_entry, dict):
            errors.append("Integrity-Datei enthält einen ungueltigen Eintrag.")
            continue
        relative_path = _safe_relative_path(file_entry.get("relative_path"))
        sha256 = str(file_entry.get("sha256", "") or "").casefold()
        if relative_path is None or not _SHA256_RE.fullmatch(sha256):
            errors.append("Integrity-Datei enthält Pfad oder SHA-256 ungueltig.")
            continue
        key = relative_path.as_posix()
        if key in declared_paths:
            errors.append(f"Integrity-Datei enthält {key} mehrfach.")
            continue
        declared_paths.add(key)
        candidate = toolchain_dir / relative_path
        if not _is_file_within(toolchain_dir, relative_path):
            errors.append(f"Versiegelte Datei fehlt: {key}.")
        elif _sha256(candidate) != sha256:
            errors.append(f"SHA-256 stimmt nicht: {key}.")

    for entry in entries.values():
        relative_path = _safe_relative_path(entry.get("relative_path"))
        if relative_path is not None and relative_path.as_posix() not in declared_paths:
            errors.append(f"Tooldatei fehlt in Integrity-Datei: {relative_path.as_posix()}.")
    actual_paths: set[str] = set()
    for candidate in toolchain_dir.rglob("*"):
        if candidate.is_symlink():
            errors.append(f"Symlink im Toolchain-Bundle ist verboten: {candidate.relative_to(toolchain_dir).as_posix()}.")
        elif candidate.is_file():
            actual_paths.add(candidate.relative_to(toolchain_dir).as_posix())
    expected_paths = declared_paths | {integrity_name.as_posix()}
    for unexpected in sorted(actual_paths - expected_paths):
        errors.append(f"Nicht versiegelte Datei im Toolchain-Bundle: {unexpected}.")
    return tuple(errors)


def _verify_entry(entry: Mapping[str, Any], identifier: str, toolchain_dir: Path, errors: list[str]) -> None:
    if not isinstance(entry.get("version"), str) or not entry["version"].strip():
        errors.append(f"{identifier} hat keine feste Version.")
    relative_path = _safe_relative_path(entry.get("relative_path"))
    expected_sha256 = str(entry.get("sha256", "") or "").casefold()
    if relative_path is None:
        errors.append(f"{identifier} hat einen unsicheren Pfad.")
        return
    if not _SHA256_RE.fullmatch(expected_sha256):
        errors.append(f"{identifier} hat keine gueltige SHA-256.")
        return
    if not _is_file_within(toolchain_dir, relative_path):
        errors.append(f"{identifier} fehlt.")
        return
    if _sha256(toolchain_dir / relative_path) != expected_sha256:
        errors.append(f"{identifier} SHA-256 stimmt nicht.")


def _run_local_self_tests(manifest: Mapping[str, Any], toolchain_dir: Path) -> tuple[str, ...]:
    """Run only verified files; no shell and no system Node/npm lookup."""

    node = _entry_path(manifest, "tools", "node", toolchain_dir)
    expected_node_version = _entry_value(manifest, "tools", "node", "version")
    try:
        node_result = subprocess.run(
            [str(node), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            cwd=toolchain_dir,
            env=_isolated_toolchain_environment(toolchain_dir),
            **_hidden_process_options(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (f"Gebündelte Node-Runtime startet nicht: {exc}",)
    if node_result.returncode != 0 or node_result.stdout.strip() != f"v{expected_node_version}":
        return ("Gebündelte Node-Runtime meldet nicht die versiegelte Version.",)

    errors: list[str] = []
    for runner_id in REQUIRED_RUNNER_IDS:
        runner = _entry_path(manifest, "runners", runner_id, toolchain_dir)
        try:
            result = subprocess.run(
                [str(node), str(runner), "--self-test"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                cwd=toolchain_dir,
                env=_isolated_toolchain_environment(toolchain_dir),
                **_hidden_process_options(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{runner_id}-Runner startet nicht: {exc}")
            continue
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().replace("\r", " ").replace("\n", " ")
            errors.append(f"{runner_id}-Runner-Selbsttest fehlgeschlagen: {detail[:180]}")
    return tuple(errors)


def _entry_path(manifest: Mapping[str, Any], section: str, identifier: str, toolchain_dir: Path) -> Path:
    entry = _entries_by_id(manifest.get(section)).get(identifier, {})
    relative_path = _safe_relative_path(entry.get("relative_path"))
    return toolchain_dir / relative_path if relative_path is not None else toolchain_dir / "__missing__"


def _entry_value(manifest: Mapping[str, Any], section: str, identifier: str, key: str) -> str:
    return str(_entries_by_id(manifest.get(section)).get(identifier, {}).get(key, "") or "")


def _isolated_toolchain_environment(toolchain_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    ktx_dir = toolchain_dir / "ktx" / "bin"
    environment["PATH"] = str(ktx_dir)
    environment["Path"] = str(ktx_dir)
    return environment


def _safe_relative_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _is_file_within(root: Path, relative_path: Path) -> bool:
    candidate = root / relative_path
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return candidate.is_file()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _viewer_supports_compressed_output(capabilities: Mapping[str, Any]) -> bool:
    decoders = capabilities.get("decoders") if isinstance(capabilities, Mapping) else None
    return isinstance(decoders, Mapping) and all(decoders.get(name) is True for name in REQUIRED_DECODER_IDS)


__all__ = [
    "BUNDLED_GLB_TOOLCHAIN_DIR",
    "COMPRESSED_OPTIMIZATION_MODE",
    "GLBToolchainStatus",
    "REQUIRED_DECODER_IDS",
    "REQUIRED_RUNNER_IDS",
    "REQUIRED_TOOL_IDS",
    "TOOLCHAIN_INTEGRITY_FILE",
    "TOOLCHAIN_MANIFEST_FILE",
    "UNCOMPRESSED_FALLBACK_MODE",
    "VIEWER_CAPABILITIES_FILE",
    "get_bundled_glb_toolchain_dir",
    "get_bundled_runner_path",
    "get_bundled_toolchain_environment",
    "get_bundled_tool_path",
    "get_glb_toolchain_status",
    "get_toolchain_integrity_path",
    "get_toolchain_manifest_path",
    "get_viewer_capabilities_path",
    "load_toolchain_manifest",
    "load_viewer_capabilities",
    "validate_glb_toolchain_for_packaging",
]
