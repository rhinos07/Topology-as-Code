"""Tests for storage_type.routing - the static travel path basis.

Topology states only the sort key (route_aisle, route_position) and which
aisle ends are usable. The sort direction - ascending when the aisle is
entered at 'start', descending at 'end' - is decided at runtime and is not
tested here.
"""

import copy
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import compile_route_aisles, compile_storage_types  # noqa: E402
from plan import OPERATIONAL_ENTITY_TYPES, OPERATIONAL_FIELDS  # noqa: E402
from validate import check_routing, make_validator  # noqa: E402

PATH = Path("storage.yaml")
VALIDATOR = make_validator("storage-type.schema.json")

RACK = {
    "id": "RCK",
    "access_model": "rack",
    "access_order": "direct",
    "storage_point_generator": {
        "aisles": 3, "stacks": 4, "levels": 2,
        "coordinate_pattern": "{aisle:02d}-{stack:02d}-{level:02d}",
    },
    "default_attributes": {"capacity_per_point": 1},
    "routing": {
        "default_entries": ["start"],
        "aisles": [{"aisle": 2, "entries": ["start", "end"], "sequence": 0}],
    },
}


def compile_one(storage_type: dict) -> tuple[list[dict], list[dict]]:
    points, _ = compile_storage_types({"storage_types": [storage_type]})
    return points, compile_route_aisles(storage_type, points)


class RoutingCompileTest(unittest.TestCase):
    def test_schema_accepts_routing(self):
        self.assertEqual(list(VALIDATOR.iter_errors(RACK)), [])

    def test_generator_derives_aisle_and_stack(self):
        points, _ = compile_one(copy.deepcopy(RACK))
        point = next(p for p in points if p["id"] == "RCK.02-03-01")
        self.assertEqual(point["route_aisle"], "RCK.A02")
        self.assertEqual(point["route_position"], 3)

    def test_level_is_not_part_of_the_key(self):
        points, _ = compile_one(copy.deepcopy(RACK))
        low = next(p for p in points if p["id"] == "RCK.01-02-01")
        high = next(p for p in points if p["id"] == "RCK.01-02-02")
        self.assertEqual(
            (low["route_aisle"], low["route_position"]),
            (high["route_aisle"], high["route_position"]),
        )

    def test_route_aisle_entities_use_defaults_and_overrides(self):
        _, aisles = compile_one(copy.deepcopy(RACK))
        by_id = {aisle["id"]: aisle for aisle in aisles}
        self.assertEqual(sorted(by_id), ["RCK.A01", "RCK.A02", "RCK.A03"])
        self.assertEqual(by_id["RCK.A01"]["entries"], ["start"])
        self.assertEqual(by_id["RCK.A01"]["sequence"], 1)
        self.assertEqual(by_id["RCK.A02"]["entries"], ["start", "end"])
        self.assertEqual(by_id["RCK.A02"]["sequence"], 0)

    def test_exception_overrides_aisle_and_position(self):
        # Rear side of a double-sided rack: grid aisle 3 is walked from aisle 2.
        storage_type = copy.deepcopy(RACK)
        storage_type["exceptions"] = [
            {"coordinate": "03-01-01", "route_aisle": 2, "route_position": 10},
        ]
        points, aisles = compile_one(storage_type)
        point = next(p for p in points if p["id"] == "RCK.03-01-01")
        self.assertEqual((point["route_aisle"], point["route_position"]), ("RCK.A02", 10))
        self.assertIn("RCK.A03", {aisle["id"] for aisle in aisles})

    def test_without_routing_no_route_fields(self):
        storage_type = copy.deepcopy(RACK)
        del storage_type["routing"]
        points, aisles = compile_one(storage_type)
        self.assertNotIn("route_aisle", points[0])
        self.assertEqual(aisles, [])

    def test_route_changes_are_operational(self):
        self.assertTrue({"route_aisle", "route_position"} <= OPERATIONAL_FIELDS)
        self.assertIn("route_aisle", OPERATIONAL_ENTITY_TYPES)


class RoutingValidationTest(unittest.TestCase):
    def test_valid_routing_passes(self):
        self.assertEqual(check_routing(PATH, {"storage_types": [RACK]}), [])

    def test_override_without_routing_is_rejected(self):
        storage_type = copy.deepcopy(RACK)
        del storage_type["routing"]
        storage_type["exceptions"] = [{"coordinate": "01-01-01", "route_position": 5}]
        errors = check_routing(PATH, {"storage_types": [storage_type]})
        self.assertTrue(any("need a routing block" in error for error in errors), errors)

    def test_unknown_routing_aisle_is_rejected(self):
        storage_type = copy.deepcopy(RACK)
        storage_type["routing"]["aisles"].append({"aisle": 9, "entries": ["end"]})
        errors = check_routing(PATH, {"storage_types": [storage_type]})
        self.assertTrue(any("aisle 9" in error for error in errors), errors)

    def test_duplicate_routing_aisle_is_rejected(self):
        storage_type = copy.deepcopy(RACK)
        storage_type["routing"]["aisles"].append({"aisle": 2, "entries": ["end"]})
        errors = check_routing(PATH, {"storage_types": [storage_type]})
        self.assertTrue(any("listed twice" in error for error in errors), errors)

    def test_explicit_points_must_state_key(self):
        storage_type = {
            "id": "BLK1",
            "access_model": "block",
            "storage_points": [
                {"coordinate": "P1", "route_aisle": 1, "route_position": 1},
                {"coordinate": "P2"},
            ],
            "routing": {"default_entries": ["start", "end"]},
        }
        errors = check_routing(PATH, {"storage_types": [storage_type]})
        self.assertTrue(any("'P2'" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
