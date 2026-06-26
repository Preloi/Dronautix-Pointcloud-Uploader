import importlib
import sys


def test_path_drop_imports_without_pyside6():
    before = {name for name in sys.modules if name == "PySide6" or name.startswith("PySide6.")}
    sys.modules.pop("dronautix_uploader.qt_app.path_drop", None)

    importlib.import_module("dronautix_uploader.qt_app.path_drop")

    after = {name for name in sys.modules if name == "PySide6" or name.startswith("PySide6.")}
    assert after == before


def test_mime_data_paths_returns_local_file_and_folder_paths():
    from dronautix_uploader.qt_app.path_drop import mime_data_paths

    mime_data = _FakeMimeData(
        (
            _FakeUrl("C:/Daten/scan.laz"),
            _FakeUrl("C:/Daten/Potree Ordner"),
            _FakeUrl("https://example.test/scan.laz", local=False),
            _FakeUrl(""),
        )
    )

    assert mime_data_paths(mime_data) == ("C:/Daten/scan.laz", "C:/Daten/Potree Ordner")


def test_mime_data_paths_ignores_non_url_mime_data():
    from dronautix_uploader.qt_app.path_drop import mime_data_paths

    assert mime_data_paths(None) == ()
    assert mime_data_paths(_FakeMimeData((), has_urls=False)) == ()


def test_append_unique_paths_merges_trimmed_paths_without_duplicates():
    from dronautix_uploader.qt_app.path_drop import append_unique_paths

    assert append_unique_paths((" C:/Daten/a.laz ",), ("C:/Daten/a.laz", " C:/Daten/b.laz ")) == (
        "C:/Daten/a.laz",
        "C:/Daten/b.laz",
    )


def test_append_unique_paths_replaces_existing_when_multiple_sources_are_not_allowed():
    from dronautix_uploader.qt_app.path_drop import append_unique_paths

    assert append_unique_paths(("C:/Daten/alt.laz",), (" C:/Daten/neu.laz ", "C:/Daten/zweite.laz"), allow_multiple=False) == (
        "C:/Daten/neu.laz",
    )


class _FakeMimeData:
    def __init__(self, urls, *, has_urls=True):
        self._urls = urls
        self._has_urls = has_urls

    def hasUrls(self):  # noqa: N802 - Qt-like fake
        return self._has_urls

    def urls(self):
        return self._urls


class _FakeUrl:
    def __init__(self, path, *, local=True):
        self._path = path
        self._local = local

    def isLocalFile(self):  # noqa: N802 - Qt-like fake
        return self._local

    def toLocalFile(self):  # noqa: N802 - Qt-like fake
        return self._path
