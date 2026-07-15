import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate import (  # noqa: E402
    check_graph_reachability,
    check_movement_rule_refs,
    check_storage_coordinate_integrity,
    check_storage_refs,
    check_topology_id_uniqueness,
    check_wcs_refs,
)


PATH = Path("fixture.yaml")
ELEMENT_IDS = {
    "load_unit_types": {"pallet"},
    "blocking_reasons": {"MAINTENANCE"},
    "hazmat_classes": {"GHS02"},
    "equipment_types": {"robot"},
    "process_types": {"putaway"},
    "workstation_templates": {"ws_port"},
    "lane_templates": {"lane_standard"},
}


class ReferentialIntegrityTests(unittest.TestCase):
    def test_storage_catalog_references_are_checked(self):
        storage = {
            "storage_types": [{
                "id": "A", "controller": "MISSING",
                "layout_variants": [{"id": "V", "load_unit_type": "missing"}],
                "default_attributes": {"hazmat_classes": ["missing"]},
            }],
            "work_centers": [{"id": "WC", "workstation_template": "missing"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.yaml"
            path.write_text("storage_types: []\n", encoding="utf-8")
            errors = check_storage_refs(path, set(), ELEMENT_IDS)
            # Replace file content through the normal loader input.
            import yaml
            path.write_text(yaml.safe_dump(storage), encoding="utf-8")
            errors = check_storage_refs(path, set(), ELEMENT_IDS)
        self.assertGreaterEqual(len(errors), 4)

    def test_wcs_internal_references_are_checked(self):
        import yaml
        data = {
            "controller_definitions": [{"id": "CTRL"}],
            "reporting_points": [{"id": "RP", "controller": "MISSING"}],
            "equipment": [{
                "id": "EQ", "type": "missing", "mode": "wms_controlled",
                "controller": "MISSING", "served_points": ["UNKNOWN"],
            }],
            "telegram_actions": [{"telegram_type": "X", "action": "go", "to_point": "UNKNOWN"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wcs.yaml"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            errors = check_wcs_refs(path, ELEMENT_IDS)
        self.assertGreaterEqual(len(errors), 5)

    def test_missing_segment_and_trigger_are_checked(self):
        import yaml
        movement = {"movement_rules": [{
            "id": "R", "allowed": True,
            "from": {"storage_type": "A"}, "to": {"storage_type": "B"},
            "via_segment": "MISSING", "trigger": "missing",
        }]}
        storage = {"storage_types": [{"id": "A"}, {"id": "B"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movement_rules.yaml"
            path.write_text(yaml.safe_dump(movement), encoding="utf-8")
            errors = check_movement_rule_refs(path, storage, {}, {}, ELEMENT_IDS)
        self.assertTrue(any("via_segment" in error for error in errors))
        self.assertTrue(any("trigger" in error for error in errors))

    def test_shared_topology_namespace_rejects_ambiguous_ids(self):
        errors = check_topology_id_uniqueness(
            PATH,
            {"storage_types": [{"id": "DUP"}], "work_centers": [{"id": "DUP"}]},
            {},
        )
        self.assertEqual(len(errors), 1)

    def test_generator_coordinates_and_exception_targets_are_checked(self):
        storage = {"storage_types": [{
            "id": "A",
            "storage_point_generator": {
                "aisles": 2, "stacks": 1, "levels": 1,
                "coordinate_pattern": "CONSTANT",
            },
            "exceptions": [{"coordinate": "UNKNOWN"}],
        }]}
        errors = check_storage_coordinate_integrity(PATH, storage, {})
        self.assertTrue(any("duplicate compiled storage_point id" in error for error in errors))
        self.assertTrue(any("does not match a generated storage point" in error for error in errors))


class GraphReachabilityTests(unittest.TestCase):
    def test_directed_segment_cannot_be_used_backwards(self):
        movement = {"movement_rules": [{
            "id": "BACKWARDS", "allowed": True, "execution": "automated",
            "from": {"reporting_point": "B"}, "to": {"reporting_point": "A"},
        }]}
        lanes = {"conveyor_segments": [{"id": "AB", "from": "A", "to": "B"}]}
        errors = check_graph_reachability(PATH, movement, {}, {}, lanes)
        self.assertTrue(any("no directed topology path" in error for error in errors))

    def test_controller_boundary_is_an_implicit_reachable_graph(self):
        movement = {"movement_rules": [{
            "id": "STORE", "allowed": True, "execution": "automated",
            "from": {"reporting_point": "RP"}, "to": {"storage_type": "GRID"},
        }]}
        storage = {"storage_types": [{"id": "GRID", "controller": "CTRL"}]}
        wcs = {"reporting_points": [{"id": "RP", "controller": "CTRL"}]}
        self.assertEqual(check_graph_reachability(PATH, movement, storage, wcs, {}), [])

    def test_via_segment_must_match_rule_endpoints(self):
        movement = {"movement_rules": [{
            "id": "WRONG_SEGMENT", "allowed": True,
            "from": {"reporting_point": "A"}, "to": {"reporting_point": "C"},
            "via_segment": "AB",
        }]}
        lanes = {"conveyor_segments": [{"id": "AB", "from": "A", "to": "B"}]}
        errors = check_graph_reachability(PATH, movement, {}, {}, lanes)
        self.assertTrue(any("does not match the rule endpoints" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
