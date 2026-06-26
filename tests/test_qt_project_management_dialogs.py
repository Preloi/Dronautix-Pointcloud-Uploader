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


def test_replace_dialog_state_from_inputs_preserves_crs_fields_for_payload_validation():
    dialogs = importlib.import_module("dronautix_uploader.qt_app.project_management_dialogs")
    models = importlib.import_module("dronautix_uploader.qt_app.project_management_dialog_models")

    state = dialogs._replace_dialog_state_from_inputs(
        _PlainTextWidget(" a.copc.laz \n\n b.copc.laz "),
        _TextWidget(" converter.exe "),
        _TextWidget(" C:/out "),
        _TextWidget(" EPSG:25832 "),
        _TextWidget(" DHHN2016 "),
        _CheckWidget(True),
    )

    payload = models.validate_replace_all_dialog_state(state)

    assert state.source_paths == ("a.copc.laz", "b.copc.laz")
    assert state.horizontal_crs == " EPSG:25832 "
    assert state.vertical_crs == " DHHN2016 "
    assert state.overwrite is True
    assert payload.crs_info_by_source_path == {
        "a.copc.laz": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "vertical_crs": "DHHN2016",
            "vertical_epsg": "DHHN2016",
            "vertical_projection": "DHHN2016",
        },
        "b.copc.laz": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "vertical_crs": "DHHN2016",
            "vertical_epsg": "DHHN2016",
            "vertical_projection": "DHHN2016",
        },
    }
