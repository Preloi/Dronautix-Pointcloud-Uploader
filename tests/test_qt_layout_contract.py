"""Layout contract of the real main window (safety net for the UI split).

The window is measured in a fresh interpreter (tests/_layout_probe.py) with
real Windows fonts at 1024, 1100 and 1400 px width.
"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("PySide6.QtWidgets")

PROBE = Path(__file__).resolve().parent / "_layout_probe.py"


@pytest.fixture(scope="module")
def layout():
    result = subprocess.run([sys.executable, str(PROBE)], capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])["pages"]


def test_every_page_renders_at_every_size(layout):
    expected = {f"{page}@{width}" for page in ("Upload", "Projektverwaltung", "Einstellungen") for width in (1024, 1100, 1400)}
    assert expected <= set(layout)


def test_no_button_text_is_clipped_at_any_size(layout):
    clipped = {page: entry["clipped_buttons"] for page, entry in layout.items() if entry["clipped_buttons"]}
    assert clipped == {}


def test_project_list_keeps_status_and_date_visible_without_scrolling(layout):
    for width in (1024, 1100, 1400):
        entry = layout[f"Projektverwaltung@{width}"]
        assert entry["rows"] == 4
        for column in ("Kunde", "Projekt", "Status", "Erstellt am"):
            assert entry["visible_columns"][column], f"{column} bei {width} px nicht sichtbar"


def test_wide_window_shows_all_project_columns(layout):
    assert all(layout["Projektverwaltung@1400"]["visible_columns"].values())
