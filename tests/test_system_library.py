import tempfile
import unittest
from pathlib import Path

from src.presets import load_builtin_solar_system
from src.storage import Library
from src.system_library import SystemLibraryController


class FakeDropdown:
    def __init__(self):
        self.model = None
        self.selected = 0

    def set_model(self, model):
        self.model = model

    def set_selected(self, selected):
        self.selected = selected

    def get_selected(self):
        return self.selected


class FakePanel:
    def __init__(self):
        self.loaded = None

    def load_system(self, system, editable):
        self.loaded = (system, editable)


class FakeDialog:
    def __init__(self, response):
        self.response = response

    def choose_finish(self, _result):
        return self.response


class SystemLibraryControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.library = Library(Path(self.temp_dir.name))
        self.current = load_builtin_solar_system()
        self.dropdown = FakeDropdown()
        self.panel = FakePanel()
        self.prepared = []
        self.loaded = []
        self.saved = []
        self.renamed = 0

        self.controller = SystemLibraryController.__new__(SystemLibraryController)
        self.controller.dropdown = self.dropdown
        self.controller.system_panel = self.panel
        self.controller.library = self.library
        self.controller.load_builtins = lambda: [load_builtin_solar_system()]
        self.controller.load_default = load_builtin_solar_system
        self.controller.current_system = lambda: self.current
        self.controller.prepare_for_save = self.prepared.append
        self.controller.load_system = self._load_system
        self.controller.system_saved = self._save_system
        self.controller.system_renamed = self._rename_system
        self.controller.systems = []
        self.controller.updating_dropdown = False

    def _load_system(self, system):
        self.current = system
        self.loaded.append(system)

    def _save_system(self, system):
        self.current = system
        self.saved.append(system)

    def _rename_system(self):
        self.renamed += 1

    def test_save_builtin_creates_editable_copy(self):
        builtin_id = self.current.id

        self.controller._on_save_clicked(None)

        self.assertNotEqual(self.current.id, builtin_id)
        self.assertTrue(self.controller.is_user_saved(self.current))
        self.assertEqual(self.prepared, [self.current])
        self.assertEqual(self.saved, [self.current])
        self.assertEqual(self.library.load(self.current.id).name, self.current.name)
        self.assertEqual(self.panel.loaded, (self.current, True))

    def test_duplicate_saves_and_selects_copy(self):
        original_id = self.current.id

        self.controller._on_duplicate_clicked(None)

        self.assertNotEqual(self.current.id, original_id)
        self.assertEqual(self.loaded, [self.current])
        self.assertEqual(self.dropdown.selected, 1)
        self.assertEqual(self.library.load(self.current.id).id, self.current.id)

    def test_rename_refreshes_active_label_without_saving(self):
        self.current = self.current.duplicate("Before")
        self.library.save(self.current)

        self.controller._on_system_name_edited(None, "After")

        self.assertEqual(self.current.name, "After")
        self.assertEqual(self.renamed, 1)
        self.assertEqual(self.dropdown.model.get_string(1), "After")
        self.assertEqual(self.library.load(self.current.id).name, "Before")

    def test_delete_response_ignores_cancel_and_builtin_ids(self):
        saved = self.current.duplicate("Saved")
        self.library.save(saved)
        self.current = saved

        self.controller._on_delete_response(FakeDialog("cancel"), None, saved.id)
        self.controller._on_delete_response(FakeDialog("delete"), None, "builtin-test")

        self.assertEqual(self.library.load(saved.id).name, "Saved")
        self.assertEqual(self.loaded, [])

    def test_delete_response_removes_saved_system_and_loads_default(self):
        saved = self.current.duplicate("Saved")
        self.library.save(saved)
        self.current = saved

        self.controller._on_delete_response(FakeDialog("delete"), None, saved.id)

        self.assertEqual(self.library.list_systems(), [])
        self.assertEqual(self.current.id, "builtin-solar-system")
        self.assertEqual(self.dropdown.selected, 0)


if __name__ == "__main__":
    unittest.main()
