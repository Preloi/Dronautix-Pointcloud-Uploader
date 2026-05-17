"""UI helper boundary for the uploader package."""

__all__ = [
    "log",
    "ui_log",
    "ui_set_step",
    "ui_set_progress",
    "ui_set_detail",
    "show_main_view",
    "widget_exists",
    "clear_frame",
    "UploadProgress",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
