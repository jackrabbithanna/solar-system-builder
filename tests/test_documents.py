import json
import unittest

from src.documents import parse_document, serialize_document, unique_import_name
from src.models import ModelError
from src.presets import load_builtin_solar_system, load_builtin_solar_system_by_id


class DocumentTests(unittest.TestCase):
    def test_serialize_parse_round_trip_is_canonical(self):
        system = load_builtin_solar_system()

        contents = serialize_document(system)
        parsed = parse_document(contents)

        self.assertTrue(contents.endswith(b"\n"))
        self.assertEqual(parsed.to_dict(), system.to_dict())
        self.assertEqual(json.loads(contents), system.to_dict())

    def test_parse_uses_existing_schema_migrations(self):
        data = load_builtin_solar_system().to_dict()
        data["schema_version"] = 10
        data["settings"].pop("trail_frame")

        parsed = parse_document(json.dumps(data))

        self.assertEqual(parsed.settings.trail_frame, "focused_parent")

    def test_parse_rejects_invalid_utf8_json_and_root_type(self):
        with self.assertRaisesRegex(ModelError, "UTF-8"):
            parse_document(b"\xff")
        with self.assertRaisesRegex(ModelError, "valid JSON"):
            parse_document("{")
        with self.assertRaisesRegex(ModelError, "JSON object"):
            parse_document("[]")

    def test_parse_rejects_invalid_model(self):
        data = load_builtin_solar_system().to_dict()
        data["bodies"] = []

        with self.assertRaisesRegex(ModelError, "at least one body"):
            parse_document(json.dumps(data))

    def test_unique_import_name_is_case_insensitive_and_stable(self):
        self.assertEqual(unique_import_name("Visitor", ["Other"]), "Visitor")
        self.assertEqual(unique_import_name("Visitor", ["visitor"]), "Visitor Imported")
        self.assertEqual(
            unique_import_name("Visitor", ["Visitor", "visitor imported", "Visitor Imported 2"]),
            "Visitor Imported 3",
        )
        self.assertEqual(unique_import_name("  ", []), "Imported System")

    def test_import_copy_regenerates_all_linked_ids(self):
        parsed = parse_document(serialize_document(load_builtin_solar_system()))

        copied = parsed.duplicate("Imported")

        self.assertNotEqual(copied.id, parsed.id)
        self.assertTrue(
            set(body.id for body in copied.bodies).isdisjoint(
                body.id for body in parsed.bodies
            )
        )
        copied_ids = {body.id for body in copied.bodies}
        self.assertTrue(
            all(body.parent_id is None or body.parent_id in copied_ids for body in copied.bodies)
        )
        self.assertTrue(
            all(body_id in copied_ids for group in copied.groups for body_id in group.body_ids)
        )

    def test_bundled_preset_can_be_reloaded_by_id(self):
        preset = load_builtin_solar_system_by_id("builtin-solar-system")

        self.assertEqual(preset.id, "builtin-solar-system")
        with self.assertRaisesRegex(ValueError, "unknown bundled preset"):
            load_builtin_solar_system_by_id("missing")


if __name__ == "__main__":
    unittest.main()
