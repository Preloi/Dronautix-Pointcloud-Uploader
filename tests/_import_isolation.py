"""Check a module's import footprint in a fresh interpreter.

Asserting ``"PySide6" not in sys.modules`` inside the test process depends on
which test files ran before; a subprocess sees only the module under test.
"""

import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_TOOLKITS = ("PySide6", "tkinter", "customtkinter")


def loaded_forbidden_modules(module: str, forbidden=UI_TOOLKITS) -> list[str]:
    script = (
        "import importlib, json, sys\n"
        f"importlib.import_module({module!r})\n"
        f"print(json.dumps([name for name in {list(forbidden)!r} if name in sys.modules]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])
