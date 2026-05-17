"""Update workflow boundary for the uploader package."""

__all__ = [
    "check_for_available_update",
    "load_update_manifest",
    "download_update_installer",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
