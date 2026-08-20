from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_production_build_packages_v2_and_uses_safe_postinstall_launch():
    build_script = (REPO_ROOT / "build_exe.py").read_text(encoding="utf-8")
    installer = (REPO_ROOT / "Dronautix_Pointcloud_Uploader.iss").read_text(encoding="utf-8")

    assert 'ENTRYPOINT = "Dronautix_Pointcloud_Uploader_v2_final.py"' in build_script
    assert 'sys.executable,' in build_script
    assert '"PyInstaller",' in build_script
    assert '"--hidden-import=PySide6.QtWidgets"' in build_script
    assert 'os.path.join("bundled_tools", "GLBToolchain")' in build_script
    assert "viewer-capabilities.v1.json" in build_script
    assert "toolchain-manifest.v1.json" in build_script
    assert 'Name: "desktopicon"' in installer
    assert "Flags: unchecked" not in installer
    assert 'Filename: "{win}\\explorer.exe"' in installer
    assert "%TEMP%\\_MEI*" not in installer


def test_release_manifest_is_written_only_after_final_installer_build():
    build_script = (REPO_ROOT / "build_exe.py").read_text(encoding="utf-8")

    sync_body = build_script.split("def sync_version_files():", 1)[1].split("def sync_output_manifest():", 1)[0]
    assert "LATEST_RELEASE_FILE" not in sync_body
    assert build_script.index("subprocess.run([inno_setup, INNO_SETUP_SCRIPT], check=True)") < build_script.rindex(
        "write_release_manifest_after_installer_build()"
    )
    assert '"installer_sha256": calculate_file_sha256(installer_path)' in build_script
