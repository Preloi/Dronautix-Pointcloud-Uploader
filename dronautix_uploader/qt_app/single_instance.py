"""Native Windows single-instance guard without lock files or dependencies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable


MUTEX_NAME = r"Local\DronautixPointcloudUploader"
ERROR_ALREADY_EXISTS = 183


def _create_named_mutex(name: str):
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_bool

    handle = create_mutex(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle, ctypes.get_last_error(), close_handle


@dataclass
class SingleInstanceGuard:
    handle: object | None = None
    close_handle: Callable[[object], object] | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        handle, error_code, close_handle = _create_named_mutex(MUTEX_NAME)
        if error_code == ERROR_ALREADY_EXISTS:
            close_handle(handle)
            return False
        self.handle = handle
        self.close_handle = close_handle
        return True

    def release(self) -> None:
        if self.handle is not None and self.close_handle is not None:
            self.close_handle(self.handle)
        self.handle = None
        self.close_handle = None


def show_single_instance_message(title: str, message: str) -> None:
    if os.name != "nt":
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, title, 0x00000040 | 0x00010000)


__all__ = ["MUTEX_NAME", "SingleInstanceGuard", "show_single_instance_message"]
