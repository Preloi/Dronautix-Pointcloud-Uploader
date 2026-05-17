"""Projects view boundary."""

__all__ = ["show_projects_view", "open_project_download_dialog"]


def __getattr__(name):
    if name in __all__:
        from .. import main
        return getattr(main, name)
    raise AttributeError(name)
