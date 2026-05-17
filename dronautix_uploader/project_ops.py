"""Project operation boundary for the uploader package."""

__all__ = [
    "run_process",
    "run_multi_upload_process",
    "replace_project_process",
    "replace_project_with_multi_pointclouds",
    "duplicate_project_process",
    "download_project_data_process",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
