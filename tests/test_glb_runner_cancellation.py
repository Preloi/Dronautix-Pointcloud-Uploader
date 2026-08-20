from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import time

import pytest

from dronautix_uploader.core.contracts import OperationCancelledError
from dronautix_uploader.core import glb_optimization_service as service_module


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_bundled_runner_cancellation_terminates_spawned_codec_process(tmp_path, monkeypatch):
    runner = tmp_path / "runners" / "spawn_codec.py"
    runner.parent.mkdir()
    child_pid_path = tmp_path / "child.pid"
    runner.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(service_module, "get_bundled_tool_path", lambda *_args: Path(sys.executable))
    monkeypatch.setattr(service_module, "get_bundled_runner_path", lambda *_args: runner)
    monkeypatch.setattr(service_module, "get_bundled_toolchain_environment", lambda *_args: os.environ.copy())

    with pytest.raises(OperationCancelledError):
        service_module._run_bundled_runner(
            None,
            "optimizer",
            (str(child_pid_path),),
            lambda: child_pid_path.is_file(),
        )

    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while _pid_is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_is_running(child_pid)
