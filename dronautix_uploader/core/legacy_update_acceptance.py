"""Record manual evidence for the installed-legacy-to-final-V2 update gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from app_version import APP_VERSION

from .cutover_acceptance import LEGACY_UPDATE
from .update_service import is_remote_version_newer


@dataclass(frozen=True)
class LegacyUpdateAcceptanceResult:
    passed: bool
    message: str
    from_version: str
    to_version: str
    installed_app_id_preserved: bool
    update_prompt_seen: bool
    download_sha_verified: bool
    post_update_launch_ok: bool
    legacy_config_or_keyring_available: bool
    completed_at_utc: str = ""
    notes: str = ""

    @property
    def status(self) -> str:
        return "passed" if self.passed else "failed"


def build_legacy_update_acceptance_result(
    *,
    from_version: str,
    to_version: str = APP_VERSION,
    installed_app_id_preserved: bool = False,
    update_prompt_seen: bool = False,
    download_sha_verified: bool = False,
    post_update_launch_ok: bool = False,
    legacy_config_or_keyring_available: bool = False,
    notes: str = "",
    completed_at_utc: str = "",
) -> LegacyUpdateAcceptanceResult:
    """Validate manually observed legacy update evidence."""

    from_value = str(from_version or "").strip()
    to_value = str(to_version or "").strip() or APP_VERSION
    completed_at = completed_at_utc or _utc_now_iso()
    missing = []
    if not from_value:
        missing.append("from_version")
    if not to_value:
        missing.append("to_version")
    required_flags = {
        "installed_app_id_preserved": installed_app_id_preserved,
        "update_prompt_seen": update_prompt_seen,
        "download_sha_verified": download_sha_verified,
        "post_update_launch_ok": post_update_launch_ok,
        "legacy_config_or_keyring_available": legacy_config_or_keyring_available,
    }
    missing.extend(flag for flag, value in required_flags.items() if value is not True)
    if missing:
        passed = False
        message = f"Pflichtnachweise fehlen: {', '.join(missing)}."
    elif not is_remote_version_newer(to_value, from_value):
        passed = False
        message = "to_version muss neuer als from_version sein."
    else:
        passed = True
        message = "Installed legacy app updated to final V2 and launched successfully."

    return LegacyUpdateAcceptanceResult(
        passed=passed,
        message=message,
        from_version=from_value,
        to_version=to_value,
        installed_app_id_preserved=installed_app_id_preserved,
        update_prompt_seen=update_prompt_seen,
        download_sha_verified=download_sha_verified,
        post_update_launch_ok=post_update_launch_ok,
        legacy_config_or_keyring_available=legacy_config_or_keyring_available,
        completed_at_utc=completed_at,
        notes=notes or message,
    )


def legacy_update_result_to_acceptance_gate(result: LegacyUpdateAcceptanceResult) -> dict[str, Any]:
    """Convert legacy update evidence into the cutover acceptance gate shape."""

    return {
        "status": result.status,
        "completed_at_utc": result.completed_at_utc,
        "from_version": result.from_version,
        "to_version": result.to_version,
        "installed_app_id_preserved": result.installed_app_id_preserved,
        "update_prompt_seen": result.update_prompt_seen,
        "download_sha_verified": result.download_sha_verified,
        "post_update_launch_ok": result.post_update_launch_ok,
        "legacy_config_or_keyring_available": result.legacy_config_or_keyring_available,
        "notes": result.notes or result.message,
    }


def merge_legacy_update_into_acceptance_evidence(
    evidence: dict[str, Any],
    result: LegacyUpdateAcceptanceResult,
) -> dict[str, Any]:
    """Return acceptance evidence with the legacy update gate replaced."""

    merged = json.loads(json.dumps(evidence or {}, ensure_ascii=False))
    merged.setdefault("schema_version", 1)
    merged["candidate_version"] = result.to_version
    gates = merged.setdefault("gates", {})
    gates[LEGACY_UPDATE] = legacy_update_result_to_acceptance_gate(result)
    return merged


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "LegacyUpdateAcceptanceResult",
    "build_legacy_update_acceptance_result",
    "legacy_update_result_to_acceptance_gate",
    "merge_legacy_update_into_acceptance_evidence",
]
