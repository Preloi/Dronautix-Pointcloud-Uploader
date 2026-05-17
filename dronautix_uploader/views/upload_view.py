"""Upload view boundary."""

__all__ = ["select_file", "drop_file", "start_thread", "get_upload_sources_from_ui"]


def __getattr__(name):
    if name in __all__:
        from .. import main
        return getattr(main, name)
    raise AttributeError(name)
