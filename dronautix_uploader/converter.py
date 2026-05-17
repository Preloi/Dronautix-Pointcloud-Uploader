"""Potree converter boundary for the uploader package."""

__all__ = [
    "run_potree_conversion",
    "resolve_converter_path",
    "is_converter_bundle_available",
    "get_bundled_converter_path",
    "get_bundled_converter_dir",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
