"""Compatibility entry point; starts the V2 app (the V1 CustomTkinter app was removed)."""

from dronautix_uploader.qt_app.app import run


if __name__ == "__main__":
    raise SystemExit(run(mode="final"))
