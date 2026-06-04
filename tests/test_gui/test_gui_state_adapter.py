from types import SimpleNamespace

import pandas as pd

from core.table_service import LoadedProjectTables
from gui.state_adapter import LegacyGUIStateAdapter
from models.project import Project


class _FakeProjectManager:
    def __init__(self, dt) -> None:
        self.dt = dt
        self.saved_settings = 0
        self.saved_tables = []
        self.loaded_projects = []
        self.created_projects = []
        self.removed_projects = []
        self.changed_projects = []
        self.loaded_ratings = 0

    def save_settings(self) -> None:
        self.saved_settings += 1

    def save_table(self, name=None, delete=False) -> None:
        self.saved_tables.append(name if not delete else (name, delete))

    def load_project(self, project=None, output_dir=None, fresh_gui=True) -> None:
        self.loaded_projects.append((project, output_dir, fresh_gui))
        if project is not None:
            self.dt.project = project
        if output_dir is not None:
            self.dt.project = "IMPORTED"
            self.dt.projects["IMPORTED"] = output_dir

    def create_project(self, name, path) -> None:
        self.created_projects.append((name, path))
        self.dt.projects[name] = path

    def rm_project(self, project) -> None:
        self.removed_projects.append(project)
        self.dt.projects.pop(project, None)

    def change_project(self, project) -> None:
        self.changed_projects.append(project)
        self.dt.project = project

    def load_ratings(self) -> None:
        self.loaded_ratings += 1

    def add_key(self, values, index, value=None):
        result = {str(key): item for key, item in values.items()}
        if value is None:
            del result[str(index)]
        else:
            if str(index) in result:
                for key in range(max(int(item) for item in result), index - 1, -1):
                    if str(key) in result:
                        result[str(key + 1)] = result[str(key)]
            result[str(index)] = value
        return {str(new_index): result[str(old_index)] for new_index, old_index in enumerate(sorted(int(k) for k in result), 1)}

    def add_qcmodule(self, values, index, name, label):
        module = {
            "name": name,
            "label": label,
            "scores": {"1": {"label": None, "num": None, "num_": None, "value": None}},
            "tags": {"1": {"label": None, "value": None}},
            "select_filter": None,
        }
        return self.add_key(values, int(index), module)

    def modify_qcmodule(self, index, name_, label_, index_):
        module = self.dt.settings["qcmodule"][str(index)].copy()
        module["name"] = name_
        module["label"] = label_
        del self.dt.settings["qcmodule"][str(index)]
        self.dt.settings["qcmodule"] = self.add_key(self.dt.settings["qcmodule"], int(index_), module)

    def check_module(self, module):
        return all(key in module for key in ["name", "label", "scores", "tags"])

    def export_module(self, module_name, path):
        self.exported_module = (module_name, path)


def _dt():
    return SimpleNamespace(
        project="SAMPLE",
        output_dir="/tmp/easyqc_SAMPLE",
        projects={"SAMPLE": "/tmp/easyqc_SAMPLE"},
        var={
            "ezqc_new": pd.DataFrame({"ezqcid": ["SUB_NEW"]}),
            "ezqc_filter": None,
            "ezqc_all": pd.DataFrame({"ezqcid": ["SUB_ALL"]}),
        },
        tab={
            "ezqc_qctable": pd.DataFrame({"ezqcid": ["SUB_QC"]}),
        },
        settings={
            "constants": {"old": "1"},
            "var_select_filter": '{"operations": []}',
            "select_filter": '{"operations": [{"operation": "select_columns", "columns": ["ezqcid"]}]}',
            "qcmodule": {
                "2": {
                    "name": "second",
                    "select_filter": "second filter",
                    "scores": {"1": {"label": "Quality"}},
                    "tags": {"1": {"label": "Artifact"}},
                },
                "1": {
                    "name": "first",
                    "select_filter": "first filter",
                    "scores": {"1": {"label": "Quality"}},
                    "tags": {"1": {"label": "Artifact"}},
                },
            },
        },
    )


def test_gui_state_adapter_shapes_project_and_module_state() -> None:
    data = _dt()
    data.system = "Darwin"
    adapter = LegacyGUIStateAdapter(dt=data)

    assert adapter.project_names() == ["SAMPLE"]
    assert adapter.current_project_name() == "SAMPLE"
    assert adapter.has_project("SAMPLE")
    assert adapter.project_display_rows() == ["SAMPLE - /tmp/easyqc_SAMPLE"]
    assert adapter.system_name() == "Darwin"
    assert adapter.module_keys() == ["1", "2"]
    assert adapter.module_names() == ["second", "first"]
    assert adapter.module_by_key("1")["name"] == "first"
    assert adapter.module_index_by_name("second") == "2"
    assert adapter.next_module_index() == "3"
    assert adapter.module_name_exists("first")
    assert not adapter.module_name_exists("first", exclude_name="first")
    assert adapter.module_table_rows()[0] == ("2", "second", None)


def test_gui_state_adapter_shapes_current_project_model() -> None:
    data = _dt()
    adapter = LegacyGUIStateAdapter(dt=data)

    project = adapter.current_project_model()

    assert isinstance(project, Project)
    assert project.name == "SAMPLE"
    assert str(project.path) == "/tmp/easyqc_SAMPLE"

    data.output_dir = None
    assert adapter.current_project_model() is None


def test_gui_state_adapter_manages_project_lifecycle_through_project_manager() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)

    adapter.create_and_load_project("NEW", "/tmp/easyqc_NEW")
    adapter.import_project_from_dir("/tmp/easyqc_IMPORTED")
    adapter.remove_project("SAMPLE")
    adapter.load_project("NEW")
    adapter.change_project("NEW")

    assert manager.created_projects == [("NEW", "/tmp/easyqc_NEW")]
    assert manager.loaded_projects == [
        ("NEW", None, True),
        (None, "/tmp/easyqc_IMPORTED", True),
        ("NEW", None, True),
    ]
    assert manager.removed_projects == ["SAMPLE"]
    assert manager.changed_projects == ["NEW"]
    assert "SAMPLE" not in data.projects


def test_gui_state_adapter_updates_constants_and_saves_settings() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)

    adapter.set_constant("new", "2")
    adapter.rename_constant("old", "renamed", "3")
    adapter.delete_constant("new")

    assert dict(adapter.constant_items()) == {"renamed": "3"}
    assert adapter.has_constant("renamed")
    assert manager.saved_settings == 3


def test_gui_state_adapter_handles_main_window_table_and_save_actions() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)

    new_table = adapter.new_variable_table()
    all_table = adapter.all_variable_table()
    adapter.load_ratings()
    adapter.save_project_state()

    assert new_table.equals(pd.DataFrame({"ezqcid": ["SUB_NEW"]}))
    assert all_table.equals(pd.DataFrame({"ezqcid": ["SUB_ALL"]}))
    assert manager.loaded_ratings == 1
    assert manager.saved_settings == 1
    assert manager.saved_tables == [None]


def test_gui_state_adapter_resolves_filter_sources_as_copies() -> None:
    data = _dt()
    adapter = LegacyGUIStateAdapter(dt=data)

    result, select_filter = adapter.resolve_filter_source("qctable")

    assert result.equals(pd.DataFrame({"ezqcid": ["SUB_QC"]}))
    assert select_filter == data.settings["select_filter"]
    result.loc[0, "ezqcid"] = "CHANGED"
    assert data.tab["ezqc_qctable"].loc[0, "ezqcid"] == "SUB_QC"

    module_result, module_filter = adapter.resolve_filter_source("first")
    assert module_result.equals(pd.DataFrame({"ezqcid": ["SUB_QC"]}))
    assert module_filter == "first filter"


def test_gui_state_adapter_saves_filter_results_through_project_manager() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)
    result = pd.DataFrame({"ezqcid": ["SUB_SAVE"]})
    query = '{"operations": []}'

    adapter.save_filter_result("qctable", result, query)
    adapter.save_filter_result("first", result, query)

    assert data.tab["ezqc_qctable_filter"].equals(result)
    assert data.settings["select_filter"] == query
    assert data.tab["first"].equals(result)
    assert data.settings["qcmodule"]["1"]["select_filter"] == query
    assert manager.saved_settings == 2
    assert manager.saved_tables == ["ezqc_qctable_filter", "first"]


def test_gui_state_adapter_updates_score_and_tag_keys() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)

    adapter.add_score("1", 2)
    adapter.add_tag("1", 2)
    adapter.delete_score("1", 1)
    adapter.delete_tag("1", 1)

    assert data.settings["qcmodule"]["1"]["scores"] == {
        "1": {"label": None, "num": None, "num_": None, "value": None}
    }
    assert data.settings["qcmodule"]["1"]["tags"] == {"1": {"label": None, "value": None}}
    assert manager.saved_settings == 4


def test_gui_state_adapter_updates_module_score_and_tag_fields_without_saving() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)

    adapter.update_module_field("1", "rater", "alice")
    adapter.update_score_fields("1", "1", label="QC", num="1-3", num_="1,2,3")
    adapter.update_tag_fields("1", "1", label="Motion")

    module = data.settings["qcmodule"]["1"]
    assert module["rater"] == "alice"
    assert module["scores"]["1"] == {"label": "QC", "num": "1-3", "num_": "1,2,3"}
    assert module["tags"]["1"] == {"label": "Motion"}
    assert manager.saved_settings == 0


def test_gui_state_adapter_manages_modules_through_project_manager() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)

    adapter.add_module("third", "Third", 3)
    adapter.modify_module("3", "renamed", "Renamed", 1)
    adapter.insert_module(2, {"name": "imported", "label": "Imported", "scores": {}, "tags": {}})
    adapter.delete_module(2)
    adapter.export_module("renamed", "/tmp/qcmodule_renamed.json")

    assert adapter.module_by_key("1")["name"] == "renamed"
    assert "imported" not in adapter.module_names()
    assert manager.exported_module == ("renamed", "/tmp/qcmodule_renamed.json")
    assert manager.saved_settings == 4


def test_gui_state_adapter_shapes_rating_menu_items() -> None:
    data = _dt()
    adapter = LegacyGUIStateAdapter(dt=data)

    assert not adapter.has_rating_data()

    data.rating_dict = {
        "SUB001": {
            "first-rater": {"name": "first", "rater": "rater"},
            "bad": "not a dict",
        }
    }

    assert adapter.has_rating_data()
    assert adapter.rating_menu_items("SUB001") == [
        {"label": "打开评分结果: first-rater", "name": "first", "rater": "rater"}
    ]


def test_gui_state_adapter_prepares_new_variable_table_and_filter_copy() -> None:
    data = _dt()
    data.var["ezqc_new"] = pd.DataFrame({0: ["SUB002", "SUB001"]})
    adapter = LegacyGUIStateAdapter(dt=data)

    result = adapter.prepare_new_variable_table("ezqcid")

    expected = pd.DataFrame({"ezqcid": ["SUB001", "SUB002"]}, index=[1, 0])
    assert data.var["ezqc_new"].equals(expected)
    assert data.var["ezqc_filter"].equals(expected)
    assert result.equals(expected)
    result.loc[1, "ezqcid"] = "CHANGED"
    assert data.var["ezqc_new"].loc[1, "ezqcid"] == "SUB001"


def test_gui_state_adapter_prepares_empty_path_import_table_without_keyerror() -> None:
    data = _dt()
    data.var["ezqc_new"] = pd.DataFrame()
    adapter = LegacyGUIStateAdapter(dt=data)

    result = adapter.prepare_new_variable_table("ccs_dir")

    expected = pd.DataFrame(columns=["ccs_dir"])
    assert data.var["ezqc_new"].equals(expected)
    assert data.var["ezqc_filter"].equals(expected)
    assert result.equals(expected)


def test_gui_state_adapter_leaves_multicolumn_table_when_varname_is_missing() -> None:
    data = _dt()
    source = pd.DataFrame({"subject": ["SUB001"], "age": [12]})
    data.var["ezqc_new"] = source.copy()
    adapter = LegacyGUIStateAdapter(dt=data)

    result = adapter.prepare_new_variable_table("ccs_dir")

    assert data.var["ezqc_new"].equals(source)
    assert data.var["ezqc_filter"].equals(source)
    assert result.equals(source)


def test_gui_state_adapter_selects_variable_merge_source_as_copy() -> None:
    data = _dt()
    data.var["ezqc_filter"] = pd.DataFrame({"ezqcid": ["FILTER"]})
    adapter = LegacyGUIStateAdapter(dt=data)

    result = adapter.new_variable_merge_source()

    assert result.equals(pd.DataFrame({"ezqcid": ["FILTER"]}))
    result.loc[0, "ezqcid"] = "CHANGED"
    assert data.var["ezqc_filter"].loc[0, "ezqcid"] == "FILTER"

    data.var["ezqc_filter"] = None
    assert adapter.new_variable_merge_source().equals(pd.DataFrame({"ezqcid": ["SUB_NEW"]}))


def test_gui_state_adapter_merges_variable_tables_and_refreshes_project() -> None:
    data = _dt()
    manager = _FakeProjectManager(data)
    adapter = LegacyGUIStateAdapter(project_manager=manager)

    adapter.merge_all_variables_as_rows(pd.DataFrame({"ezqcid": ["SUB_NEW"]}))
    assert data.var["ezqc_all"]["ezqcid"].tolist() == ["SUB_ALL", "SUB_NEW"]

    adapter.set_all_variable_table(pd.DataFrame({"ezqcid": ["SUB_ALL"], "age": [1]}))
    adapter.merge_all_variables_as_columns(pd.DataFrame({"ezqcid": ["SUB_ALL"], "sex": ["F"]}))
    assert data.var["ezqc_all"].to_dict("records") == [{"ezqcid": "SUB_ALL", "age": 1, "sex": "F"}]

    adapter.refresh_project_after_variable_merge()

    assert manager.saved_tables == ["ezqc_all", ("table", True)]
    assert manager.loaded_projects == [("SAMPLE", None, True)]


def test_gui_state_adapter_applies_loaded_project_tables_as_legacy_state() -> None:
    data = _dt()
    adapter = LegacyGUIStateAdapter(dt=data)
    loaded_tables = LoadedProjectTables(
        variables={"ezqc_all": pd.DataFrame({"ezqcid": ["SUB_SERVICE"]})},
        results={
            "ezqc_qctable": pd.DataFrame({"ezqcid": ["SUB_QC"], "score": [1]}),
            "AnatRestAll": pd.DataFrame({"ezqcid": ["SUB_MODULE"]}),
            "MissingModule": None,
        },
    )

    adapter.apply_loaded_tables(loaded_tables)

    assert data.var["ezqc_all"].equals(pd.DataFrame({"ezqcid": ["SUB_SERVICE"]}))
    assert data.tab["ezqc_qctable"].equals(pd.DataFrame({"ezqcid": ["SUB_QC"], "score": [1]}))
    assert data.tab["AnatRestAll"].equals(pd.DataFrame({"ezqcid": ["SUB_MODULE"]}))
    assert data.tab["MissingModule"] is None


def test_gui_state_adapter_applies_loaded_ratings_as_legacy_state() -> None:
    data = _dt()
    adapter = LegacyGUIStateAdapter(dt=data)
    qctable = pd.DataFrame({"ezqcid": ["SUB001"], "example.rater1.score1": ["Good"]})
    rating_dict = {
        "SUB001": {
            "example-rater1": {
                "name": "example",
                "rater": "rater1",
                "scores": {"1": {"value": "Good"}},
            }
        }
    }

    adapter.apply_loaded_ratings(SimpleNamespace(rating_dict=rating_dict, qctable=qctable))

    assert data.rating_dict == rating_dict
    assert data.tab["ezqc_qctable"].equals(qctable)

    rating_dict["SUB001"]["example-rater1"]["scores"]["1"]["value"] = "Changed"
    qctable.loc[0, "example.rater1.score1"] = "Changed"

    assert data.rating_dict["SUB001"]["example-rater1"]["scores"]["1"]["value"] == "Good"
    assert data.tab["ezqc_qctable"].loc[0, "example.rater1.score1"] == "Good"
