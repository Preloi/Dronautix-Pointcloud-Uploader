"""Settings view boundary."""

__all__ = ["show_settings_view"]


def __getattr__(name):
    if name in __all__:
        from .. import main
        return getattr(main, name)
    raise AttributeError(name)
