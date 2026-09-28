"""Check that latest-release.json points to a real, matching GitHub release asset.

Publishing the manifest (pushing it to ``master``) makes every installed app
offer the update, so it must only reference an uploaded installer whose
SHA-256 equals ``installer_sha256``. Used by CI and before publishing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.update_service import (  # noqa: E402
    calculate_url_sha256,
    get_update_installer_url,
    parse_version_tuple,
    validate_installer_sha256,
    validate_update_download_info,
)


def verify_release_manifest(
    manifest: dict,
    *,
    expected_version: str = "",
    opener=None,
    timeout_seconds: float = 300.0,
) -> list[str]:
    """Return problems; an empty list means the manifest may be published."""

    problems: list[str] = []
    version = str(manifest.get("version", "") or "").strip()
    installer_name = str(manifest.get("installer_name", "") or "").strip()
    installer_url = get_update_installer_url(manifest)
    expected_sha = str(manifest.get("installer_sha256", "") or "").strip().lower()

    ok, message = validate_update_download_info(manifest, installer_url, installer_name)
    if not ok:
        problems.append(f"Manifest ungültig: {message}")
    ok, message = validate_installer_sha256(expected_sha)
    if not ok:
        problems.append(message)
    if expected_version and parse_version_tuple(version) != parse_version_tuple(expected_version):
        problems.append(f"Manifest-Version {version!r} passt nicht zu app_version.py ({expected_version!r}).")
    if problems:
        return problems

    try:
        actual_sha, size = calculate_url_sha256(installer_url, timeout_seconds=timeout_seconds, opener=opener)
    except Exception as error:  # network, 404 of a missing asset, ...
        return [f"Release-Asset nicht abrufbar: {installer_url}: {error}"]
    if size <= 0:
        problems.append(f"Release-Asset ist leer: {installer_url}")
    if actual_sha.lower() != expected_sha:
        problems.append(
            f"SHA-256 des Release-Assets ({actual_sha}) weicht vom Manifest ab ({expected_sha})."
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(REPO_ROOT / "latest-release.json"))
    parser.add_argument(
        "--expect-app-version",
        action="store_true",
        help="Also require manifest version == app_version.APP_VERSION.",
    )
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    expected_version = ""
    if args.expect_app_version:
        from app_version import APP_VERSION

        expected_version = APP_VERSION
    problems = verify_release_manifest(manifest, expected_version=expected_version)
    if problems:
        print("[FEHLER] Manifest darf so nicht veröffentlicht werden:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"[OK] {manifest['installer_name']} ({manifest['version']}) passt zu installer_sha256.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
