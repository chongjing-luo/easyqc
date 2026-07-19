from types import SimpleNamespace

from gui import app as app_module


class FakeRoot:
    def __init__(self) -> None:
        self.idle_callbacks = []

    def after_idle(self, callback):
        self.idle_callbacks.append(callback)
        return f"idle-{len(self.idle_callbacks)}"


def test_tk_startup_warning_is_noop_when_logging_is_healthy(monkeypatch) -> None:
    root = FakeRoot()
    shown = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showwarning",
        lambda *args, **kwargs: shown.append((args, kwargs)),
    )

    app_module.schedule_tk_startup_warning(root, None)
    app_module.schedule_tk_startup_warning(root, "")

    assert root.idle_callbacks == []
    assert shown == []


def test_tk_startup_warning_schedules_exactly_one_toolkit_dialog(monkeypatch) -> None:
    root = FakeRoot()
    shown = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showwarning",
        lambda title, message, *, parent: shown.append((title, message, parent)),
    )

    app_module.schedule_tk_startup_warning(root, "file logging unavailable")

    assert len(root.idle_callbacks) == 1
    assert shown == []
    root.idle_callbacks[0]()
    assert shown == [
        ("日志记录受限", "file logging unavailable", root),
    ]


def test_default_tk_app_consumes_current_logging_status_once(monkeypatch) -> None:
    root = FakeRoot()
    expected_services = SimpleNamespace(
        project_service=object(),
        rating_service=object(),
        table_service=object(),
        code_executor=object(),
        table_transform=object(),
    )
    main_window = object()
    scheduled = []

    def build_legacy_window(actual_root, services=None):
        assert actual_root is root
        assert services is expected_services
        return main_window

    monkeypatch.setattr(app_module.tk, "Tk", lambda: root)
    monkeypatch.setattr(
        app_module,
        "build_app_services",
        lambda _path: expected_services,
    )
    monkeypatch.setattr(app_module, "LegacyEasyQCApp", build_legacy_window)
    monkeypatch.setattr(
        app_module,
        "get_logging_status",
        lambda: SimpleNamespace(warning_message="degraded"),
    )
    monkeypatch.setattr(
        app_module,
        "schedule_tk_startup_warning",
        lambda actual_root, message: scheduled.append((actual_root, message)),
    )

    application = app_module.EasyQCApp()

    assert application.root is root
    assert application.main_window is main_window
    assert scheduled == [(root, "degraded")]
