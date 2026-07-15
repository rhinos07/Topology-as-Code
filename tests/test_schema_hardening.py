import copy
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import compile_storage_types  # noqa: E402
from validate import check_section_membership, make_validator, validate_storage_types  # noqa: E402


STORAGE_VALIDATOR = make_validator("storage-type.schema.json")
MOVEMENT_VALIDATOR = make_validator("movement-rule.schema.json")
WCS_VALIDATOR = make_validator("wcs.schema.json")
STORAGE_FILE_VALIDATOR = make_validator("storage.schema.json")


def rack():
    return {
        "id": "RACK_A",
        "automation_level": "manual",
        "movement_policy": "default_allow",
        "access_model": "rack",
        "access_order": "direct",
        "storage_point_generator": {
            "aisles": 1, "stacks": 1, "levels": 1,
            "coordinate_pattern": "{aisle}-{stack}-{level}",
        },
    }


class StorageTypeSchemaTests(unittest.TestCase):
    def assert_invalid(self, value):
        self.assertTrue(list(STORAGE_VALIDATOR.iter_errors(value)))

    def test_unknown_field_is_rejected(self):
        value = rack()
        value["max_weigth"] = "500kg"
        self.assert_invalid(value)

    def test_legacy_string_quantity_is_rejected(self):
        value = rack()
        value["default_attributes"] = {"max_weight": "500kg"}
        self.assert_invalid(value)

    def test_exactly_one_point_definition_is_required(self):
        value = rack()
        value["storage_points"] = [{"coordinate": "A"}]
        self.assert_invalid(value)

    def test_generated_block_requires_physical_access_fields(self):
        value = rack()
        value.update({"id": "BLOCK_A", "access_model": "block", "access_order": "lifo"})
        self.assert_invalid(value)

    def test_rack_cannot_be_lifo(self):
        value = rack()
        value["access_order"] = "lifo"
        self.assert_invalid(value)

    def test_automated_storage_requires_explicit_policy(self):
        value = rack()
        value["automation_level"] = "conveyor_automated"
        self.assert_invalid(value)

    def test_blocked_point_requires_reason(self):
        value = rack()
        value["exceptions"] = [{"coordinate": "1-1-1", "blocked": True}]
        self.assert_invalid(value)

    def test_duplicate_storage_type_ids_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.yaml"
            path.write_text(yaml.safe_dump({"storage_types": [rack(), copy.deepcopy(rack())]}), encoding="utf-8")
            errors = validate_storage_types(path)
        self.assertTrue(any("duplicate storage_type id 'RACK_A'" in error for error in errors))

    def test_unknown_activity_area_field_is_rejected(self):
        data = {
            "storage_types": [rack()],
            "activity_areas": [{
                "id": "PICK_A", "activity": "picking",
                "bins_from": ["RACK_A"], "activty": "typo",
            }],
        }
        self.assertTrue(list(STORAGE_FILE_VALIDATOR.iter_errors(data)))

    def test_selector_membership_and_direct_override_are_compiled(self):
        value = rack()
        value["storage_point_generator"]["aisles"] = 3
        value["sections"] = [
            {"id": "A", "selector": {"aisles": {"from": 1, "to": 2}}},
            {"id": "B", "selector": {"aisles": {"values": [3]}}},
        ]
        value["exceptions"] = [{"coordinate": "2-1-1", "section": "B"}]
        value["section_membership"] = {"require_full_coverage": True}
        self.assertEqual(check_section_membership(Path("storage.yaml"), {"storage_types": [value]}), [])
        points, warnings = compile_storage_types({"storage_types": [value]})
        self.assertEqual(warnings, [])
        sections = {point["coordinate"]: point["section"] for point in points}
        self.assertEqual(sections, {
            "1-1-1": "RACK_A.A",
            "2-1-1": "RACK_A.B",
            "3-1-1": "RACK_A.B",
        })

    def test_overlapping_section_selectors_are_rejected(self):
        value = rack()
        value["storage_point_generator"]["aisles"] = 3
        value["sections"] = [
            {"id": "A", "selector": {"aisles": {"from": 1, "to": 2}}},
            {"id": "B", "selector": {"aisles": {"from": 2, "to": 3}}},
        ]
        errors = check_section_membership(Path("storage.yaml"), {"storage_types": [value]})
        self.assertTrue(any("matches multiple sections" in error for error in errors))

    def test_required_section_coverage_is_checked(self):
        value = rack()
        value["storage_point_generator"]["aisles"] = 2
        value["sections"] = [
            {"id": "A", "selector": {"aisles": {"values": [1]}}},
        ]
        value["section_membership"] = {"require_full_coverage": True}
        errors = check_section_membership(Path("storage.yaml"), {"storage_types": [value]})
        self.assertTrue(any("has no section" in error for error in errors))


class MovementRuleSchemaTests(unittest.TestCase):
    def assert_invalid(self, value):
        self.assertTrue(list(MOVEMENT_VALIDATOR.iter_errors(value)))

    def test_rule_requires_at_least_one_endpoint(self):
        self.assert_invalid({"id": "RULE", "allowed": True})

    def test_endpoint_cannot_be_empty(self):
        self.assert_invalid({"id": "RULE", "allowed": True, "to": {}})

    def test_section_requires_storage_type(self):
        self.assert_invalid({"id": "RULE", "allowed": True, "to": {"section": "SEC_A"}})

    def test_denial_requires_reason(self):
        self.assert_invalid({"id": "RULE", "allowed": False, "to": {"storage_type": "A"}})

    def test_conversion_requires_source_load_unit_types(self):
        self.assert_invalid({
            "id": "RULE", "allowed": True,
            "to": {"storage_type": "A"},
            "conversion_of_load_unit_required": True,
        })


class WcsSchemaTests(unittest.TestCase):
    def test_runtime_availability_is_rejected(self):
        data = {
            "controller_definitions": [{"id": "CTRL"}],
            "reporting_points": [{
                "id": "RP", "controller": "CTRL", "availability": "available",
            }],
        }
        self.assertTrue(list(WCS_VALIDATOR.iter_errors(data)))

    def test_unknown_controller_field_is_rejected(self):
        data = {"controller_definitions": [{"id": "CTRL", "adress": "typo"}]}
        self.assertTrue(list(WCS_VALIDATOR.iter_errors(data)))

    def test_autonomous_equipment_requires_controller(self):
        data = {"equipment": [{"id": "FLEET", "type": "robot", "mode": "controller_autonomous"}]}
        self.assertTrue(list(WCS_VALIDATOR.iter_errors(data)))


if __name__ == "__main__":
    unittest.main()
