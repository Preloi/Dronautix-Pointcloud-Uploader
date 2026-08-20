"""Seal an already acquired GLB toolchain; this utility never downloads tools."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.glb_toolchain import (
    REQUIRED_RUNNER_IDS,
    REQUIRED_TOOL_IDS,
    TOOLCHAIN_INTEGRITY_FILE,
    TOOLCHAIN_MANIFEST_FILE,
    get_bundled_glb_toolchain_dir,
    get_glb_toolchain_status,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(toolchain_dir: Path) -> dict[str, Any]:
    path = toolchain_dir / TOOLCHAIN_MANIFEST_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Toolchain-Manifest hat kein unterstütztes Schema.")
    return data


def entries_by_id(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    return {
        str(entry.get("id")): entry
        for entry in value
        if isinstance(entry, dict) and str(entry.get("id", "")).strip()
    }


def relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Manifest-Einstiegspfad fehlt.")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Manifest-Einstiegspfad ist unsicher: {value!r}")
    return path


def seal(toolchain_dir: Path) -> None:
    manifest = load_manifest(toolchain_dir)
    entries = entries_by_id(manifest.get("tools"))
    runners = entries_by_id(manifest.get("runners"))
    for identifier in REQUIRED_TOOL_IDS:
        if identifier not in entries:
            raise ValueError(f"Tool {identifier} fehlt im Manifest.")
    for identifier in REQUIRED_RUNNER_IDS:
        if identifier not in runners:
            raise ValueError(f"Runner {identifier} fehlt im Manifest.")

    for entry in (*entries.values(), *runners.values()):
        path = toolchain_dir / relative_path(entry.get("relative_path"))
        if not path.is_file():
            raise ValueError(f"Versiegelte Werkzeugdatei fehlt: {path}")
        entry["sha256"] = sha256(path)

    manifest["bundle_state"] = "sealed"
    manifest_path = toolchain_dir / TOOLCHAIN_MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    integrity_path = toolchain_dir / TOOLCHAIN_INTEGRITY_FILE
    files = []
    for path in sorted(toolchain_dir.rglob("*")):
        if not path.is_file() or path == integrity_path:
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink ist im Toolchain-Bundle nicht erlaubt: {path}")
        files.append({"relative_path": path.relative_to(toolchain_dir).as_posix(), "sha256": sha256(path)})
    integrity = {
        "schema_version": 1,
        "toolchain_version": manifest.get("toolchain_version"),
        "files": files,
    }
    integrity_path.write_text(json.dumps(integrity, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def verify(toolchain_dir: Path) -> int:
    status = get_glb_toolchain_status(toolchain_dir.parents[1])
    if status.toolchain_available and status.viewer_supports_compressed_output:
        print(f"GLB toolchain: OK ({status.toolchain_version})")
        return 0
    print("GLB toolchain: BLOCKED")
    print(status.fallback_reason)
    for error in status.integrity_errors:
        print(f"- {error}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seal", action="store_true", help="Versiegelt vorhandene lokale Artefakte.")
    parser.add_argument("--verify", action="store_true", help="Prüft Hashes und lokale Runner.")
    args = parser.parse_args(argv)
    if args.seal == args.verify:
        parser.error("Genau eine Aktion --seal oder --verify angeben.")
    toolchain_dir = get_bundled_glb_toolchain_dir(REPO_ROOT)
    if args.seal:
        seal(toolchain_dir)
    return verify(toolchain_dir)


if __name__ == "__main__":
    raise SystemExit(main())
