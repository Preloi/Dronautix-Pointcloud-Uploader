"""Keep unrelated workstation DLLs out of Windows builds and test the EXE."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def build_environment():
    env = os.environ.copy()
    if sys.platform == "win32":
        windows = Path(os.environ["SystemRoot"])
        # PyInstaller searches PATH for DLLs. Poppler's ICU is not Windows ICU.
        env["PATH"] = os.pathsep.join(map(str, (
            Path(sys.executable).parent, Path(sys.base_prefix),
            windows / "System32", windows,
        )))
    return env


def verify_frozen_startup(executable):
    with tempfile.TemporaryDirectory(prefix="dronautix_startup_check_") as directory:
        report = Path(directory) / "startup.json"
        env = build_environment()
        if sys.platform == "win32":
            env["QT_QPA_PLATFORM"] = "windows"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        with subprocess.Popen(
            [str(Path(executable).resolve()), "--startup-self-test", str(report)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options,
        ) as process:
            try:
                process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        [str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                         "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
                    )
                process.kill()
                process.communicate()
                raise RuntimeError("EXE startup self-test timed out") from None
        result = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
        if process.returncode != 0 or result.get("ok") is not True or result.get("frozen") is not True:
            raise RuntimeError(f"EXE startup self-test failed: {result}")
        print("[OK] Frozen EXE startup self-test:", json.dumps(result, ensure_ascii=True))
        return result
