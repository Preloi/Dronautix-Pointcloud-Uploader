"""Preview entry point for the PySide6 QtWidgets UI skeleton.

This module intentionally does not import PySide6 at module import time.
Run it directly to start the preview UI:

    python Dronautix_Pointcloud_Uploader_v2.py
"""

from dronautix_uploader.qt_app.app import run


if __name__ == "__main__":
    raise SystemExit(run())
