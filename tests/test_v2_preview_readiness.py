import importlib

from tools.check_v2_preview_ready import (
    PreviewGate,
    _check_build_dependencies,
    _check_preview_build_isolation,
    _check_preview_imports,
    _check_required_files,
    build_preview_readiness_report,
    main as check_preview_ready,
)


def test_preview_readiness_tool_is_import_safe():
    module = importlib.import_module("tools.check_v2_preview_ready")

    assert hasattr(module, "build_preview_readiness_report")


def test_preview_readiness_passes_local_source_gates_with_dependency_warning():
    report = build_preview_readiness_report()

    gate_labels = {gate.label for gate in report.gates}
    assert "Preview-Dateien" in gate_labels
    assert "Import-Sicherheit" in gate_labels
    assert "Preview-Build-Isolation" in gate_labels
    assert "V2-Ausgaben" in gate_labels
    assert "V2-Ausgaben aktuell" in gate_labels
    assert "Build-Abhaengigkeiten" in gate_labels
    assert all(not gate.blocked for gate in report.gates)


def test_preview_readiness_cli_returns_zero_when_only_optional_build_deps_are_missing(capsys):
    exit_code = check_preview_ready([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "V2 preview gate: OK" in captured.out


def test_preview_readiness_can_require_build_dependencies(monkeypatch):
    monkeypatch.setattr(
        "tools.check_v2_preview_ready.importlib.util.find_spec",
        lambda module_name: None,
    )

    gate = _check_build_dependencies(require_build_dependencies=True)

    assert gate.blocked
    assert "pyinstaller" in gate.detail
    assert "PySide6" in gate.detail


def test_preview_readiness_reports_build_dependency_warning_by_default(monkeypatch):
    monkeypatch.setattr(
        "tools.check_v2_preview_ready.importlib.util.find_spec",
        lambda module_name: None,
    )

    gate = _check_build_dependencies(require_build_dependencies=False)

    assert gate.warning
    assert not gate.blocked


def test_preview_readiness_blocks_missing_required_files(tmp_path):
    present = tmp_path / "present.txt"
    missing = tmp_path / "missing.txt"
    present.write_text("", encoding="utf-8")

    gate = _check_required_files((present, missing))

    assert gate.blocked
    assert "missing.txt" in gate.detail


def test_preview_readiness_blocks_failed_preview_import():
    gate = _check_preview_imports(("definitely_missing_preview_module",))

    assert gate.blocked
    assert "definitely_missing_preview_module" in gate.detail


def test_preview_build_isolation_gate_matches_preview_contract():
    gate = _check_preview_build_isolation()

    assert gate == PreviewGate(
        "Preview-Build-Isolation",
        "ok",
        "Separater Entrypoint und isolierte Preview-Build-Ordner.",
    )


def test_preview_cli_can_fail_when_build_dependencies_are_required(monkeypatch, capsys):
    monkeypatch.setattr(
        "tools.check_v2_preview_ready.importlib.util.find_spec",
        lambda module_name: None,
    )

    exit_code = check_preview_ready(["--require-build-deps"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[BLOCKED] Build-Abhaengigkeiten" in captured.out
