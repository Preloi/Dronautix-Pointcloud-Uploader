"""UI-free acceptance evidence helpers for the V2 cutover gate."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app_version import APP_VERSION

from .update_service import is_remote_version_newer, validate_installer_sha256


ACCEPTANCE_SCHEMA_VERSION = 1
REAL_S3_ACCEPTANCE = "real_s3_acceptance"
GITHUB_ASSET_SHA = "github_asset_sha"
LEGACY_INSTALLED_UPDATE = "legacy_installed_update"
LEGACY_UPDATE = LEGACY_INSTALLED_UPDATE
REQUIRED_ACCEPTANCE_GATES = (REAL_S3_ACCEPTANCE, GITHUB_ASSET_SHA, LEGACY_UPDATE)
EXPECTED_REPO = "Preloi/Dronautix-Pointcloud-Uploader"
DEFAULT_CANDIDATE_MANIFEST_PATH = "artifacts/v2-final-candidate-release.json"
DEFAULT_S3_ACCEPTANCE_SCENARIOS = (
    "single_potree_upload",
    "single_copc_upload",
    "multi_mix_upload",
    "vertical_crs_upload",
    "existing_potree_folder_upload",
    "duplicate_project",
    "delete_project",
    "rename_project",
    "single_replace",
    "multi_replace",
    "disabled_link_state",
)


@dataclass(frozen=True)
class AcceptanceGateResult:
    gate_id: str
    label: str
    complete: bool
    detail: str


def load_acceptance_evidence(path: str | Path) -> dict[str, Any]:
    evidence_path = Path(path)
    if not evidence_path.is_file():
        return {}
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Akzeptanz-Evidenz ist kein gueltiges JSON: {evidence_path}") from exc
    return data if isinstance(data, dict) else {}


def evaluate_acceptance_evidence(
    evidence: dict[str, Any],
    *,
    candidate_contract: dict[str, Any] | None = None,
    required_s3_scenarios: Iterable[str] = (),
) -> tuple[AcceptanceGateResult, ...]:
    """Evaluate manual acceptance evidence required before final cutover."""

    if int(evidence.get("schema_version", 0) or 0) != ACCEPTANCE_SCHEMA_VERSION:
        return tuple(
            AcceptanceGateResult(gate_id, _gate_label(gate_id), False, "schema_version=1 fehlt.")
            for gate_id in REQUIRED_ACCEPTANCE_GATES
        )

    gates = evidence.get("gates", {})
    if not isinstance(gates, dict):
        gates = {}
    required_scenarios = _normalize_required_scenarios(required_s3_scenarios)

    return (
        _evaluate_real_s3_acceptance(gates, required_scenarios=required_scenarios),
        _evaluate_github_asset_sha(evidence, gates, candidate_contract),
        _evaluate_legacy_update(gates),
    )


def build_acceptance_evidence_template(
    *,
    required_s3_scenarios: Iterable[str] = DEFAULT_S3_ACCEPTANCE_SCENARIOS,
) -> dict[str, Any]:
    """Return the expected local evidence schema without marking gates complete."""

    scenario_list = list(_normalize_required_scenarios(required_s3_scenarios))
    return {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "candidate_version": APP_VERSION,
        "candidate_manifest_path": DEFAULT_CANDIDATE_MANIFEST_PATH,
        "candidate_installer_name": f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe",
        "candidate_installer_sha256": "",
        "production_release_files_not_written": [
            "latest-release.json",
            "installer_version.iss",
            "version_info.txt",
            "Output/",
            "Dronautix_Pointcloud_Uploader.spec",
        ],
        "gates": {
            REAL_S3_ACCEPTANCE: {
                "status": "pending",
                "completed_at_utc": "",
                "test_prefix": "",
                "scenarios_passed": scenario_list,
                "projects_index_verified": False,
                "metadata_verified": False,
                "cleanup_verified": False,
            },
            GITHUB_ASSET_SHA: {
                "status": "pending",
                "repo": EXPECTED_REPO,
                "release_tag": f"v{APP_VERSION}",
                "asset_name": f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe",
                "manifest_sha256": "",
                "asset_sha256": "",
                "asset_size": 0,
                "match": False,
            },
            LEGACY_UPDATE: {
                "status": "pending",
                "from_version": "",
                "to_version": APP_VERSION,
                "installed_app_id_preserved": False,
                "update_prompt_seen": False,
                "download_sha_verified": False,
                "post_update_launch_ok": False,
                "legacy_config_or_keyring_available": False,
            },
        },
    }


def _evaluate_real_s3_acceptance(
    gates: dict[str, Any],
    *,
    required_scenarios: tuple[str, ...],
) -> AcceptanceGateResult:
    gate_id = REAL_S3_ACCEPTANCE
    label = "Echter S3-Akzeptanztest"
    gate = gates.get(gate_id)
    if not isinstance(gate, dict):
        return AcceptanceGateResult(gate_id, label, False, "Evidenz fehlt.")
    if not _gate_passed(gate):
        return AcceptanceGateResult(gate_id, label, False, "status=passed fehlt.")
    missing = tuple(
        field for field in ("completed_at_utc", "test_prefix") if not str(gate.get(field, "") or "").strip()
    )
    if missing:
        return AcceptanceGateResult(gate_id, label, False, f"Pflichtfelder fehlen: {', '.join(missing)}.")
    scenarios = gate.get("scenarios_passed")
    if not isinstance(scenarios, list) or not all(str(value).strip() for value in scenarios):
        return AcceptanceGateResult(gate_id, label, False, "scenarios_passed muss eine gefuellte Liste sein.")
    passed_scenario_list = tuple(str(value).strip() for value in scenarios)
    passed_scenarios = set(passed_scenario_list)
    if len(passed_scenarios) != len(passed_scenario_list):
        return AcceptanceGateResult(gate_id, label, False, "scenarios_passed enthaelt Duplikate.")
    if required_scenarios:
        required_set = set(required_scenarios)
        unknown_scenarios = tuple(scenario for scenario in passed_scenario_list if scenario not in required_set)
        if unknown_scenarios:
            return AcceptanceGateResult(
                gate_id,
                label,
                False,
                f"S3-Szenarien sind nicht im Golden-Manifest: {', '.join(unknown_scenarios)}.",
            )
    missing_scenarios = tuple(scenario for scenario in required_scenarios if scenario not in passed_scenarios)
    if missing_scenarios:
        return AcceptanceGateResult(
            gate_id,
            label,
            False,
            f"S3-Szenarien fehlen: {', '.join(missing_scenarios)}.",
        )
    for flag in ("projects_index_verified", "metadata_verified", "cleanup_verified"):
        if gate.get(flag) is not True:
            return AcceptanceGateResult(gate_id, label, False, f"{flag}=true fehlt.")
    return AcceptanceGateResult(gate_id, label, True, str(gate.get("notes", "") or "OK"))


def _evaluate_github_asset_sha(
    evidence: dict[str, Any],
    gates: dict[str, Any],
    candidate_contract: dict[str, Any] | None,
) -> AcceptanceGateResult:
    gate = gates.get(GITHUB_ASSET_SHA)
    label = "GitHub Asset SHA"
    if not isinstance(gate, dict):
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, "Evidenz fehlt.")
    if not _gate_passed(gate):
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, "status=passed fehlt.")
    missing = tuple(
        field
        for field in ("repo", "release_tag", "asset_name", "manifest_sha256", "asset_sha256")
        if not str(gate.get(field, "") or "").strip()
    )
    if missing:
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, f"Pflichtfelder fehlen: {', '.join(missing)}.")
    if str(gate.get("repo", "") or "").strip() != EXPECTED_REPO:
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, "Repo passt nicht zum Update-Repository.")
    if gate.get("match") is not True:
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, "match=true fehlt.")
    asset_size = gate.get("asset_size")
    if isinstance(asset_size, bool) or not isinstance(asset_size, int) or asset_size <= 0:
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, "asset_size muss eine positive Zahl sein.")

    manifest_sha = str(gate.get("manifest_sha256", "") or "").strip().lower()
    asset_sha = str(gate.get("asset_sha256", "") or "").strip().lower()
    candidate_sha = str(evidence.get("candidate_installer_sha256", "") or "").strip().lower()
    for sha_value in (manifest_sha, asset_sha, candidate_sha):
        ok, message = validate_installer_sha256(sha_value)
        if not ok:
            return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, message)
    if manifest_sha != asset_sha:
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, "manifest_sha256 und asset_sha256 unterscheiden sich.")

    contract_issue = _candidate_contract_issue(evidence, gate, candidate_contract, manifest_sha)
    if contract_issue:
        return AcceptanceGateResult(GITHUB_ASSET_SHA, label, False, contract_issue)
    return AcceptanceGateResult(GITHUB_ASSET_SHA, label, True, str(gate.get("notes", "") or "OK"))


def _evaluate_legacy_update(gates: dict[str, Any]) -> AcceptanceGateResult:
    gate_id = LEGACY_UPDATE
    label = "Altversions-Update"
    gate = gates.get(gate_id)
    if not isinstance(gate, dict):
        return AcceptanceGateResult(gate_id, label, False, "Evidenz fehlt.")
    if not _gate_passed(gate):
        return AcceptanceGateResult(gate_id, label, False, "status=passed fehlt.")
    missing = tuple(
        field
        for field in ("completed_at_utc", "from_version", "to_version")
        if not str(gate.get(field, "") or "").strip()
    )
    if missing:
        return AcceptanceGateResult(gate_id, label, False, f"Pflichtfelder fehlen: {', '.join(missing)}.")
    from_version = str(gate.get("from_version", "") or "").strip()
    to_version = str(gate.get("to_version", "") or "").strip()
    if to_version != APP_VERSION:
        return AcceptanceGateResult(gate_id, label, False, "to_version passt nicht zur aktuellen Final-V2-Version.")
    if not is_remote_version_newer(to_version, from_version):
        return AcceptanceGateResult(gate_id, label, False, "to_version muss neuer als from_version sein.")
    for flag in (
        "installed_app_id_preserved",
        "update_prompt_seen",
        "download_sha_verified",
        "post_update_launch_ok",
        "legacy_config_or_keyring_available",
    ):
        if gate.get(flag) is not True:
            return AcceptanceGateResult(gate_id, label, False, f"{flag}=true fehlt.")
    return AcceptanceGateResult(gate_id, label, True, str(gate.get("notes", "") or "OK"))


def _candidate_contract_issue(
    evidence: dict[str, Any],
    gate: dict[str, Any],
    candidate_contract: dict[str, Any] | None,
    verified_sha: str,
) -> str:
    if candidate_contract is None:
        return ""
    release_manifest = candidate_contract.get("release_manifest_candidate", {})
    isolated_paths = candidate_contract.get("isolated_paths", {})
    comparisons = (
        ("candidate_version", evidence.get("candidate_version"), release_manifest.get("version")),
        ("candidate_manifest_path", evidence.get("candidate_manifest_path"), isolated_paths.get("manifest_path")),
        ("candidate_installer_name", evidence.get("candidate_installer_name"), release_manifest.get("installer_name")),
        ("release_tag", gate.get("release_tag"), release_manifest.get("release_tag")),
        ("asset_name", gate.get("asset_name"), release_manifest.get("installer_name")),
    )
    for field, actual, expected in comparisons:
        if str(actual or "").strip() != str(expected or "").strip():
            return f"{field} passt nicht zum Final-V2-Candidate-Vertrag."
    if str(evidence.get("candidate_installer_sha256", "") or "").strip().lower() != verified_sha:
        return "candidate_installer_sha256 passt nicht zur verifizierten GitHub-Asset-SHA."
    if str(release_manifest.get("installer_sha256", "") or "").strip().lower() != verified_sha:
        return "release_manifest_candidate.installer_sha256 passt nicht zur verifizierten GitHub-Asset-SHA."
    return ""


def _gate_passed(gate: dict[str, Any]) -> bool:
    return gate.get("status") == "passed" or gate.get("passed") is True


def _normalize_required_scenarios(values: Iterable[str]) -> tuple[str, ...]:
    scenario_ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        scenario_id = str(value or "").strip()
        if not scenario_id or scenario_id in seen:
            continue
        scenario_ids.append(scenario_id)
        seen.add(scenario_id)
    return tuple(scenario_ids)


def _gate_label(gate_id: str) -> str:
    return {
        REAL_S3_ACCEPTANCE: "Echter S3-Akzeptanztest",
        GITHUB_ASSET_SHA: "GitHub Asset SHA",
        LEGACY_UPDATE: "Altversions-Update",
    }.get(gate_id, gate_id)


__all__ = [
    "ACCEPTANCE_SCHEMA_VERSION",
    "DEFAULT_S3_ACCEPTANCE_SCENARIOS",
    "DEFAULT_CANDIDATE_MANIFEST_PATH",
    "EXPECTED_REPO",
    "GITHUB_ASSET_SHA",
    "LEGACY_INSTALLED_UPDATE",
    "LEGACY_UPDATE",
    "REAL_S3_ACCEPTANCE",
    "REQUIRED_ACCEPTANCE_GATES",
    "AcceptanceGateResult",
    "build_acceptance_evidence_template",
    "evaluate_acceptance_evidence",
    "load_acceptance_evidence",
]
