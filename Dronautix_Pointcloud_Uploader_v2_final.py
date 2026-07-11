"""Final-candidate entry point for the PySide6 QtWidgets V2 UI.

This module intentionally does not import PySide6 at module import time.
It is for isolated final-candidate packaging only; the existing preview
entry point remains `Dronautix_Pointcloud_Uploader_v2.py`.
"""

from dronautix_uploader.qt_app.app import run


if __name__ == "__main__":
    raise SystemExit(run(mode="final"))
