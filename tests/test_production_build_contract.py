from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_production_build_packages_v2_and_uses_safe_postinstall_launch():
    build_script = (REPO_ROOT / "build_exe.py").read_text(encoding="utf-8")
    installer = (REPO_ROOT / "Dronautix_Pointcloud_Uploader.iss").read_text(encoding="utf-8")

    assert 'ENTRYPOINT = "Dronautix_Pointcloud_Uploader_v2_final.py"' in build_script
    assert 'sys.executable,' in build_script
    assert '"PyInstaller",' in build_script
    assert '"--hidden-import=PySide6.QtWidgets"' in build_script
    assert 'Name: "desktopicon"' in installer
    assert "Flags: unchecked" not in installer
    assert 'Filename: "{win}\\explorer.exe"' in installer
    assert "%TEMP%\\_MEI*" not in installer
