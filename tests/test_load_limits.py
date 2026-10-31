"""Tests for storage_type.load_limits - bay (field) and shelf load per
rack. Topology states the limits only; the runtime sums the measured
weight. These tests pin the grouping, the compiled entities and the
static consistency checks.
"""

import copy
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import compile_load_groups, compile_storage_types  # noqa: E402
from plan import OPERATIONAL_ENTITY_TYPES, OPERATIONAL_FIELDS  # noqa: E402
from validate import check_load_limits, make_validator  # noqa: E402

PATH = Path("storage.yaml")
VALIDATOR = make_validator("storage-type.schema.json")


def kg(value):
    return {"value": value, "unit": "kg"}


RACK = {
    "id": "PAL",
    "access_model": "rack",
    "access_order": "direct",
    "storage_point_generator": {
        "aisles": 2, "stacks": 6, "levels": 3,
        "coordinate_pattern": "{aisle:02d}-{stack:02d}-{level:02d}",
    },
    "default_attributes": {"capacity_per_point": 1, "max_weight": kg(1000)},
    "load_limits": {
        "positions_per_bay": 3,
        "bay_max_weight": kg(7000),
        "shelf_max_weight": kg(2500),
        "exceptions": [{"aisle": 2, "bay": 1, "bay_max_weight": kg(4000)}],
    },
}


def errors_for(storage_type: dict) -> list[str]:
    return check_load_limits(PATH, {"storage_types": [storage_type]})


class LoadLimitCompileTest(unittest.TestCase):
    def test_schema_accepts_load_limits(self):
        self.assertEqual(list(VALIDATOR.iter_errors(RACK)), [])

    def test_schema_rejects_block_storage(self):
        storage_type = copy.deepcopy(RACK)
        storage_type["access_model"] = "block"
        del storage_type["access_order"]
        self.assertTrue(list(VALIDATOR.iter_errors(storage_type)))

    def test_points_get_bay_and_shelf(self):
        points, _ = compile_storage_types({"storage_types": [copy.deepcopy(RACK)]})
        by_id = {point["id"]: point for point in points}
        # Stacks 1-3 are bay 1, stacks 4-6 bay 2.
        self.assertEqual(by_id["PAL.01-03-02"]["bay"], "PAL.F01-01")
        self.assertEqual(by_id["PAL.01-04-02"]["bay"], "PAL.F01-02")
        self.assertEqual(by_id["PAL.01-04-02"]["shelf"], "PAL.F01-02-L02")

    def test_entities_carry_effective_limits(self):
        bays, shelves = compile_load_groups(copy.deepcopy(RACK))
        self.assertEqual(len(bays), 4)        # 2 aisles x 2 bays
        self.assertEqual(len(shelves), 12)    # x 3 levels
        by_id = {bay["id"]: bay for bay in bays}
        self.assertEqual(by_id["PAL.F01-01"]["max_weight"], kg(7000))
        self.assertEqual(by_id["PAL.F02-01"]["max_weight"], kg(4000))
        self.assertEqual(shelves[0]["max_weight"], kg(2500))

    def test_missing_limit_is_left_out(self):
        storage_type = copy.deepcopy(RACK)
        del storage_type["load_limits"]["bay_max_weight"]
        storage_type["load_limits"]["exceptions"] = []
        bays, _ = compile_load_groups(storage_type)
        self.assertNotIn("max_weight", bays[0])

    def test_channel_depths_share_the_shelf(self):
        storage_type = copy.deepcopy(RACK)
        storage_type.update({
            "access_model": "channel", "channel_depth": 3,
            "entry_side": "front", "exit_side": "back",
        })
        del storage_type["access_order"]
        points, _ = compile_storage_types({"storage_types": [storage_type]})
        shelves = {p["shelf"] for p in points if p["id"].startswith("PAL.01-02-01-D")}
        self.assertEqual(shelves, {"PAL.F01-01-L01"})

    def test_changes_are_operational(self):
        self.assertTrue({"bay", "shelf"} <= OPERATIONAL_FIELDS)
        self.assertTrue({"bay", "shelf"} <= OPERATIONAL_ENTITY_TYPES)


class LoadLimitValidationTest(unittest.TestCase):
    def test_valid_limits_pass(self):
        self.assertEqual(errors_for(RACK), [])

    def test_positions_must_divide_stacks(self):
        storage_type = copy.deepcopy(RACK)
        storage_type["load_limits"]["positions_per_bay"] = 4
        self.assertTrue(any("does not divide" in e for e in errors_for(storage_type)))

    def test_shelf_above_bay_is_rejected(self):
        storage_type = copy.deepcopy(RACK)
        storage_type["load_limits"]["exceptions"] = [
            {"aisle": 1, "bay": 2, "bay_max_weight": kg(2000)},
        ]
        errors = errors_for(storage_type)
        self.assertTrue(any("aisle 1 bay 2" in e and "exceeds bay_max_weight" in e for e in errors), errors)

    def test_point_above_shelf_is_rejected(self):
        storage_type = copy.deepcopy(RACK)
        storage_type["exceptions"] = [{"coordinate": "01-02-03", "max_weight": kg(3000)}]
        errors = errors_for(storage_type)
        self.assertTrue(any("'01-02-03'" in e for e in errors), errors)

    def test_fully_loaded_shelf_above_limit_is_allowed(self):
        # 3 x 1000 kg on a 2500 kg shelf: the runtime checks the measured weight.
        self.assertEqual(errors_for(RACK), [])

    def test_unknown_or_duplicate_bay_exception_is_rejected(self):
        storage_type = copy.deepcopy(RACK)
        storage_type["load_limits"]["exceptions"] = [
            {"aisle": 9, "bay": 1, "bay_max_weight": kg(5000)},
            {"aisle": 1, "bay": 1, "bay_max_weight": kg(5000)},
            {"aisle": 1, "bay": 1, "bay_max_weight": kg(6000)},
        ]
        errors = errors_for(storage_type)
        self.assertTrue(any("names no existing bay" in e for e in errors), errors)
        self.assertTrue(any("listed twice" in e for e in errors), errors)


FLEX = {
    "id": "FLX",
    "access_model": "rack",
    "access_order": "direct",
    "layout_variants": [
        {"id": "two", "load_unit_type": "pallet_industrial", "positions_per_bay": 2,
         "coordinate_pattern": "{aisle:02d}-{bay:02d}-L{level:02d}-A{slot}"},
        {"id": "three", "load_unit_type": "pallet_euro", "positions_per_bay": 3,
         "coordinate_pattern": "{aisle:02d}-{bay:02d}-L{level:02d}-B{slot}"},
    ],
    "layout_grid": {"aisles": 1, "bays": 2, "levels": 3},
    "default_attributes": {"capacity_per_point": 1, "max_weight": kg(1000)},
    "load_limits": {"bay_max_weight": kg(9000), "shelf_max_weight": kg(3000)},
}


class LayoutVariantLoadLimitTest(unittest.TestCase):
    def test_schema_accepts_levels_and_limits(self):
        self.assertEqual(list(VALIDATOR.iter_errors(FLEX)), [])

    def test_levels_expand_and_share_shelf(self):
        points, _ = compile_storage_types({"storage_types": [copy.deepcopy(FLEX)]})
        self.assertEqual(len(points), 1 * 2 * 3 * (2 + 3))
        shelf = {p["shelf"] for p in points if p["coordinate"].startswith("01-02-L03-")}
        self.assertEqual(shelf, {"FLX.F01-02-L03"})
        point = next(p for p in points if p["coordinate"] == "01-02-L03-B1")
        self.assertEqual(point["physical_bay"], "FLX-01-02-L03")
        self.assertEqual(point["bay"], "FLX.F01-02")

    def test_entities_use_grid_bays(self):
        bays, shelves = compile_load_groups(copy.deepcopy(FLEX))
        self.assertEqual((len(bays), len(shelves)), (2, 6))

    def test_valid_flex_passes(self):
        self.assertEqual(errors_for(FLEX), [])

    def test_positions_per_bay_is_rejected(self):
        storage_type = copy.deepcopy(FLEX)
        storage_type["load_limits"]["positions_per_bay"] = 2
        self.assertTrue(any("not used with layout_variants" in e for e in errors_for(storage_type)))

    def test_point_above_shelf_is_rejected(self):
        storage_type = copy.deepcopy(FLEX)
        storage_type["load_limits"]["shelf_max_weight"] = kg(800)
        self.assertTrue(any("exceeds the shelf limit" in e for e in errors_for(storage_type)))

    def test_levels_need_level_placeholder(self):
        from validate import validate_storage_types
        import tempfile
        import yaml
        storage_type = copy.deepcopy(FLEX)
        storage_type["layout_variants"][0]["coordinate_pattern"] = "{aisle:02d}-{bay:02d}-A{slot}"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.yaml"
            path.write_text(yaml.safe_dump({"storage_types": [storage_type]}), encoding="utf-8")
            errors = validate_storage_types(path)
        self.assertTrue(any("needs {level}" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
