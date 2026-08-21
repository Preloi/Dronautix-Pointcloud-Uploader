import importlib
import sys


class _TextWidget:
    def __init__(self, value):
        self._value = value

    def text(self):
        return self._value


class _PlainTextWidget(_TextWidget):
    def toPlainText(self):
        return self._value


class _CheckWidget:
    def __init__(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked


def test_project_management_dialogs_import_without_qt_or_tk_bindings():
    module = importlib.import_module("dronautix_uploader.qt_app.project_management_dialogs")

    assert module is not None
    assert "PySide6" not in sys.modules
    assert "tkinter" not in sys.modules
    assert "customtkinter" not in sys.modules


def test_replace_dialog_state_from_inputs_uses_injected_converter_and_temp_output():
    dialogs = importlib.import_module("dronautix_uploader.qt_app.project_management_dialogs")
    models = importlib.import_module("dronautix_uploader.qt_app.project_management_dialog_models")

    # The simplified replace dialog only collects sources; the bundled converter
    # and a temporary output folder are injected by the caller, and CRS is
    # auto-detected later (no CRS/converter/output fields in the dialog).
    state = dialogs._replace_dialog_state_from_inputs(
        _PlainTextWidget(" a.copc.laz \n\n b.copc.laz "),
        "C:/bundled/PotreeConverter.exe",
        "C:/temp/out",
    )

    payload = models.validate_replace_all_dialog_state(state)

    assert state.source_paths == ("a.copc.laz", "b.copc.laz")
    assert state.converter_path == "C:/bundled/PotreeConverter.exe"
    assert state.output_base_dir == "C:/temp/out"
    assert state.overwrite is True
    assert state.horizontal_crs == ""
    assert state.vertical_crs == ""
    assert payload.crs_info_by_source_path is None


def test_add_models_prompt_returns_all_selected_glb_paths_without_qt_runtime():
    dialogs = importlib.import_module("dronautix_uploader.qt_app.project_management_dialogs")
    project_model = importlib.import_module("dronautix_uploader.qt_app.project_management")

    class FileDialog:
        @staticmethod
        def getOpenFileNames(parent, title, start_dir, file_filter):
            assert "Projekt" in title
            assert file_filter == "GLB-Modelle (*.glb)"
            return (["C:/models/fassade.glb", "C:/models/dach.glb"], file_filter)

    class QtWidgets:
        QFileDialog = FileDialog

    project = project_model.make_project_preview(
        {"id": "project", "projekt": "Projekt", "kunde": "Kunde"},
        disabled=False,
    )

    payload = dialogs.prompt_add_project_models(QtWidgets, None, project)

    assert payload.source_paths == ("C:/models/fassade.glb", "C:/models/dach.glb")


def test_crs_repair_suggestions_prefill_horizontal_and_vertical_values_from_project_clouds():
    dialogs = importlib.import_module("dronautix_uploader.qt_app.project_management_dialogs")
    project_model = importlib.import_module("dronautix_uploader.qt_app.project_management")
    project = project_model.make_project_preview(
        {
            "id": "project",
            "projekt": "Terra Hydron",
            "kunde": "Kunde",
            "pointclouds": [
                {"name": "Mellitzgraben", "format": "potree", "crs": "EPSG:31255 + EPSG:5778"},
            ],
        },
        disabled=False,
    )

    assert dialogs._suggest_project_crs(project) == ("EPSG:31255", "EPSG:5778")


def test_crs_repair_suggestions_never_mix_epsg_codes_from_different_clouds():
    dialogs = importlib.import_module("dronautix_uploader.qt_app.project_management_dialogs")
    project_model = importlib.import_module("dronautix_uploader.qt_app.project_management")
    project = project_model.make_project_preview(
        {
            "id": "project",
            "pointclouds": [
                {"name": "Ohne Höhe", "format": "potree", "crs": "EPSG:25832"},
                {"name": "Donor", "format": "potree", "crs": "EPSG:31255 + EPSG:5778"},
            ],
        },
        disabled=False,
    )

    assert dialogs._suggest_project_crs(project) == ("EPSG:31255", "EPSG:5778")
