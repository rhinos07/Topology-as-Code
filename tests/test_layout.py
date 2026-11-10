"""Physical layout: warehouse.layout, storage_type.footprint and the position
of doors, work centers and reporting points - all optional."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import normalize_quantities  # noqa: E402
from validate import check_layout, make_validator  # noqa: E402

STORAGE = Path("storage.yaml")
WCS = Path("wcs.yaml")


def m(value):
    return {"value": value, "unit": "m"}


def pos(x, y):
    return {"x": m(x), "y": m(y)}


def footprint(x, y, width, depth):
    return {"position": pos(x, y), "width": m(width), "depth": m(depth)}


def hall(width=60, depth=40):
    return {"warehouse": {"layout": {"width": m(width), "depth": m(depth)}}}


def errors(warehouse=None, storage=None, wcs=None):
    return check_layout(Path("warehouse.yaml"), warehouse, STORAGE, storage or {}, WCS, wcs or {})


class LayoutCheckTest(unittest.TestCase):
    def test_no_layout_fields_pass(self):
        self.assertEqual(errors(storage={"storage_types": [{"id": "A"}], "doors": [{"id": "D1"}]}), [])

    def test_everything_inside_passes(self):
        self.assertEqual(errors(
            hall(),
            {
                "storage_types": [
                    {"id": "A", "footprint": footprint(0, 0, 10, 20)},
                    {"id": "B", "footprint": footprint(10, 0, 10, 20)},  # shares an edge with A
                ],
                "doors": [{"id": "D1", "position": pos(30, 40)}],  # on the wall
                "work_centers": [{"id": "WC1", "position": pos(0, 0)}],  # the reference corner
            },
            {"reporting_points": [{"id": "RP1", "position": pos(59.5, 1)}]},
        ), [])

    def test_footprint_outside_hall(self):
        [error] = errors(hall(), {"storage_types": [{"id": "A", "footprint": footprint(55, 0, 10, 5)}]})
        self.assertIn("storage_type 'A': footprint", error)
        self.assertIn("lies outside warehouse.layout (60 m x 40 m)", error)

    def test_footprints_overlap(self):
        [error] = errors(storage={"storage_types": [
            {"id": "A", "footprint": footprint(0, 0, 10, 10)},
            {"id": "B", "footprint": footprint(9, 9, 5, 5)},
        ]})
        self.assertIn("storage_type 'B': footprint overlaps the footprint of storage_type 'A'", error)

    def test_positions_outside_hall(self):
        found = errors(
            hall(),
            {"doors": [{"id": "D1", "position": pos(61, 0)}], "work_centers": [{"id": "WC1", "position": pos(1, 41)}]},
            {"reporting_points": [{"id": "RP1", "position": pos(70, 70)}]},
        )
        self.assertEqual(len(found), 3)
        self.assertTrue(found[0].startswith("storage.yaml: door 'D1': position (61 m, 0 m) lies outside"))
        self.assertTrue(found[2].startswith("wcs.yaml: reporting_point 'RP1'"))

    def test_without_hall_only_overlap_is_checked(self):
        self.assertEqual(errors(storage={
            "storage_types": [{"id": "A", "footprint": footprint(500, 500, 10, 10)}],
            "doors": [{"id": "D1", "position": pos(900, 0)}],
        }), [])

    def test_point_position_outside_its_footprint(self):
        storage = {"storage_types": [{
            "id": "A", "footprint": footprint(2, 2, 10, 10),
            "exceptions": [
                {"coordinate": "01-01-01", "position": pos(3, 3)},
                {"coordinate": "01-01-02", "position": pos(20, 3)},
            ],
            "storage_points": [{"coordinate": "P1", "position": pos(1, 1)}],
        }]}
        found = errors(storage=storage)
        self.assertEqual(len(found), 2)
        self.assertIn("exception '01-01-02': position (20 m, 3 m) lies outside the storage_type's footprint", found[0])
        self.assertIn("storage_point 'P1'", found[1])

    def test_units_are_converted(self):
        storage = {"storage_types": [{"id": "A", "footprint": {
            "position": {"x": {"value": 5900, "unit": "cm"}, "y": m(0)},
            "width": {"value": 2000, "unit": "mm"}, "depth": m(1),
        }}]}
        self.assertEqual(len(errors(hall(), storage)), 1)  # 59 m + 2 m > 60 m


class SchemaTest(unittest.TestCase):
    def count(self, schema, data):
        return len(list(make_validator(schema).iter_errors(data)))

    def storage(self, **extra):
        return {"storage_types": [{
            "id": "A", "access_model": "block",
            "storage_points": [{"coordinate": "P0"}], **extra,
        }]}

    def test_footprint_accepted(self):
        self.assertEqual(self.count("storage.schema.json", self.storage(footprint=footprint(0, 0, 10, 5))), 0)

    def test_footprint_needs_position_width_and_depth(self):
        self.assertTrue(self.count("storage.schema.json", self.storage(footprint={"position": pos(0, 0), "width": m(10)})))

    def test_footprint_width_must_be_positive(self):
        self.assertTrue(self.count("storage.schema.json", self.storage(footprint=footprint(0, 0, 0, 5))))

    def test_position_may_be_zero_but_not_negative(self):
        door = {"id": "D1", "direction": "inbound", "staging_section": "A.S1"}
        self.assertEqual(self.count("door.schema.json", {**door, "position": pos(0, 0)}), 0)
        self.assertTrue(self.count("door.schema.json", {**door, "position": pos(-1, 0)}))
        self.assertTrue(self.count("door.schema.json", {**door, "position": {"x": m(1)}}))

    def test_work_center_and_reporting_point_position(self):
        storage = {**self.storage(), "work_centers": [{"id": "WC1", "step": "packing", "position": pos(1, 2)}]}
        self.assertEqual(self.count("storage.schema.json", storage), 0)
        wcs = {"reporting_points": [{"id": "RP1", "controller": "C1", "position": {**pos(1, 2), "z": m(0)}}]}
        self.assertEqual(self.count("wcs.schema.json", wcs), 0)

    def test_storage_point_position_still_accepted(self):
        storage = self.storage(storage_points=[{"coordinate": "P1", "position": {**pos(4.8, 12), "z": m(3.6)}}])
        self.assertEqual(self.count("storage.schema.json", storage), 0)


class CompileTest(unittest.TestCase):
    def test_layout_quantities_are_normalized_to_metres(self):
        entity = normalize_quantities({"id": "A", "footprint": {
            "position": {"x": {"value": 250, "unit": "cm"}, "y": m(0)},
            "width": {"value": 12000, "unit": "mm"}, "depth": m(4),
        }})
        self.assertEqual(entity["footprint"]["position"], {"x": m(2.5), "y": m(0)})
        self.assertEqual(entity["footprint"]["width"], m(12))


if __name__ == "__main__":
    unittest.main()
