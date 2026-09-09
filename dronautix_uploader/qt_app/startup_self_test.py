"""Exercise packaged Qt and the real window without user settings or services."""

import json
from pathlib import Path
import sys
import tempfile
import traceback


def run_startup_self_test(report_path):
    report = Path(report_path)
    result = {"ok": False, "frozen": bool(getattr(sys, "frozen", False))}
    previous_temp = tempfile.tempdir
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        from app_version import APP_VERSION
        from .main_window import create_main_window
        from .style import APP_STYLE

        with tempfile.TemporaryDirectory(prefix="dronautix_qt_self_test_") as directory:
            # The window's cleanup and QSettings must never touch user state.
            tempfile.tempdir = directory
            QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
            for scope in (QtCore.QSettings.UserScope, QtCore.QSettings.SystemScope):
                QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, scope, directory)
            app = QtWidgets.QApplication([sys.argv[0]])
            app.setStyle("Fusion")
            app.setStyleSheet(APP_STYLE)
            window = create_main_window(QtCore, QtGui, QtWidgets, project_previews=())
            window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
            window.show()
            QtCore.QTimer.singleShot(100, app.quit)
            exit_code = app.exec()
            if exit_code != 0 or window.grab().isNull():
                raise RuntimeError("Qt window event loop or rendering failed")
            result.update(ok=True, version=APP_VERSION, qt=QtCore.qVersion(),
                          platform=app.platformName(), pages=window.stack.count())
            window.close()
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        tempfile.tempdir = previous_temp
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
