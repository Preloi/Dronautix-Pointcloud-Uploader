"""CRS detection and normalization boundary for the uploader package."""

__all__ = [
    "detect_pointcloud_crs",
    "normalize_crs_value",
    "normalize_vertical_crs_value",
    "read_las_projection_records",
    "extract_epsg_from_wkt",
    "extract_vertical_epsg_from_wkt",
    "extract_name_from_wkt",
    "extract_vertical_name_from_wkt",
    "resolve_pointcloud_crs",
    "write_potree_metadata_crs",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
