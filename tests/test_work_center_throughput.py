"""Work center throughput: workstation_template value with unit, optional
override per work center, and optional description fields."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import compile_work_centers  # noqa: E402
from validate import make_validator  # noqa: E402

TEMPLATES = {"ws_port": {"id": "ws_port", "type": "goods-to-person",
                         "throughput_capacity": 650, "throughput_unit": "load_units_per_hour"}}


class CompileTest(unittest.TestCase):
    def test_template_value_is_inherited(self):
        [wc] = compile_work_centers([{"id": "WC1", "step": "picking", "workstation_template": "ws_port"}], TEMPLATES)
        self.assertEqual((wc["throughput_capacity"], wc["throughput_unit"], wc["throughput_source"]),
                         (650, "load_units_per_hour", "template"))

    def test_work_center_overrides_template(self):
        [wc] = compile_work_centers([{
            "id": "WC1", "step": "picking", "workstation_template": "ws_port",
            "throughput_capacity": 450, "throughput_unit": "load_units_per_hour",
        }], TEMPLATES)
        self.assertEqual((wc["throughput_capacity"], wc["throughput_source"]), (450, "work_center"))

    def test_no_template_no_throughput(self):
        [wc] = compile_work_centers([{"id": "EXIT", "step": "exit"}], TEMPLATES)
        self.assertNotIn("throughput_capacity", wc)
        self.assertNotIn("throughput_source", wc)

    def test_shipped_templates_have_units(self):
        [wc] = compile_work_centers([{"id": "WC1", "step": "picking", "workstation_template": "ws_pick_to_light"}])
        self.assertEqual(wc["throughput_unit"], "picks_per_hour")


class SchemaTest(unittest.TestCase):
    def storage_errors(self, work_center):
        validator = make_validator("storage.schema.json")
        storage = {"storage_types": [{"id": "A", "access_model": "rack"}], "work_centers": [work_center]}
        return [e for e in validator.iter_errors(storage) if "work_centers" in list(e.absolute_path)]

    def test_capacity_needs_unit(self):
        self.assertTrue(self.storage_errors({"id": "WC1", "step": "picking", "throughput_capacity": 100}))

    def test_unit_needs_capacity(self):
        self.assertTrue(self.storage_errors({"id": "WC1", "step": "picking", "throughput_unit": "picks_per_hour"}))

    def test_unknown_unit_rejected(self):
        self.assertTrue(self.storage_errors({
            "id": "WC1", "step": "picking", "throughput_capacity": 100, "throughput_unit": "boxes",
        }))

    def test_description_and_override_accepted(self):
        self.assertEqual(self.storage_errors({
            "id": "WC1", "step": "picking", "description": "Pick port",
            "throughput_capacity": 100, "throughput_unit": "order_lines_per_hour",
        }), [])

    def test_template_capacity_needs_unit(self):
        validator = make_validator("rack-templates.schema.json")
        errors = list(validator.iter_errors({"workstation_templates": [
            {"id": "ws_x", "type": "manual", "throughput_capacity": 100},
        ]}))
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
