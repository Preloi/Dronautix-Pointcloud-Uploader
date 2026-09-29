"""Measure the real main window in a fresh process (used by test_qt_layout_contract).

Runs offscreen with the Windows fonts so text metrics are real and do not
depend on which test created a QApplication first. Prints one JSON document.
"""

import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from dronautix_uploader.qt_app import main_window  # noqa: E402
from dronautix_uploader.qt_app.runtime_services import (  # noqa: E402
    ProjectManagementRuntimeConfig,
    create_runtime_controller_bundle,
)
from dronautix_uploader.qt_app.style import APP_STYLE  # noqa: E402

SIZES = ((1024, 680), (1100, 750), (1400, 900))


def _project(index, name, customer, disabled=False, multi=False):
    project = {
        "id": f"a{index:05x}",
        "projekt": name,
        "kunde": customer,
        "format": "potree",
        "datum": f"2026-09-{10 + index:02d}T10:00:00",
        "link": f"https://pointcloud.dronautix.at/index.html?id=a{index:05x}",
        "viewer_path": f"k/a{index:05x}/p",
        "s3_path": f"pointclouds/k/a{index:05x}/p",
        "crs": "EPSG:31256",
        "vertical_crs": "EPSG:5778",
    }
    if multi:
        project["format"] = "multi"
        project["pointclouds"] = [
            {"name": f"Befliegung Teil {j}", "format": "potree", "s3_path": f"pointclouds/k/a{index:05x}/c{j}",
             "viewer_path": f"k/a{index:05x}/c{j}", "crs": "EPSG:31256"}
            for j in range(1, 4)
        ]
    return project


class FakeS3:
    def __init__(self):
        self.index = {
            "projects": [
                _project(1, "Freileitung Bodenabstand Visualisierung", "Netz NÖ"),
                _project(2, "Steinbruch Volumenberechnung Q3", "Asamer", multi=True),
                _project(3, "Brückeninspektion A1 km 213,4", "ASFINAG"),
            ],
            "disabled_projects": [_project(4, "Deaktivierter Kundenlink", "Kunde X", disabled=True)],
        }

    def get_object(self, Bucket, Key):
        data = self.index if Key == "projects_index.json" else {}
        return {"Body": io.BytesIO(json.dumps(data).encode("utf-8")), "ETag": '"1"'}


def _pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _clipped_buttons(page):
    clipped = []
    for button in page.findChildren(QtWidgets.QAbstractButton):
        if not button.isVisible() or not button.text().strip():
            continue
        # sizeHint includes stylesheet padding and the font's real text width.
        if button.width() + 1 < button.sizeHint().width():
            clipped.append(f"{button.text()!r} {button.width()}<{button.sizeHint().width()}")
    return clipped


def main():
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="dx_layout_appdata_")
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    app.setFont(QtGui.QFont("Segoe UI", 10))
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, os.environ["APPDATA"])
    main_window.cleanup_stale_upload_temp_dirs = lambda: ()

    config = ProjectManagementRuntimeConfig(
        aws_access_key_id="AKIA_TEST", aws_secret_access_key="s", region_name="eu-central-1", bucket_name="bucket"
    )
    bundle = create_runtime_controller_bundle(config, s3_client=FakeS3())
    window = main_window.create_main_window(
        QtCore,
        QtGui,
        QtWidgets,
        project_provider=bundle.project_provider,
        project_controller=bundle.project_controller,
        upload_controller=bundle.upload_controller,
        project_runtime_status=bundle.status,
    )
    report = {"pages": {}}
    window.show()
    _pump(app, 0.5)
    for width, height in SIZES:
        window.resize(width, height)
        for name in list(window._pages):
            window._select_page(name)
            _pump(app, 0.3)
            page = window._pages[name]
            entry = {"clipped_buttons": _clipped_buttons(page)}
            if name == "Projektverwaltung":
                table = page.findChild(QtWidgets.QTableView, "ProjectsTable")
                table.selectRow(0)
                _pump(app, 0.2)
                header = table.horizontalHeader()
                viewport = table.viewport().width()
                visible = {}
                for column in range(table.model().columnCount()):
                    label = table.model().headerData(column, QtCore.Qt.Horizontal)
                    right = header.sectionViewportPosition(column) + header.sectionSize(column)
                    visible[label] = (not table.isColumnHidden(column)) and 0 <= header.sectionViewportPosition(column) and right <= viewport + 1
                entry["visible_columns"] = visible
                entry["rows"] = table.model().rowCount()
                entry["clipped_buttons"] = _clipped_buttons(page)
            report["pages"][f"{name}@{width}"] = entry
    window._close_action_progress_dialog(None)
    window.close()
    print(json.dumps(report, ensure_ascii=True))  # pipe-safe regardless of the console code page


if __name__ == "__main__":
    main()
