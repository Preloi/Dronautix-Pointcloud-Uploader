"""Naming and path helpers shared by upload and replace workflows."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

from .constants import DOMAIN_URL


def sanitize_folder_name(name: str) -> str:
    value = (name or "").strip().lower()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9_]", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def get_pointcloud_display_name(source_path: str) -> str:
    filename = os.path.basename(source_path or "").strip()
    lower_name = filename.lower()
    if lower_name.endswith(".copc.laz"):
        return filename[:-9] or "Punktwolke"
    name, _extension = os.path.splitext(filename)
    return name or "Punktwolke"


def make_unique_slug(name: str, used_slugs: set[str]) -> str:
    base_slug = sanitize_folder_name(name) or "punktwolke"
    slug = base_slug
    counter = 2
    while slug in used_slugs:
        slug = f"{base_slug}_{counter}"
        counter += 1
    used_slugs.add(slug)
    return slug


def make_unique_cloud_slug(source_path: str, used_slugs: set[str]) -> str:
    return make_unique_slug(get_pointcloud_display_name(source_path), used_slugs)


@dataclass(frozen=True)
class ProjectPaths:
    folder_kunde: str
    folder_project: str
    project_viewer_root: str
    s3_prefix: str
    project_url: str


def build_project_paths(kunde: str, projekt: str, project_id: str) -> ProjectPaths:
    folder_kunde = sanitize_folder_name(kunde)
    folder_project = sanitize_folder_name(projekt)
    project_viewer_root = f"{folder_kunde}/{project_id}/{folder_project}"
    return ProjectPaths(
        folder_kunde=folder_kunde,
        folder_project=folder_project,
        project_viewer_root=project_viewer_root,
        s3_prefix=f"pointclouds/{project_viewer_root}",
        project_url=f"{DOMAIN_URL}?id={project_id}",
    )


__all__ = [
    "ProjectPaths",
    "build_project_paths",
    "get_pointcloud_display_name",
    "make_unique_cloud_slug",
    "make_unique_slug",
    "sanitize_folder_name",
]
