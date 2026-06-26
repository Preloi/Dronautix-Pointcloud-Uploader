"""UI-free update manifest and installer verification helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
from typing import Any

from .constants import UPDATE_MANIFEST_URL, UPDATE_REPO_NAME, UPDATE_REPO_OWNER


@dataclass(frozen=True)
class UpdateDownloadResult:
    ok: bool
    message: str
    installer_path: str = ""
    installer_url: str = ""
    installer_sha256: str = ""


def parse_version_tuple(version_value: str) -> tuple[int, ...]:
    if not version_value:
        return tuple()
    return tuple(int(part) for part in re.findall(r"\d+", str(version_value)))


def is_remote_version_newer(remote_version: str, local_version: str) -> bool:
    remote_tuple = parse_version_tuple(remote_version)
    local_tuple = parse_version_tuple(local_version)
    max_length = max(len(remote_tuple), len(local_tuple))
    remote_tuple += (0,) * (max_length - len(remote_tuple))
    local_tuple += (0,) * (max_length - len(local_tuple))
    return remote_tuple > local_tuple


def get_update_installer_url(manifest: dict[str, object]) -> str:
    installer_url = str(manifest.get("installer_url", "")).strip()
    if installer_url:
        return installer_url

    remote_version = str(manifest.get("version", "")).strip()
    installer_name = str(manifest.get("installer_name", "")).strip()
    repo_owner = str(manifest.get("repo_owner", UPDATE_REPO_OWNER)).strip()
    repo_name = str(manifest.get("repo_name", UPDATE_REPO_NAME)).strip()
    release_tag = str(manifest.get("release_tag", f"v{remote_version}")).strip()
    if not remote_version or not installer_name or not repo_owner or not repo_name or not release_tag:
        return ""

    return (
        f"https://github.com/{repo_owner}/{repo_name}/releases/download/"
        f"{urllib.parse.quote(release_tag, safe='')}/{urllib.parse.quote(installer_name)}"
    )


def is_safe_installer_name(installer_name: str) -> bool:
    if not installer_name or os.path.basename(installer_name) != installer_name:
        return False
    return installer_name.lower().endswith(".exe")


def load_update_manifest(
    manifest_url: str = UPDATE_MANIFEST_URL,
    *,
    timeout_seconds: float = 10.0,
    opener: Any = None,
) -> dict[str, object]:
    request = urllib.request.Request(
        manifest_url,
        headers={"User-Agent": "DronautixPointcloudUploaderV2/preview"},
    )
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=timeout_seconds) as response:
        manifest = json.loads(response.read().decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Update-Manifest muss ein JSON-Objekt sein.")
    return manifest


def validate_update_download_info(
    manifest: dict[str, object],
    installer_url: str,
    installer_name: str,
) -> tuple[bool, str]:
    remote_version = str(manifest.get("version", "")).strip()
    expected_tag = f"v{remote_version}"
    if not remote_version or not is_safe_installer_name(installer_name):
        return False, "ungueltige Versions- oder Installerangabe"

    repo_owner = str(manifest.get("repo_owner", UPDATE_REPO_OWNER)).strip()
    repo_name = str(manifest.get("repo_name", UPDATE_REPO_NAME)).strip()
    release_tag = str(manifest.get("release_tag", expected_tag)).strip()
    if repo_owner != UPDATE_REPO_OWNER or repo_name != UPDATE_REPO_NAME or release_tag != expected_tag:
        return False, "Update-Manifest verweist nicht auf das erwartete Release"

    parsed_url = urllib.parse.urlparse(installer_url)
    expected_path = (
        f"/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}/releases/download/"
        f"{urllib.parse.quote(expected_tag, safe='')}/{urllib.parse.quote(installer_name, safe='')}"
    )
    if parsed_url.scheme != "https" or parsed_url.netloc.lower() != "github.com":
        return False, "Installer-URL muss auf https://github.com zeigen"
    if parsed_url.path != expected_path:
        return False, "Installer-URL passt nicht zum erwarteten Release-Pfad"
    return True, "OK"


def validate_installer_sha256(expected_sha256: str) -> tuple[bool, str]:
    expected_sha256 = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        return False, "Update-Manifest enthaelt keinen gueltigen SHA-256 Hash"
    return True, "OK"


def download_update_installer(
    installer_url: str,
    installer_name: str,
    download_dir: str | os.PathLike[str],
    *,
    timeout_seconds: float = 60.0,
    opener: Any = None,
) -> str:
    if not is_safe_installer_name(installer_name):
        raise ValueError("ungueltiger Installer-Dateiname")

    target_dir = Path(download_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / installer_name
    temp_path = target_dir / f"{installer_name}.download"
    if temp_path.exists():
        temp_path.unlink()

    request = urllib.request.Request(
        installer_url,
        headers={"User-Agent": "DronautixPointcloudUploaderV2/preview"},
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout_seconds) as response, open(temp_path, "wb") as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                output.write(chunk)
        os.replace(temp_path, target_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
    return str(target_path)


def calculate_url_sha256(
    url: str,
    *,
    timeout_seconds: float = 60.0,
    opener: Any = None,
) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    byte_count = 0
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DronautixPointcloudUploaderV2/preview"},
    )
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=timeout_seconds) as response:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            byte_count += len(chunk)
            sha256.update(chunk)
    return sha256.hexdigest(), byte_count


def download_and_verify_installer(
    manifest: dict[str, object],
    download_dir: str | os.PathLike[str],
    *,
    timeout_seconds: float = 60.0,
    opener: Any = None,
) -> UpdateDownloadResult:
    installer_name = str(manifest.get("installer_name", "") or "").strip()
    installer_url = get_update_installer_url(manifest)
    expected_sha256 = str(manifest.get("installer_sha256", "") or "").strip().lower()

    valid_download, download_message = validate_update_download_info(manifest, installer_url, installer_name)
    if not valid_download:
        return UpdateDownloadResult(False, download_message, installer_url=installer_url)

    valid_sha, sha_message = validate_installer_sha256(expected_sha256)
    if not valid_sha:
        return UpdateDownloadResult(False, sha_message, installer_url=installer_url)

    try:
        installer_path = download_update_installer(
            installer_url,
            installer_name,
            download_dir,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except Exception as exc:
        return UpdateDownloadResult(False, f"Installer-Download fehlgeschlagen: {exc}", installer_url=installer_url)

    ok, hash_message = verify_installer_hash(installer_path, expected_sha256)
    if not ok:
        try:
            Path(installer_path).unlink(missing_ok=True)
        except Exception:
            pass
        return UpdateDownloadResult(
            False,
            hash_message,
            installer_url=installer_url,
            installer_sha256=expected_sha256,
        )

    return UpdateDownloadResult(
        True,
        "OK",
        installer_path=installer_path,
        installer_url=installer_url,
        installer_sha256=expected_sha256,
    )


def calculate_file_sha256(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_installer_hash(installer_path: str, expected_sha256: str) -> tuple[bool, str]:
    ok, message = validate_installer_sha256(expected_sha256)
    if not ok:
        return ok, message

    expected_sha256 = str(expected_sha256 or "").strip().lower()
    actual_sha256 = calculate_file_sha256(installer_path)
    if actual_sha256.lower() != expected_sha256:
        return False, "Installer-Hash stimmt nicht mit dem Release-Manifest ueberein"
    return True, "OK"
