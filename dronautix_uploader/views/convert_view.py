"""Local conversion view boundary."""

__all__ = ["show_local_conversion_view", "run_local_conversion"]


def __getattr__(name):
    if name in __all__:
        from .. import main
        return getattr(main, name)
    raise AttributeError(name)
