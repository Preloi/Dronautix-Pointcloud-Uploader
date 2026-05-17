"""Configuration boundary for the uploader package."""

__all__ = [
    "APPDATA_DIR",
    "CONFIG_FILE",
    "CSV_FILE",
    "KEYRING_SERVICE",
    "load_config",
    "save_config",
    "get_aws_credentials",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
