"""Runtime identity for the QtWidgets app.

Preview must stay isolated until the cutover gates are proven. Final mode is
explicit so the same UI bootstrap can later use the production config and
installer identity without rewriting the application start path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app_version import APP_VERSION


APP_MODE_PREVIEW = "preview"
APP_MODE_FINAL = "final"
AppMode = Literal["preview", "final"]


@dataclass(frozen=True)
class QtAppIdentity:
    mode: AppMode
    application_name: str
    window_title: str
    sidebar_badge: str
    uses_preview_config: bool
    default_runtime_status: str

    @property
    def preview(self) -> bool:
        return self.uses_preview_config


PREVIEW_IDENTITY = QtAppIdentity(
    mode=APP_MODE_PREVIEW,
    application_name="Dronautix Pointcloud Uploader Preview",
    window_title="Dronautix Pointcloud Uploader V2 Preview",
    sidebar_badge="QtWidgets Preview",
    uses_preview_config=True,
    default_runtime_status="Qt Preview - keine Service-Integration aktiv",
)

FINAL_IDENTITY = QtAppIdentity(
    mode=APP_MODE_FINAL,
    application_name="Dronautix Pointcloud Uploader",
    window_title="Dronautix Pointcloud Uploader",
    sidebar_badge=f"Version {APP_VERSION}",
    uses_preview_config=False,
    default_runtime_status="Nicht verbunden - AWS-Zugangsdaten in den Einstellungen hinterlegen",
)


def resolve_app_identity(mode: str | QtAppIdentity | None = None) -> QtAppIdentity:
    if isinstance(mode, QtAppIdentity):
        return mode
    normalized = str(mode or APP_MODE_PREVIEW).strip().lower().replace("_", "-")
    if normalized in {"preview", "v2-preview", "qt-preview"}:
        return PREVIEW_IDENTITY
    if normalized in {"final", "production", "stable"}:
        return FINAL_IDENTITY
    raise ValueError(f"Unbekannter Qt-App-Modus: {mode!r}")


__all__ = [
    "APP_MODE_FINAL",
    "APP_MODE_PREVIEW",
    "FINAL_IDENTITY",
    "PREVIEW_IDENTITY",
    "QtAppIdentity",
    "resolve_app_identity",
]
