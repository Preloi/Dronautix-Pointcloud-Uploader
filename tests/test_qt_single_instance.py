from dronautix_uploader.qt_app import single_instance
from dronautix_uploader.qt_app import app as qt_app


def test_first_windows_instance_holds_mutex_until_release(monkeypatch):
    closed = []
    monkeypatch.setattr(single_instance.os, "name", "nt")
    monkeypatch.setattr(
        single_instance,
        "_create_named_mutex",
        lambda name: (123, 0, closed.append),
    )

    guard = single_instance.SingleInstanceGuard()

    assert guard.acquire() is True
    assert closed == []
    guard.release()
    assert closed == [123]


def test_second_windows_instance_closes_duplicate_handle_and_stops(monkeypatch):
    closed = []
    monkeypatch.setattr(single_instance.os, "name", "nt")
    monkeypatch.setattr(
        single_instance,
        "_create_named_mutex",
        lambda name: (456, single_instance.ERROR_ALREADY_EXISTS, closed.append),
    )

    guard = single_instance.SingleInstanceGuard()

    assert guard.acquire() is False
    assert closed == [456]
    guard.release()
    assert closed == [456]


def test_non_windows_runtime_needs_no_mutex(monkeypatch):
    monkeypatch.setattr(single_instance.os, "name", "posix")
    monkeypatch.setattr(
        single_instance,
        "_create_named_mutex",
        lambda name: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    guard = single_instance.SingleInstanceGuard()

    assert guard.acquire() is True
    guard.release()


def test_second_instance_exits_before_qt_start_and_shows_message(monkeypatch):
    messages = []

    class DuplicateGuard:
        def acquire(self):
            return False

        def release(self):
            raise AssertionError("duplicate guard owns no handle")

    monkeypatch.setattr(single_instance, "SingleInstanceGuard", DuplicateGuard)
    monkeypatch.setattr(single_instance, "show_single_instance_message", lambda title, message: messages.append(message))
    monkeypatch.setattr(
        qt_app,
        "_run_qt_application",
        lambda *args: (_ for _ in ()).throw(AssertionError("Qt must not start")),
    )

    assert qt_app.run(argv=["app.exe"], mode="final") == 0
    assert "läuft bereits" in messages[0]


def test_first_instance_releases_mutex_after_qt_exit(monkeypatch):
    released = []

    class FirstGuard:
        def acquire(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(single_instance, "SingleInstanceGuard", FirstGuard)
    monkeypatch.setattr(qt_app, "_run_qt_application", lambda *args: 17)

    assert qt_app.run(argv=["app.exe"], mode="final") == 17
    assert released == [True]
