import tempfile
import unittest
from pathlib import Path

from src.presets import load_builtin_solar_system
from src.storage import Library


class StorageTests(unittest.TestCase):
    def test_save_load_list_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Library(Path(temp_dir))
            system = load_builtin_solar_system().duplicate("Test System")

            library.save(system)

            self.assertEqual([item.id for item in library.list_systems()], [system.id])
            self.assertEqual(library.load(system.id).name, "Test System")

            library.delete(system.id)
            self.assertEqual(library.list_systems(), [])


if __name__ == "__main__":
    unittest.main()
