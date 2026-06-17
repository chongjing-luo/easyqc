import inspect
from pathlib import Path
from types import SimpleNamespace

import easyqc as entrypoint
import pandas as pd
from gui import dialog_main, dialogs, gui_qcpage, gui_table, main_window, qc_page, table_view, widgets
from gui import state_bridge
from gui.state_bridge import GUIStateBridge
from gui import app as gui_app
from gui.qc_page import QCPageController, QCPageRuntimeContext


def test_new_gui_modules_do_not_call_tk_root_directly() -> None:
    for module in [widgets, table_view, dialogs, qc_page, state_bridge]:
        source = inspect.getsource(module)
        assert "tk.Tk(" not in source


def test_context_menu_helper_binds_platform_specific_events() -> None:
    class FakeWidget:
        def __init__(self):
            self.events = []

        def bind(self, event, callback):
            self.events.append((event, callback))

    callback = object()
    linux_widget = FakeWidget()
    mac_widget = FakeWidget()

    assert widgets.bind_context_menu(linux_widget, callback, system_name="Linux") == ["<Button-3>"]
    assert widgets.bind_context_menu(mac_widget, callback, system_name="Darwin") == ["<Button-2>", "<Control-Button-1>"]
    assert linux_widget.events == [("<Button-3>", callback)]
    assert mac_widget.events == [("<Button-2>", callback), ("<Control-Button-1>", callback)]


def test_legacy_gui_no_longer_uses_os_system() -> None:
    assert "os.system(" not in inspect.getsource(gui_table)


def test_legacy_gui_tk_root_calls_are_removed_from_dialog_table_qcpage() -> None:
    for module in [dialog_main, gui_table, gui_qcpage, main_window]:
        assert "tk.Tk(" not in inspect.getsource(module)


def test_default_entrypoint_delegates_root_creation_to_gui_app() -> None:
    source = inspect.getsource(entrypoint.main)
    assert "from gui.app import EasyQCApp" in source
    assert "tk.Tk(" not in source
    assert "root.mainloop()" not in source
    assert "app.run()" in source


def test_cli_entrypoint_uses_service_context_for_qcpage_launch() -> None:
    source = inspect.getsource(entrypoint.open_qcpage_from_shell)
    assert "resolve_qcpage_launch(" in source
    assert "QCPageLaunchError" in source
    # P2-CLI: no more gui_state.update_module_field (uses ProjectService directly)
    assert "gui_state" not in source
    assert "project_service" in source
    assert "module_found" not in source
    assert "for idx, mod" not in source


def test_cli_entrypoint_hides_root_window_for_qcpage_launch() -> None:
    source = inspect.getsource(entrypoint.open_qcpage_from_shell)
    assert "cli_root = tk.Tk()" in source
    assert "cli_root.withdraw()" in source
    assert "cli_root.mainloop()" in source
    assert "qcpage_instance.gui_qcpage.mainloop()" not in source
    assert 'protocol("WM_DELETE_WINDOW", close_cli_qcpage)' in source


def test_cli_entrypoint_initializes_qcpage_runtime_context_for_shell_launch() -> None:
    source = inspect.getsource(entrypoint.open_qcpage_from_shell)

    # P2-CLI: CLI path now uses ProjectService (no ProjectManager / adapter)
    assert "QCPageRuntimeContext" in source
    assert "ProjectService" in source
    assert "LegacyGUIStateAdapter" not in source
    assert "ProjectManager" not in source
    assert "QCPageRuntimeContext.from_project_service(" in source
    assert "qcpage_instance.runtime_context.set_module_rater_dir(" in source
    assert "pm.dt.settings" not in source
    assert "QCPageRuntimeContext.from_legacy_dt(pm.dt)" not in source
    assert "qcpage_instance.dt.dir_module_rater" not in source


def test_gui_app_owns_root_creation_and_shutdown_protocol() -> None:
    init_source = inspect.getsource(gui_app.EasyQCApp.__init__)
    run_source = inspect.getsource(gui_app.EasyQCApp.run)
    assert "tk.Tk(" in init_source
    assert 'self.root.protocol("WM_DELETE_WINDOW", self.main_window.quit_app)' in run_source
    assert "self.root.mainloop()" in run_source


def test_gui_app_builds_services_context_for_legacy_main_window() -> None:
    init_source = inspect.getsource(gui_app.EasyQCApp.__init__)
    assert "AppServices(" in init_source
    assert "self.services = AppServices(" in init_source
    assert "LegacyEasyQCApp(self.root, services=self.services)" in init_source


def test_legacy_main_window_accepts_services_context_without_requiring_it() -> None:
    init_source = inspect.getsource(main_window.EasyQCApp.__init__)
    assert "def __init__(self, root, services=None)" in init_source
    assert "self.services = services" in init_source
    assert "self.project_service = getattr(services, \"project_service\", None)" in init_source
    assert "self.dt = self.ProjM.dt" not in init_source
    # P2 step 3: main window now uses GUIStateBridge (service-backed, no ProjectManager)
    assert "GUIStateBridge(" in init_source
    assert "LegacyGUIStateAdapter(self.ProjM)" not in init_source


def test_main_window_project_loading_uses_gui_state_adapter() -> None:
    load_project_source = inspect.getsource(main_window.EasyQCApp.load_project_to_gui)
    load_module_source = inspect.getsource(main_window.EasyQCApp.load_module_to_gui)
    project_sync_source = inspect.getsource(main_window.EasyQCApp._sync_project_service_from_legacy_state)
    sync_source = inspect.getsource(main_window.EasyQCApp._sync_legacy_tables_from_service)
    assert "self._sync_project_service_from_legacy_state()" in load_project_source
    assert "self._sync_legacy_tables_from_service()" in load_project_source
    assert "self.project_service.reload_registry()" in project_sync_source
    assert "self.project_service.load(project_name)" in project_sync_source
    assert "self.gui_state.current_project_model()" in sync_source
    assert "self.table_service.load_legacy_state_tables(" in sync_source
    assert "self.gui_state.apply_loaded_tables(" in sync_source
    assert "self.gui_state.project_names()" in load_project_source
    assert "self.gui_state.current_project_name()" in load_project_source
    assert "self.gui_state.module_keys()" in load_module_source
    assert "self.dt.settings['qcmodule']" not in load_module_source


def test_main_window_syncs_legacy_tables_from_table_service_bridge() -> None:
    class FakeTableService:
        def __init__(self) -> None:
            self.calls = []

        def load_legacy_state_tables(self, project, module_names):
            self.calls.append((project, list(module_names)))
            return "loaded-tables"

    class FakeGuiState:
        def __init__(self) -> None:
            self.project = SimpleNamespace(name="SAMPLE", path="/tmp/easyqc_SAMPLE")
            self.applied = []

        def current_project_model(self):
            return self.project

        def module_names(self):
            return ["AnatRestAll", "hcpall"]

        def apply_loaded_tables(self, loaded_tables):
            self.applied.append(loaded_tables)

    app = object.__new__(main_window.EasyQCApp)
    app.table_service = FakeTableService()
    app.gui_state = FakeGuiState()

    app._sync_legacy_tables_from_service()

    assert app.table_service.calls == [(app.gui_state.project, ["AnatRestAll", "hcpall"])]
    assert app.gui_state.applied == ["loaded-tables"]


def test_main_window_table_service_sync_is_noop_without_service_or_project() -> None:
    class FakeGuiState:
        def __init__(self, project) -> None:
            self.project = project
            self.applied = []

        def current_project_model(self):
            return self.project

        def module_names(self):
            return ["AnatRestAll"]

        def apply_loaded_tables(self, loaded_tables):
            self.applied.append(loaded_tables)

    app = object.__new__(main_window.EasyQCApp)
    app.table_service = None
    app.gui_state = FakeGuiState(SimpleNamespace(name="SAMPLE", path="/tmp/easyqc_SAMPLE"))

    app._sync_legacy_tables_from_service()

    assert app.gui_state.applied == []

    app.table_service = SimpleNamespace(load_legacy_state_tables=lambda *args, **kwargs: "loaded")
    app.gui_state = FakeGuiState(None)

    app._sync_legacy_tables_from_service()

    assert app.gui_state.applied == []


def test_main_window_syncs_project_service_from_legacy_state() -> None:
    class FakeProjectService:
        def __init__(self) -> None:
            self.reloaded = 0
            self.loaded = []

        def reload_registry(self):
            self.reloaded += 1

        def load(self, project_name):
            self.loaded.append(project_name)

    class FakeGuiState:
        def current_project_name(self):
            return "SAMPLE"

    app = object.__new__(main_window.EasyQCApp)
    app.project_service = FakeProjectService()
    app.gui_state = FakeGuiState()

    assert app._sync_project_service_from_legacy_state()
    assert app.project_service.reloaded == 1
    assert app.project_service.loaded == ["SAMPLE"]


def test_main_window_project_service_sync_is_noop_or_false_on_missing_project() -> None:
    class FakeProjectService:
        def __init__(self) -> None:
            self.reloaded = 0

        def reload_registry(self):
            self.reloaded += 1

        def load(self, project_name):
            raise AssertionError("load should not be called without a project name")

    class FakeGuiState:
        def current_project_name(self):
            return None

    app = object.__new__(main_window.EasyQCApp)
    app.project_service = FakeProjectService()
    app.gui_state = FakeGuiState()

    assert not app._sync_project_service_from_legacy_state()
    assert app.project_service.reloaded == 0

    app.project_service = None

    assert not app._sync_project_service_from_legacy_state()


def test_main_window_project_service_sync_returns_false_on_load_failure() -> None:
    class FakeProjectService:
        def reload_registry(self):
            pass

        def load(self, project_name):
            raise KeyError(project_name)

    class FakeGuiState:
        def current_project_name(self):
            return "MISSING"

    app = object.__new__(main_window.EasyQCApp)
    app.project_service = FakeProjectService()
    app.gui_state = FakeGuiState()

    assert not app._sync_project_service_from_legacy_state()


def test_main_window_extract_qc_results_prefers_service_bridge() -> None:
    class FakeRatingService:
        calls = []

        def __init__(self, project) -> None:
            self.project = project

        def load_legacy_state(self, subjects):
            self.__class__.calls.append((self.project, subjects.copy()))
            return SimpleNamespace(
                rating_dict={"SUB001": {"example-rater1": {"name": "example", "rater": "rater1"}}},
                qctable=pd.DataFrame({"ezqcid": ["SUB001"], "example.rater1.score1": ["Good"]}),
                original_table=pd.DataFrame({"ezqcid": ["SUB001"], "module_name": ["example"]}),
                original_wide_table=pd.DataFrame({"ezqcid": ["SUB001"], "example.rater1.score1": ["Good"]}),
            )

    class FakeTableService:
        def __init__(self) -> None:
            self.saved = []

        def save_table(self, project, table_type, df):
            self.saved.append((project, table_type, df.copy()))

    class FakeGuiState:
        def __init__(self) -> None:
            self.project = SimpleNamespace(name="SAMPLE", path="/tmp/easyqc_SAMPLE")
            self.subjects = pd.DataFrame({"ezqcid": ["SUB001"]})
            self.applied = []
            self.legacy_loads = 0

        def current_project_model(self):
            return self.project

        def all_variable_table(self):
            return self.subjects.copy()

        def apply_loaded_ratings(self, loaded_ratings):
            self.applied.append(loaded_ratings)

        def load_ratings(self):
            self.legacy_loads += 1

    app = object.__new__(main_window.EasyQCApp)
    app.rating_service = FakeRatingService(None)
    app.table_service = FakeTableService()
    app.gui_state = FakeGuiState()

    app.extract_qc_results()

    assert FakeRatingService.calls[0][0] is app.gui_state.project
    assert FakeRatingService.calls[0][1].equals(pd.DataFrame({"ezqcid": ["SUB001"]}))
    assert app.gui_state.applied[0].qctable["example.rater1.score1"].tolist() == ["Good"]
    assert app.gui_state.legacy_loads == 0
    assert [save[1] for save in app.table_service.saved] == [
        "ezqc_qctable",
    ]
    assert app.table_service.saved[0][2]["ezqcid"].tolist() == ["SUB001"]


def test_main_window_extract_qc_results_reuses_matching_rating_service() -> None:
    class FakeRatingService:
        created = 0

        def __init__(self, project) -> None:
            self.project = project
            self.loaded = []
            self.__class__.created += 1

        def load_legacy_state(self, subjects):
            self.loaded.append(subjects.copy())
            return SimpleNamespace(
                rating_dict={},
                qctable=pd.DataFrame({"ezqcid": ["SUB001"]}),
                original_table=pd.DataFrame(),
                original_wide_table=pd.DataFrame(),
            )

    class FakeTableService:
        def __init__(self) -> None:
            self.saved = []

        def save_table(self, project, table_type, df):
            self.saved.append((project, table_type, df.copy()))

    class FakeGuiState:
        def __init__(self, project) -> None:
            self.project = project
            self.applied = []

        def current_project_model(self):
            return self.project

        def all_variable_table(self):
            return pd.DataFrame({"ezqcid": ["SUB001"]})

        def apply_loaded_ratings(self, loaded_ratings):
            self.applied.append(loaded_ratings)

    project = SimpleNamespace(name="SAMPLE", path="/tmp/easyqc_SAMPLE")
    rating_service = FakeRatingService(project)
    app = object.__new__(main_window.EasyQCApp)
    app.rating_service = rating_service
    app.table_service = FakeTableService()
    app.gui_state = FakeGuiState(project)

    app.extract_qc_results()

    assert FakeRatingService.created == 1
    assert len(rating_service.loaded) == 1
    assert app.gui_state.applied[0].qctable["ezqcid"].tolist() == ["SUB001"]
    assert [save[1] for save in app.table_service.saved] == ["ezqc_qctable"]


def test_main_window_extract_qc_results_falls_back_to_legacy_loader() -> None:
    class FakeGuiState:
        def __init__(self) -> None:
            self.legacy_loads = 0

        def load_ratings(self):
            self.legacy_loads += 1

    app = object.__new__(main_window.EasyQCApp)
    app.rating_service = None
    app.table_service = None
    app.gui_state = FakeGuiState()

    app.extract_qc_results()

    assert app.gui_state.legacy_loads == 1


def test_main_window_module_card_updates_fields_through_gui_state_adapter() -> None:
    source = inspect.getsource(main_window.EasyQCApp.create_collapsible_card)
    assert "self.gui_state.module_by_key(qcidx)" in source
    assert "self.gui_state.update_module_field(" in source
    assert "self.gui_state.update_score_fields(" in source
    assert "self.gui_state.update_tag_fields(" in source
    assert "self.dt.settings['qcmodule']" not in source


def test_main_window_table_buttons_and_quit_use_gui_state_adapter() -> None:
    variable_source = inspect.getsource(main_window.EasyQCApp.variable_widget)
    set_variable_source = inspect.getsource(main_window.EasyQCApp.set_variable)
    extract_source = inspect.getsource(main_window.EasyQCApp.extract_qc_results)
    quit_source = inspect.getsource(main_window.EasyQCApp.quit_app)
    source = variable_source + set_variable_source + extract_source + quit_source

    assert "self.gui_state.all_variable_table()" in variable_source
    assert "self.gui_state.new_variable_table()" in set_variable_source
    assert "self.extract_qc_results" in variable_source
    assert "self.gui_state.load_ratings()" in extract_source
    assert "self.gui_state.save_project_state()" in quit_source
    assert "self.dt.var[" not in source
    assert "self.ProjM.load_ratings" not in source
    assert "self.ProjM.save_settings(" not in source
    assert "self.ProjM.save_table(" not in source


def test_main_window_project_combo_switch_uses_gui_state_adapter() -> None:
    source = inspect.getsource(main_window.EasyQCApp.project_manager_widget)

    assert "self.gui_state.change_project(" in source
    assert "self.ProjM.change_project(" not in source


def test_table_display_filter_state_uses_gui_state_adapter() -> None:
    assert "self.state_adapter().resolve_filter_source(" in inspect.getsource(gui_table.TableDisplay.resolve_filter_source)
    save_source = inspect.getsource(gui_table.TableDisplay.save_filter_result)
    assert "self.state_adapter().save_filter_result(" in save_source
    assert "self.dt.tab" not in save_source
    assert "self.state_adapter().restore_filter_source(" in inspect.getsource(gui_table.TableDisplay.restore_filter_source)


def test_table_display_init_prefers_existing_gui_state_adapter() -> None:
    source = inspect.getsource(gui_table.TableDisplay.__init__)

    assert "self.gui_state = getattr(app, 'gui_state', None)" in source
    assert "self.dt = self.app.ProjM.dt" not in source
    assert "self.ProjM = self.app.ProjM" not in source


def test_table_display_right_menu_reads_rating_state_through_adapter() -> None:
    source = inspect.getsource(gui_table.TableDisplay.show_right_menu)

    assert "self.state_adapter().has_rating_data()" in source
    assert "hasattr(self.dt, 'rating_dict')" not in source


def test_table_display_right_menu_has_cancel_action() -> None:
    source = inspect.getsource(gui_table.TableDisplay.show_right_menu)

    assert 'label="取消"' in source
    assert 'menu.bind("<Escape>", dismiss_menu)' in source
    assert "menu.unpost()" in source


def test_qc_page_controller_applies_rating_state_without_overwriting_config() -> None:
    current = {
        "label": "Current",
        "code": "current-code",
        "scores": {"1": {"label": "Quality", "value": None}},
        "tags": {"1": {"label": "Artifact", "value": False}},
    }
    rating = {
        "label": "Old",
        "code": "old-code",
        "ezqcid": "SUB001",
        "scores": {"1": {"value": "Good"}},
        "tags": {"1": {"value": True}},
        "notes": "note",
        "time": "2026-06-02 00:00:00",
        "code_exe": {"0": "cmd"},
    }

    result = QCPageController().apply_rating_state(current, rating)

    assert result["label"] == "Current"
    assert result["code"] == "current-code"
    assert result["scores"]["1"]["value"] == "Good"
    assert result["tags"]["1"]["value"] is True


def test_qc_page_controller_detects_rating_compatibility_issues() -> None:
    current = {
        "scores": {"1": {"num_": "Poor,Fair,Good"}},
        "tags": {"1": {"label": "Artifact"}},
    }
    rating = {
        "scores": {"1": {"num_": "1,2,3"}},
        "tags": {"1": {"label": "Motion"}},
    }

    result = QCPageController().find_rating_compatibility_issues(current, rating)

    assert result == [("score", "1"), ("tag", "1")]


def test_qc_page_controller_compatibility_no_crash_on_missing_rating_score() -> None:
    """BUG-2: a saved rating lacking a score key present in the current module
    must NOT raise KeyError — it must surface as an explicit issue."""
    current = {
        "scores": {"1": {"num_": "1,2,3"}, "2": {"num_": "A,B"}},  # current has 1 and 2
        "tags": {"1": {"label": "Artifact"}},
    }
    rating = {
        "scores": {"1": {"num_": "1,2,3"}},  # rating missing score "2"
        "tags": {"1": {"label": "Artifact"}},
    }

    result = QCPageController().find_rating_compatibility_issues(current, rating)

    assert ("score_missing", "2") in result


def test_qc_page_controller_compatibility_no_crash_on_missing_rating_tag() -> None:
    """BUG-2 (tag side): missing tag key in saved rating must not crash."""
    current = {
        "scores": {"1": {"num_": "1,2,3"}},
        "tags": {"1": {"label": "Artifact"}, "2": {"label": "Motion"}},  # current has 1 and 2
    }
    rating = {
        "scores": {"1": {"num_": "1,2,3"}},
        "tags": {"1": {"label": "Artifact"}},  # rating missing tag "2"
    }

    result = QCPageController().find_rating_compatibility_issues(current, rating)

    assert ("tag_missing", "2") in result


def test_qc_page_controller_compatibility_no_crash_on_empty_rating_schema() -> None:
    """BUG-2 (extreme): a legacy rating with no scores/tags at all must not crash."""
    current = {
        "scores": {"1": {"num_": "1,2,3"}},
        "tags": {"1": {"label": "Artifact"}},
    }
    rating: dict = {}

    result = QCPageController().find_rating_compatibility_issues(current, rating)

    assert ("score_missing", "1") in result
    assert ("tag_missing", "1") in result


def test_qc_page_controller_compatibility_no_crash_when_current_module_lacks_schema() -> None:
    """Defensive: current module missing scores/tags should not crash either."""
    result = QCPageController().find_rating_compatibility_issues({}, {"scores": {}, "tags": {}})

    assert result == []


def test_qc_page_controller_updates_runtime_rating_state() -> None:
    module = {
        "scores": {"1": {"value": "Old"}},
        "tags": {"1": {"value": True}},
        "notes": "old note",
        "code_exe": {"0": "old"},
        "ezqcid": "OLD",
        "time": "old time",
    }
    controller = QCPageController()

    controller.set_score_value(module, "1", "Good")
    controller.set_tag_value(module, "1", False)
    controller.set_notes(module, "new note")
    controller.set_code_execution(module, {0: "cmd"})
    controller.set_subject(module, "SUB002")

    assert module["scores"]["1"]["value"] == "Good"
    assert module["tags"]["1"]["value"] is False
    assert module["notes"] == "new note"
    assert module["code_exe"] == {0: "cmd"}
    assert module["ezqcid"] == "SUB002"

    controller.reset_rating_state(module, "SUB001")

    assert module["scores"]["1"]["value"] is None
    assert module["tags"]["1"]["value"] is False
    assert module["notes"] is None
    assert module["code_exe"] is None
    assert module["ezqcid"] == "SUB001"
    assert module["time"] is None


def test_qc_page_controller_shapes_current_module_metadata() -> None:
    module = {"name": "example", "rater": "alice"}
    settings = {"qcmodule": {"2": module}}
    controller = QCPageController()

    assert controller.module_index_by_name(settings, "example") == "2"
    assert controller.module_index_by_name(settings, "missing") is None
    assert controller.current_module(settings, "2") is module
    assert controller.module_rater(module) == "alice"

    replacement = {"name": "replacement", "rater": "bob"}
    controller.set_current_module(settings, "2", replacement)

    assert settings["qcmodule"]["2"] is replacement


def test_qc_page_controller_shapes_module_tables_and_subjects(tmp_path) -> None:
    controller = QCPageController()
    qctable = table = pd.DataFrame({"ezqcid": ["SUB002", "SUB001", "SUB001"], "x": [2, 1, 1]})
    tables = {"ezqc_qctable": qctable, "ezqc_all": pd.DataFrame({"ezqcid": ["SUB_ALL"]})}

    assert controller.ensure_module_table(tables, "example") is qctable
    assert tables["example"] is qctable
    assert controller.table_has_rows(table)
    assert not controller.table_has_rows(pd.DataFrame())
    assert controller.module_subject_rows(tables, "example")["ezqcid"].tolist() == ["SUB001", "SUB002"]
    assert controller.first_subject_id(tables, "example") == "SUB001"
    assert controller.subject_exists(tables, "example", "SUB002")
    assert not controller.subject_exists(tables, "example", "MISSING")
    assert controller.module_table(tables, "example") is qctable
    assert controller.module_rater_dir(tmp_path, "example", "rater") == str(tmp_path / "RatingFiles" / "example" / "rater")


def test_qc_page_runtime_context_wraps_legacy_dt_and_syncs_rating_dir(tmp_path) -> None:
    dt = type("LegacyDT", (), {})()
    dt.settings = {"qcmodule": {}}
    dt.tab = {"ezqc_qctable": pd.DataFrame({"ezqcid": ["SUB001"]})}
    dt.output_dir = str(tmp_path)

    context = QCPageRuntimeContext.from_legacy_dt(dt)
    path = context.set_module_rater_dir(tmp_path / "RatingFiles" / "example" / "rater")

    assert context.settings is dt.settings
    assert context.tables is dt.tab
    assert context.output_dir == str(tmp_path)
    assert path == str(tmp_path / "RatingFiles" / "example" / "rater")
    assert dt.dir_module_rater == path


def test_qc_page_runtime_context_can_be_built_from_gui_state(tmp_path) -> None:
    dt = type("LegacyDT", (), {})()
    dt.settings = {"qcmodule": {}}
    dt.tab = {"ezqc_qctable": pd.DataFrame({"ezqcid": ["SUB001"]})}
    dt.output_dir = str(tmp_path)
    gui_state = type("GuiState", (), {"dt": dt})()

    context = QCPageRuntimeContext.from_gui_state(gui_state)

    assert context.legacy_dt is dt
    assert context.settings is dt.settings
    assert context.tables is dt.tab


def test_legacy_qcpage_runtime_context_prefers_gui_state(tmp_path) -> None:
    dt = type("LegacyDT", (), {})()
    dt.settings = {"qcmodule": {}}
    dt.tab = {"ezqc_qctable": pd.DataFrame({"ezqcid": ["SUB001"]})}
    dt.output_dir = str(tmp_path)
    page = gui_qcpage.gui_qcpage()
    page.gui_state = type("GuiState", (), {"dt": dt})()

    context = page._ensure_runtime_context()

    assert context.legacy_dt is dt
    assert not hasattr(page, "dt")


def test_legacy_qcpage_rating_state_writes_delegate_to_controller() -> None:
    source = "\n".join(
        [
            inspect.getsource(gui_qcpage.gui_qcpage.qcpage_widgets),
            inspect.getsource(gui_qcpage.gui_qcpage.init_present),
            inspect.getsource(gui_qcpage.gui_qcpage.open_image),
            inspect.getsource(gui_qcpage.gui_qcpage.gen_present),
            inspect.getsource(gui_qcpage.gui_qcpage.check_module),
        ]
    )
    assert "self._ensure_controller().set_score_value(" in source
    assert "self._ensure_controller().set_tag_value(" in source
    assert "self._ensure_controller().set_notes(" in source
    assert "controller.reset_rating_state(" in source
    assert "controller.set_current_module(" in source
    assert "self._ensure_controller().set_code_execution(" in source
    assert "controller.module_index_by_name(" in source
    assert "controller.module_rater(" in source
    assert "self._ensure_controller().set_subject(" in source
    assert "['scores'][i].update" not in source
    assert "['tags'][tag_key].update" not in source
    assert "['notes'] = self.notes_text" not in source
    assert "['code_exe'] = code_exe" not in source
    assert "self.dt.settings['qcmodule'].items()" not in source
    assert "self.dt.settings['qcmodule'][self.module_index] = module" not in source
    assert "self.dt.settings['qcmodule'][self.module_index]['ezqcid'] = None" not in source


def test_legacy_qcpage_runtime_state_reads_use_runtime_context() -> None:
    source = "\n".join(
        [
            inspect.getsource(gui_qcpage.gui_qcpage.current_module),
            inspect.getsource(gui_qcpage.gui_qcpage.check_table),
            inspect.getsource(gui_qcpage.gui_qcpage.check_module),
            inspect.getsource(gui_qcpage.gui_qcpage.populate_listbox),
            inspect.getsource(gui_qcpage.gui_qcpage.save_rating),
            inspect.getsource(gui_qcpage.gui_qcpage.load_rating),
            inspect.getsource(gui_qcpage.gui_qcpage.init_present),
            inspect.getsource(gui_qcpage.gui_qcpage.gen_present),
            inspect.getsource(gui_qcpage.gui_qcpage.gen_code),
        ]
    )

    assert "self._runtime_settings()" in source
    assert "self._runtime_tables()" in source
    assert "self._module_rater_dir()" in source
    assert "self.dt.settings" not in source
    assert "self.dt.tab" not in source
    assert "self.dt.dir_module_rater" not in source


def test_legacy_qcpage_save_rating_delegates_file_io_to_controller() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage.save_rating)
    assert "self._ensure_controller().save_legacy_module_rating(" in source
    assert "FileUtils.safe_json_save" not in source


def test_legacy_qcpage_load_rating_delegates_file_io_to_controller() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage.load_rating)
    assert "controller = self._ensure_controller()" in source
    assert "controller.load_first_legacy_module_rating(" in source
    assert "controller.find_rating_compatibility_issues(" in source
    assert "json.load" not in source
    assert "['num_'] != new_module" not in source
    assert "['label'] != new_module" not in source


def test_legacy_qcpage_list_preview_delegates_rating_file_io_to_controller() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage.populate_listbox)
    assert "controller = self._ensure_controller()" in source
    assert "controller.load_first_legacy_module_rating(" in source
    assert "self._ensure_controller().module_subject_rows(" in source
    assert "glob.glob" not in source
    assert "json.load" not in source


def test_legacy_qcpage_gen_code_delegates_template_logic_to_controller() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage.gen_code)
    assert "self._ensure_controller().generate_code(" in source
    assert "self._ensure_controller().module_table(" in source
    assert ".replace(" not in source
    assert "MULTICMD" not in source


def test_legacy_qcpage_main_launch_saves_settings_through_gui_state_adapter() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage.open_qcpage_from_main)

    assert "GUIStateBridge" in source
    assert "QCPageRuntimeContext.from_gui_state(self.gui_state)" in source
    assert "self.dt = self.runtime_context.legacy_dt" not in source
    assert "self.gui_state.save_settings()" in source
    assert "app.ProjM.dt" not in source
    assert "self.ProjM = app.ProjM" not in source
    assert "self.DataM = app.DataM" not in source
    assert "self.ProjM.save_settings(" not in source


def test_legacy_qcpage_process_shutdown_delegates_to_code_executor() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage.close_current_process)

    assert "self.code_executor.close_current_processes()" in source
    assert "os.killpg" not in source
    assert "platform.system()" not in source
    assert ".terminate()" not in source
    assert ".kill()" not in source


def test_legacy_qcpage_context_menu_binding_uses_shared_helper() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage.qcpage_widgets)

    assert "bind_context_menu(self.listbox, show_right_menu)" in source
    assert "platform.system()" not in source
    assert ".bind(\"<Button-3>\"" not in source
    assert ".bind(\"<Button-2>\"" not in source


def test_legacy_qcpage_removed_empty_keyboard_stub_and_bare_prints() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage)
    assert "def load_keyboard" not in source
    assert "print(" not in source


def test_legacy_qcpage_removed_noop_shell_window_parameter() -> None:
    source = inspect.getsource(gui_qcpage.gui_qcpage)
    assert "shell=True" not in source
    assert "shell=False" not in source
    assert "if shell" not in source


def test_dialog_main_project_methods_delegate_to_project_dialog() -> None:
    source = inspect.getsource(dialog_main.DialogMain.create_project)
    assert "self.ProjectD.create_project()" in source
    source = inspect.getsource(dialog_main.DialogMain.import_project)
    assert "self.ProjectD.import_project()" in source
    source = inspect.getsource(dialog_main.DialogMain.remove_project)
    assert "self.ProjectD.remove_project()" in source


def test_dialog_main_does_not_keep_unused_legacy_state_handles() -> None:
    source = inspect.getsource(dialog_main.DialogMain.__init__)

    assert "self.ProjM = app.ProjM" not in source
    assert "self.dt = self.app.ProjM.dt" not in source


def test_dialog_main_removed_empty_json_import_export_stubs() -> None:
    assert not hasattr(dialog_main.DialogMain, "import_json")
    assert not hasattr(dialog_main.DialogMain, "export_json")


def test_unused_utils_qcpage_stub_is_removed() -> None:
    easyqc_root = Path(__file__).resolve().parents[2]

    assert not (easyqc_root / "utils" / "qcpage.py").exists()


def test_main_window_has_no_stale_json_import_export_references() -> None:
    source = inspect.getsource(main_window)
    assert "import_json" not in source
    assert "export_json" not in source


def test_main_window_removed_empty_icon_setup_block() -> None:
    source = inspect.getsource(main_window.EasyQCApp.setup_window)
    assert "设置窗口图标" not in source
    assert "pass" not in source


def test_project_dialog_contains_project_lifecycle_methods() -> None:
    assert hasattr(dialogs.ProjectDialog, "create_project")
    assert hasattr(dialogs.ProjectDialog, "import_project")
    assert hasattr(dialogs.ProjectDialog, "remove_project")


def test_project_dialog_uses_gui_state_adapter_for_project_lifecycle() -> None:
    source = inspect.getsource(dialogs.ProjectDialog)
    assert "self.gui_state.has_project(" in source
    assert "self.gui_state.create_and_load_project(" in source
    assert "self.gui_state.import_project_from_dir(" in source
    assert "self.gui_state.project_display_rows()" in source
    assert "self.gui_state.remove_project(" in source
    assert "self.gui_state.project_names()" in source
    assert "self.gui_state.current_project_name()" in source
    assert "self.app.ProjM.create_project(" not in source
    assert "self.app.ProjM.load_project(" not in source
    assert "self.app.ProjM.rm_project(" not in source
    assert "self.dt.projects" not in source


def test_new_dialog_classes_do_not_keep_legacy_project_state_handles() -> None:
    source = inspect.getsource(dialogs)

    assert "self.gui_state = _gui_state_from_app(app)" in source
    assert "LegacyGUIStateAdapter(app.ProjM)" not in source
    assert "self.ProjM = app.ProjM" not in source
    assert "self.dt = app.ProjM.dt" not in source
    assert "dt = app.ProjM.dt" not in source


def test_dialog_gui_state_helper_prefers_existing_adapter_without_proj_manager() -> None:
    gui_state = object()
    app = type("App", (), {"gui_state": gui_state})()

    assert dialogs._gui_state_from_app(app) is gui_state


def test_dialog_gui_state_helper_returns_bridge_when_no_gui_state() -> None:
    # P2 step 4: _gui_state_from_app now returns a GUIStateBridge (no adapter).
    # When app has no gui_state, it constructs a bridge from app services.
    app = type("App", (), {"gui_state": None, "project_service": None,
                           "session_state": None, "table_service": None})()

    result = dialogs._gui_state_from_app(app)

    assert isinstance(result, GUIStateBridge)


def test_dialog_main_constant_methods_delegate_to_constant_dialog() -> None:
    source = inspect.getsource(dialog_main.DialogMain.add_constant)
    assert "self.ConstantD.add_constant()" in source
    source = inspect.getsource(dialog_main.DialogMain.refresh_constant_table)
    assert "self.ConstantD.refresh_constant_table()" in source
    source = inspect.getsource(dialog_main.DialogMain.edit_constant)
    assert "self.ConstantD.edit_constant(event)" in source


def test_constant_dialog_contains_constant_lifecycle_methods() -> None:
    assert hasattr(dialogs.ConstantDialog, "add_constant")
    assert hasattr(dialogs.ConstantDialog, "refresh_constant_table")
    assert hasattr(dialogs.ConstantDialog, "edit_constant")


def test_constant_dialog_writes_constants_through_gui_state_adapter() -> None:
    add_source = inspect.getsource(dialogs.ConstantDialog.add_constant)
    edit_source = inspect.getsource(dialogs.ConstantDialog.edit_constant)
    refresh_source = inspect.getsource(dialogs.ConstantDialog.refresh_constant_table)
    assert "self.gui_state.set_constant(" in add_source
    assert "self.gui_state.rename_constant(" in edit_source
    assert "self.gui_state.delete_constant(" in edit_source
    assert "self.gui_state.constant_items()" in refresh_source
    assert 'settings["constants"]' not in add_source + edit_source + refresh_source


def test_dialog_main_variable_methods_delegate_to_variable_dialog() -> None:
    for method_name in [
        "extract_path",
        "set_varname",
        "extract_file",
        "extract_words",
        "merge_newdata",
        "new_merge",
        "show_all_variable",
    ]:
        source = inspect.getsource(getattr(dialog_main.DialogMain, method_name))
        assert f"self.VariableD.{method_name}(" in source


def test_variable_dialog_contains_variable_lifecycle_methods() -> None:
    for method_name in [
        "extract_path",
        "set_varname",
        "extract_file",
        "extract_words",
        "merge_newdata",
        "new_merge",
        "show_all_variable",
    ]:
        assert hasattr(dialogs.VariableDialog, method_name)


def test_variable_dialog_writes_variables_through_gui_state_adapter() -> None:
    source = inspect.getsource(dialogs.VariableDialog)
    assert "self.gui_state.set_new_variable_table(" in source
    assert "self.gui_state.prepare_new_variable_table(" in source
    assert "self.gui_state.new_variable_merge_source(" in source
    assert "self.gui_state.set_filtered_variable_table(" in source
    assert "self.gui_state.set_all_variable_table(" in source
    assert "self.gui_state.merge_all_variables_as_rows(" in source
    assert "self.gui_state.merge_all_variables_as_columns(" in source
    assert "self.gui_state.refresh_project_after_variable_merge(" in source
    assert "self.dt.var[" not in source
    assert "self.ProjM.save_table(" not in source
    assert "self.ProjM.load_project(" not in source


def test_dialog_main_module_methods_delegate_to_module_dialog() -> None:
    for method_name in [
        "change_module_index",
        "import_module",
        "export_module",
        "manage_module",
        "start_qc",
    ]:
        source = inspect.getsource(getattr(dialog_main.DialogMain, method_name))
        assert f"self.ModuleD.{method_name}(" in source

    source = inspect.getsource(dialog_main.DialogMain.add_module)
    assert "self.ModuleD.add_module(" in source


def test_module_dialog_contains_module_lifecycle_methods() -> None:
    for method_name in [
        "add_module",
        "change_module_index",
        "import_module",
        "export_module",
        "manage_module",
        "start_qc",
    ]:
        assert hasattr(dialogs.ModuleDialog, method_name)


def test_module_dialog_uses_file_utils_for_json_loading() -> None:
    assert hasattr(dialogs.ModuleDialog, "load_module_file")
    source = inspect.getsource(dialogs)
    assert "json.load" not in source
    assert "FileUtils.safe_json_load" in source


def test_module_dialog_writes_modules_through_gui_state_adapter() -> None:
    source = inspect.getsource(dialogs.ModuleDialog)
    assert "self.gui_state.add_module(" in source
    assert "self.gui_state.modify_module(" in source
    assert "self.gui_state.insert_module(" in source
    assert "self.gui_state.delete_module(" in source
    assert "self.gui_state.export_module(" in source
    assert "self.gui_state.save_settings(" in source
    assert "self.gui_state.system_name()" in source
    assert "bind_context_menu(" in source
    assert "self.ProjM.add_qcmodule(" not in source
    assert "self.ProjM.modify_qcmodule(" not in source
    assert "self.ProjM.export_module(" not in source
    assert "self.ProjM.save_settings(" not in source
    assert "self.dt.system" not in source


def test_dialog_main_score_tag_methods_delegate_to_score_tag_editor() -> None:
    for method_name in [
        "add_score",
        "del_score",
        "validate_score",
        "add_tag",
        "del_tag",
    ]:
        source = inspect.getsource(getattr(dialog_main.DialogMain, method_name))
        assert f"self.ScoreTagE.{method_name}(" in source


def test_score_tag_editor_contains_score_tag_methods() -> None:
    for method_name in [
        "add_score",
        "del_score",
        "validate_score",
        "add_tag",
        "del_tag",
    ]:
        assert hasattr(dialogs.ScoreTagEditor, method_name)


def test_score_tag_editor_writes_through_gui_state_adapter() -> None:
    assert "self.gui_state.add_score(" in inspect.getsource(dialogs.ScoreTagEditor.add_score)
    assert "self.gui_state.delete_score(" in inspect.getsource(dialogs.ScoreTagEditor.del_score)
    assert "self.gui_state.add_tag(" in inspect.getsource(dialogs.ScoreTagEditor.add_tag)
    assert "self.gui_state.delete_tag(" in inspect.getsource(dialogs.ScoreTagEditor.del_tag)



# ---- P1-D: GUI subscribes to EventBus (AC-10, ADR-002) ----

def test_app_services_carries_event_bus() -> None:
    """P1-D: AppServices exposes a shared EventBus so the legacy main window
    can subscribe/unsubscribe on the same bus ProjectService emits to."""
    from core.event_bus import EventBus
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(gui_app.AppServices)}
    assert "event_bus" in field_names


def test_gui_app_builds_shared_event_bus_and_injects_into_project_service() -> None:
    """P1-D: the app wrapper builds ONE shared EventBus, passes it to
    ProjectService (so service emissions reach GUI subscribers), and stores it
    in AppServices."""
    init_source = inspect.getsource(gui_app.EasyQCApp.__init__)
    assert "EventBus(" in init_source
    assert "event_bus=" in init_source or "event_bus =" in init_source


def test_main_window_subscribes_project_and_modules_events_in_init() -> None:
    """P1-D: the main window subscribes to PROJECT_CHANGED + MODULES_CHANGED so
    it reacts to typed service events (AC-10), not only pull-based refresh.
    Subscription is encapsulated in _subscribe_event_bus, called from __init__."""
    from core.event_bus import EventType

    init_source = inspect.getsource(main_window.EasyQCApp.__init__)
    assert "_subscribe_event_bus" in init_source
    sub_source = inspect.getsource(main_window.EasyQCApp._subscribe_event_bus)
    assert "EventType.PROJECT_CHANGED" in sub_source
    assert "EventType.MODULES_CHANGED" in sub_source
    assert "subscribe" in sub_source


def test_main_window_has_event_bus_teardown_called_from_quit() -> None:
    """P1-D: teardown must run on quit to unsubscribe handlers (no leak)."""
    quit_source = inspect.getsource(main_window.EasyQCApp.quit_app)
    teardown_exists = hasattr(main_window.EasyQCApp, "teardown_event_bus")
    assert teardown_exists, "main window must define teardown_event_bus"
    assert "teardown_event_bus" in quit_source


def test_main_window_project_changed_handler_is_callable_without_tk() -> None:
    """P1-D: the typed-event handler runs without a live tkinter root (the
    EventBus calls it). It must be safe to call with a minimal app shell."""
    app = object.__new__(main_window.EasyQCApp)
    # handler should not assume GUI widgets exist; provide a no-op refresh
    app.load_project_to_gui = lambda: None
    app.load_module_to_gui = lambda: None
    # _on_project_changed must not raise
    app._on_project_changed()
    app._on_modules_changed()


def test_main_window_teardown_unsubscribes_its_handlers() -> None:
    """P1-D: after teardown, the handlers are no longer registered on the bus,
    so a later emit does not invoke them."""
    from core.event_bus import EventBus, Event, EventType

    bus = EventBus()
    app = object.__new__(main_window.EasyQCApp)
    app.event_bus = bus
    app._on_project_changed = lambda: None
    app._on_modules_changed = lambda: None
    app._project_changed_handler = app._on_project_changed
    app._modules_changed_handler = app._on_modules_changed
    bus.subscribe(EventType.PROJECT_CHANGED, app._project_changed_handler)
    bus.subscribe(EventType.MODULES_CHANGED, app._modules_changed_handler)

    app.teardown_event_bus()

    # emit after teardown -> handlers must NOT fire (verified via counter)
    fired = []
    bus.subscribe(EventType.PROJECT_CHANGED, fired.append)
    bus.emit(Event(type=EventType.PROJECT_CHANGED, source="X"))
    assert fired == [Event(type=EventType.PROJECT_CHANGED, source="X")]
    # the original handlers are gone; only the probe remains
