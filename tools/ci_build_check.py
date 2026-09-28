"""Build the release EXE with the release PyInstaller command and start it once.

Used by CI (``.github/workflows/tests.yml``) and before a release. Unlike
``build_exe.py`` it never runs Inno Setup, never writes ``latest-release.json``
and keeps all output (including the generated ``.spec``) outside the repository.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import build_exe  # noqa: E402 - needs the repository root on sys.path
from app_version import APP_EXE_NAME  # noqa: E402
from tools.windows_build import build_environment, verify_frozen_startup  # noqa: E402

VERSION_FILES = (build_exe.VERSION_INFO_FILE, build_exe.INSTALLER_VERSION_FILE)


def version_files_are_in_sync() -> bool:
    """Regenerate the version files and report whether the committed ones were stale."""

    build_exe.sync_version_files()
    result = subprocess.run(
        ["git", "diff", "--exit-code", "--", *VERSION_FILES],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[FEHLER] version_info.txt / installer_version.iss passen nicht zu app_version.py.")
        print("         'python build_exe.py' bzw. sync_version_files() ausführen und committen.")
        print(result.stdout)
        return False
    print("[OK] Versionsdateien passen zu app_version.py")
    return True


def build_and_verify(output_root: Path) -> dict:
    command = build_exe.pyinstaller_command(
        extra_args=(
            f"--distpath={output_root / 'dist'}",
            f"--workpath={output_root / 'build'}",
            f"--specpath={output_root}",
            "--noconfirm",
        ),
        root=str(REPO_ROOT),
    )
    print("Befehl:", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=REPO_ROOT, env=build_environment())
    return verify_frozen_startup(output_root / "dist" / APP_EXE_NAME)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-version-files",
        action="store_true",
        help="Fail when version_info.txt/installer_version.iss are out of date (CI).",
    )
    parser.add_argument("--output-dir", default="", help="Keep the build here instead of a temporary folder.")
    args = parser.parse_args(argv)

    os.chdir(REPO_ROOT)
    if args.check_version_files and not version_files_are_in_sync():
        return 1
    if not build_exe.check_build_prerequisites():
        return 1
    try:
        if args.output_dir:
            build_and_verify(Path(args.output_dir).resolve())
        else:
            with tempfile.TemporaryDirectory(prefix="dronautix_ci_build_") as directory:
                build_and_verify(Path(directory))
    except (subprocess.CalledProcessError, RuntimeError) as error:
        print(f"[FEHLER] Build-Check fehlgeschlagen: {error}")
        return 1
    print("[OK] EXE gebaut und Start-Selbsttest bestanden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
