"""Verify the final V2 GitHub release asset against the candidate manifest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .cutover_acceptance import GITHUB_ASSET_SHA
from .update_service import (
    calculate_file_sha256,
    calculate_url_sha256,
    download_update_installer,
    get_update_installer_url,
    validate_installer_sha256,
    validate_update_download_info,
)


@dataclass(frozen=True)
class GitHubAssetShaVerificationResult:
    """Result of comparing the manifest SHA with the downloadable release asset."""

    passed: bool
    message: str
    repo: str
    release_tag: str
    asset_name: str
    installer_url: str
    manifest_sha256: str
    asset_sha256: str = ""
    asset_size: int = 0
    source_path: str = ""
    completed_at_utc: str = ""

    @property
    def status(self) -> str:
        return "passed" if self.passed else "failed"


def verify_github_asset_sha(
    candidate_contract: dict[str, Any],
    *,
    asset_path: str | Path | None = None,
    download_dir: str | Path | None = None,
    opener: Any = None,
    timeout_seconds: float = 60.0,
) -> GitHubAssetShaVerificationResult:
    """Download or inspect the release asset and compare it to the candidate SHA."""

    release_manifest = _release_manifest_from_contract(candidate_contract)
    repo_owner = str(release_manifest.get("repo_owner", "") or "").strip()
    repo_name = str(release_manifest.get("repo_name", "") or "").strip()
    repo = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else ""
    release_tag = str(release_manifest.get("release_tag", "") or "").strip()
    asset_name = str(release_manifest.get("installer_name", "") or "").strip()
    installer_url = get_update_installer_url(release_manifest)
    manifest_sha = str(release_manifest.get("installer_sha256", "") or "").strip().lower()
    completed_at = _utc_now_iso()

    download_ok, download_message = validate_update_download_info(release_manifest, installer_url, asset_name)
    if not download_ok:
        return _result(
            False,
            download_message,
            repo=repo,
            release_tag=release_tag,
            asset_name=asset_name,
            installer_url=installer_url,
            manifest_sha=manifest_sha,
            completed_at=completed_at,
        )

    sha_ok, sha_message = validate_installer_sha256(manifest_sha)
    if not sha_ok:
        return _result(
            False,
            sha_message,
            repo=repo,
            release_tag=release_tag,
            asset_name=asset_name,
            installer_url=installer_url,
            manifest_sha=manifest_sha,
            completed_at=completed_at,
        )

    try:
        asset_sha, asset_size, verified_path = _asset_hash(
            installer_url,
            asset_name,
            asset_path=asset_path,
            download_dir=download_dir,
            opener=opener,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return _result(
            False,
            f"GitHub-Asset konnte nicht verifiziert werden: {exc}",
            repo=repo,
            release_tag=release_tag,
            asset_name=asset_name,
            installer_url=installer_url,
            manifest_sha=manifest_sha,
            completed_at=completed_at,
        )

    if asset_size <= 0:
        return _result(
            False,
            "GitHub-Asset ist leer.",
            repo=repo,
            release_tag=release_tag,
            asset_name=asset_name,
            installer_url=installer_url,
            manifest_sha=manifest_sha,
            asset_sha=asset_sha,
            asset_size=asset_size,
            source_path=verified_path,
            completed_at=completed_at,
        )

    if asset_sha != manifest_sha:
        return _result(
            False,
            "GitHub-Asset-SHA unterscheidet sich vom Candidate-Manifest.",
            repo=repo,
            release_tag=release_tag,
            asset_name=asset_name,
            installer_url=installer_url,
            manifest_sha=manifest_sha,
            asset_sha=asset_sha,
            asset_size=asset_size,
            source_path=verified_path,
            completed_at=completed_at,
        )

    return _result(
        True,
        "GitHub release asset SHA matches the final-V2 candidate manifest.",
        repo=repo,
        release_tag=release_tag,
        asset_name=asset_name,
        installer_url=installer_url,
        manifest_sha=manifest_sha,
        asset_sha=asset_sha,
        asset_size=asset_size,
        source_path=verified_path,
        completed_at=completed_at,
    )


def github_asset_sha_result_to_acceptance_gate(result: GitHubAssetShaVerificationResult) -> dict[str, Any]:
    """Convert a verification result into the github_asset_sha acceptance gate."""

    return {
        "status": result.status,
        "completed_at_utc": result.completed_at_utc,
        "repo": result.repo,
        "release_tag": result.release_tag,
        "asset_name": result.asset_name,
        "asset_url": result.installer_url,
        "manifest_sha256": result.manifest_sha256,
        "asset_sha256": result.asset_sha256,
        "asset_size": result.asset_size,
        "match": result.passed and result.manifest_sha256 == result.asset_sha256,
        "notes": result.message,
    }


def merge_github_asset_sha_into_acceptance_evidence(
    evidence: dict[str, Any],
    result: GitHubAssetShaVerificationResult,
    candidate_contract: dict[str, Any],
    *,
    candidate_manifest_path: str | Path = "",
) -> dict[str, Any]:
    """Return acceptance evidence with the GitHub asset SHA gate replaced."""

    merged = json.loads(json.dumps(evidence or {}, ensure_ascii=False))
    release_manifest = _release_manifest_from_contract(candidate_contract)
    isolated_paths = candidate_contract.get("isolated_paths", {})
    if not isinstance(isolated_paths, dict):
        isolated_paths = {}

    merged.setdefault("schema_version", 1)
    merged["candidate_version"] = str(release_manifest.get("version", "") or "")
    merged["candidate_manifest_path"] = str(
        candidate_manifest_path or isolated_paths.get("manifest_path", "") or ""
    )
    merged["candidate_installer_name"] = result.asset_name
    merged["candidate_installer_sha256"] = result.manifest_sha256
    merged["production_release_files_not_written"] = list(
        candidate_contract.get("production_release_files_not_written", [])
    )
    gates = merged.setdefault("gates", {})
    gates[GITHUB_ASSET_SHA] = github_asset_sha_result_to_acceptance_gate(result)
    return merged


def _release_manifest_from_contract(candidate_contract: dict[str, Any]) -> dict[str, Any]:
    release_manifest = candidate_contract.get("release_manifest_candidate", candidate_contract)
    return release_manifest if isinstance(release_manifest, dict) else {}


def _asset_hash(
    installer_url: str,
    asset_name: str,
    *,
    asset_path: str | Path | None,
    download_dir: str | Path | None,
    opener: Any,
    timeout_seconds: float,
) -> tuple[str, int, str]:
    if asset_path is not None:
        path = Path(asset_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.name != asset_name:
            raise ValueError("Lokaler Asset-Dateiname passt nicht zum Candidate-Manifest.")
        return calculate_file_sha256(str(path)).lower(), path.stat().st_size, str(path)

    if download_dir is not None:
        downloaded_path = Path(
            download_update_installer(
                installer_url,
                asset_name,
                download_dir,
                opener=opener,
                timeout_seconds=timeout_seconds,
            )
        )
        return calculate_file_sha256(str(downloaded_path)).lower(), downloaded_path.stat().st_size, str(downloaded_path)

    asset_sha, asset_size = calculate_url_sha256(installer_url, opener=opener, timeout_seconds=timeout_seconds)
    return asset_sha.lower(), asset_size, installer_url


def _result(
    passed: bool,
    message: str,
    *,
    repo: str,
    release_tag: str,
    asset_name: str,
    installer_url: str,
    manifest_sha: str,
    asset_sha: str = "",
    asset_size: int = 0,
    source_path: str = "",
    completed_at: str,
) -> GitHubAssetShaVerificationResult:
    return GitHubAssetShaVerificationResult(
        passed=passed,
        message=message,
        repo=repo,
        release_tag=release_tag,
        asset_name=asset_name,
        installer_url=installer_url,
        manifest_sha256=manifest_sha,
        asset_sha256=asset_sha,
        asset_size=asset_size,
        source_path=source_path,
        completed_at_utc=completed_at,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "GitHubAssetShaVerificationResult",
    "github_asset_sha_result_to_acceptance_gate",
    "merge_github_asset_sha_into_acceptance_evidence",
    "verify_github_asset_sha",
]
