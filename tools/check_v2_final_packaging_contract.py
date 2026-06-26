"""Validate the final-V2 packaging contract without touching the release channel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_version import APP_EXE_NAME, APP_ID, APP_NAME, APP_PUBLISHER, APP_VERSION
from dronautix_uploader.core.constants import (
    UPDATE_MANIFEST_BRANCH,
    UPDATE_REPO_NAME,
    UPDATE_REPO_OWNER,
)
from dronautix_uploader.core.update_service import (
    validate_installer_sha256,
    validate_update_download_info,
)


V2_ENTRYPOINT = "Dronautix_Pointcloud_Uploader_v2_final.py"
PREVIEW_ENTRYPOINT = "Dronautix_Pointcloud_Uploader_v2.py"
PRODUCTION_PYINSTALLER_NAME = "Dronautix_Pointcloud_Uploader"
CANDIDATE_DIST_DIR = "dist_v2_final_candidate"
CANDIDATE_OUTPUT_DIR = "Output_v2_final_candidate"
CANDIDATE_MANIFEST_PATH = Path("artifacts/v2-final-candidate-release.json")
PENDING_SHA256 = "PENDING_FINAL_INSTALLER_SHA256"


def build_final_packaging_contract(installer_sha256: str = "") -> dict[str, Any]:
    installer_name = f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe"
    release_tag = f"v{APP_VERSION}"
    installer_url = (
        f"https://github.com/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}/"
        f"releases/download/{release_tag}/{installer_name}"
    )
    return {
        "schema_version": 1,
        "candidate_only": True,
        "entrypoint": V2_ENTRYPOINT,
        "pyinstaller_name": PRODUCTION_PYINSTALLER_NAME,
        "runtime_mode": {
            "mode": "final",
            "detection": "explicit_final_entrypoint",
            "preview_entrypoint": PREVIEW_ENTRYPOINT,
            "preview_entrypoint_default": "preview",
        },
        "app_identity": {
            "app_name": APP_NAME,
            "app_publisher": APP_PUBLISHER,
            "app_exe_name": APP_EXE_NAME,
            "app_id": APP_ID,
            "version": APP_VERSION,
        },
        "isolated_paths": {
            "dist_dir": CANDIDATE_DIST_DIR,
            "output_dir": CANDIDATE_OUTPUT_DIR,
            "manifest_path": CANDIDATE_MANIFEST_PATH.as_posix(),
        },
        "release_manifest_candidate": {
            "version": APP_VERSION,
            "installer_name": installer_name,
            "repo_owner": UPDATE_REPO_OWNER,
            "repo_name": UPDATE_REPO_NAME,
            "manifest_branch": UPDATE_MANIFEST_BRANCH,
            "release_tag": release_tag,
            "installer_url": installer_url,
            "installer_sha256": installer_sha256.strip().lower() if installer_sha256 else PENDING_SHA256,
        },
        "production_release_files_not_written": (
            "latest-release.json",
            "installer_version.iss",
            "version_info.txt",
            "Output/",
            "Dronautix_Pointcloud_Uploader.spec",
        ),
    }


def validate_final_packaging_contract(
    contract: dict[str, Any],
    *,
    require_valid_sha: bool = False,
) -> tuple[str, ...]:
    issues: list[str] = []
    if contract.get("candidate_only") is not True:
        issues.append("Contract muss candidate_only=true setzen.")
    if contract.get("entrypoint") != V2_ENTRYPOINT:
        issues.append("Final-Kandidat muss den V2-Entrypoint verwenden.")
    if contract.get("pyinstaller_name") != PRODUCTION_PYINSTALLER_NAME:
        issues.append("Final-Kandidat muss den produktiven PyInstaller-Namen verwenden.")
    runtime_mode = contract.get("runtime_mode", {})
    if runtime_mode.get("mode") != "final":
        issues.append("Final-Kandidat muss runtime_mode.mode=final setzen.")
    if runtime_mode.get("detection") != "explicit_final_entrypoint":
        issues.append("Final-Kandidat muss den Runtime-Modus ueber den expliziten Final-Entrypoint setzen.")
    if runtime_mode.get("preview_entrypoint") != PREVIEW_ENTRYPOINT:
        issues.append("Final-Kandidat Runtime-Modus muss den getrennten Preview-Entrypoint dokumentieren.")
    if runtime_mode.get("preview_entrypoint_default") != "preview":
        issues.append("V2-Entrypoint muss als Source/Preview-Start weiterhin Preview bleiben.")

    identity = contract.get("app_identity", {})
    expected_identity = {
        "app_name": APP_NAME,
        "app_publisher": APP_PUBLISHER,
        "app_exe_name": APP_EXE_NAME,
        "app_id": APP_ID,
        "version": APP_VERSION,
    }
    for key, expected in expected_identity.items():
        if identity.get(key) != expected:
            issues.append(f"Produktidentitaet weicht ab: {key}.")

    isolated_paths = contract.get("isolated_paths", {})
    if isolated_paths.get("dist_dir") == "dist" or isolated_paths.get("output_dir") == "Output":
        issues.append("Final-Kandidat darf nicht in produktive Build-Ordner schreiben.")
    if isolated_paths.get("manifest_path") == "latest-release.json":
        issues.append("Final-Kandidat darf nicht latest-release.json schreiben.")

    release_manifest = contract.get("release_manifest_candidate", {})
    installer_name = str(release_manifest.get("installer_name", "") or "")
    installer_url = str(release_manifest.get("installer_url", "") or "")
    ok, message = validate_update_download_info(release_manifest, installer_url, installer_name)
    if not ok:
        issues.append(message)

    installer_sha256 = str(release_manifest.get("installer_sha256", "") or "")
    if not installer_sha256:
        issues.append("installer_sha256 Feld fehlt.")
    elif require_valid_sha:
        sha_ok, sha_message = validate_installer_sha256(installer_sha256)
        if not sha_ok:
            issues.append(sha_message)
    return tuple(issues)


def load_candidate_manifest(path: str | Path = CANDIDATE_MANIFEST_PATH) -> dict[str, Any]:
    manifest_path = Path(path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Candidate-Manifest muss ein JSON-Objekt sein.")
    return data


def validate_candidate_manifest_file(
    path: str | Path,
    expected_contract: dict[str, Any],
    *,
    require_valid_sha: bool = False,
) -> tuple[str, ...]:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return (f"Candidate-Manifest fehlt: {manifest_path}",)
    try:
        actual_contract = load_candidate_manifest(manifest_path)
    except json.JSONDecodeError as exc:
        return (f"Candidate-Manifest ist kein gueltiges JSON: {manifest_path} ({exc})",)
    except ValueError as exc:
        return (str(exc),)

    issues = list(validate_final_packaging_contract(actual_contract, require_valid_sha=require_valid_sha))
    actual_without_sha = _contract_without_installer_sha(actual_contract)
    expected_without_sha = _contract_without_installer_sha(expected_contract)
    if actual_without_sha != expected_without_sha:
        issues.append("Candidate-Manifest driftet gegen den aktuellen Final-V2-Vertrag.")

    if require_valid_sha:
        actual_sha = _contract_installer_sha(actual_contract)
        expected_sha = _contract_installer_sha(expected_contract)
        if actual_sha != expected_sha:
            issues.append("Candidate-Manifest installer_sha256 passt nicht zur verifizierten SHA.")
    return tuple(issues)


def _contract_installer_sha(contract: dict[str, Any]) -> str:
    release_manifest = contract.get("release_manifest_candidate", {})
    if not isinstance(release_manifest, dict):
        return ""
    return str(release_manifest.get("installer_sha256", "") or "").strip().lower()


def _contract_without_installer_sha(contract: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(contract, ensure_ascii=False))
    release_manifest = normalized.get("release_manifest_candidate")
    if isinstance(release_manifest, dict):
        release_manifest["installer_sha256"] = "<installer_sha256>"
    return normalized


def write_candidate_manifest(contract: dict[str, Any], output_path: str | Path = CANDIDATE_MANIFEST_PATH) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the isolated final-V2 packaging candidate contract.")
    parser.add_argument("--installer-sha256", default="", help="Optional real installer SHA-256 for strict checks.")
    parser.add_argument("--require-valid-sha", action="store_true", help="Require installer_sha256 to be a real SHA-256.")
    parser.add_argument("--write", action="store_true", help="Write artifacts/v2-final-candidate-release.json.")
    parser.add_argument("--output", default=str(CANDIDATE_MANIFEST_PATH), help="Candidate manifest output path.")
    args = parser.parse_args(argv)

    contract = build_final_packaging_contract(args.installer_sha256)
    issues = validate_final_packaging_contract(contract, require_valid_sha=args.require_valid_sha)
    if args.write:
        written = write_candidate_manifest(contract, args.output)
        print(f"Candidate manifest written: {written}")
    if issues:
        print("Final-V2 packaging candidate: BLOCKED")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Final-V2 packaging candidate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
