"""General utility boundary for the uploader package."""

__all__ = [
    "sanitize_folder_name",
    "format_bytes",
    "parse_iso_datetime",
    "normalize_upload_sources",
    "validate_file",
    "validate_replacement_file",
    "detect_input_format",
    "get_total_size",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
