# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['Dronautix_Pointcloud_Uploader_v2_final.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.ico', '.'),
        ('bundled_tools/PotreeConverter', 'bundled_tools/PotreeConverter'),
        ('bundled_tools/GLBToolchain', 'bundled_tools/GLBToolchain'),
    ],
    hiddenimports=['keyring', 'keyring.backends.Windows', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'boto3'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Dronautix_Pointcloud_Uploader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['icon.ico'],
)
