"""Tests for scan_codes.yaml - the explicitly maintained check code per
storage_point. There is no generation rule; the tests pin the validation
(schema, uniqueness, existing storage_point), the compile step and the
plan classification.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import apply_scan_codes  # noqa: E402
from plan import OPERATIONAL_FIELDS  # noqa: E402
from validate import (  # noqa: E402
    check_scan_code_refs, collect_element_ids, validate_scan_codes, validate_warehouse_file,
)

HALL_3 = REPO_ROOT / "customers" / "example_customer" / "facilities" / "facility_pa11" / "buildings" / "hall_3"
STORAGE = yaml.safe_load((HALL_3 / "structure" / "storage.yaml").read_text(encoding="utf-8"))
WCS = yaml.safe_load((HALL_3 / "structure" / "wcs.yaml").read_text(encoding="utf-8"))


class ScanCodeTest(unittest.TestCase):
    def _write(self, entries) -> Path:
        directory = tempfile.mkdtemp()
        path = Path(directory) / "scan_codes.yaml"
        path.write_text(yaml.safe_dump({"scan_codes": entries}), encoding="utf-8")
        return path

    def test_hall_3_example_is_valid(self):
        self.assertEqual(validate_warehouse_file(HALL_3 / "warehouse.yaml", collect_element_ids()), [])

    def test_valid_entries_pass(self):
        path = self._write([
            {"storage_point": "FIX.FIX-01-01", "code": "73"},
            {"storage_point": "FIX.FIX-01-02", "code": "18"},
        ])
        self.assertEqual(validate_scan_codes(path), [])
        self.assertEqual(check_scan_code_refs(path, STORAGE, WCS), [])

    def test_code_with_space_is_rejected(self):
        path = self._write([{"storage_point": "FIX.FIX-01-01", "code": "7 3"}])
        self.assertTrue(validate_scan_codes(path))

    def test_duplicate_code_is_rejected(self):
        path = self._write([
            {"storage_point": "FIX.FIX-01-01", "code": "73"},
            {"storage_point": "FIX.FIX-01-02", "code": "73"},
        ])
        errors = validate_scan_codes(path)
        self.assertTrue(any("used by both" in error for error in errors), errors)

    def test_two_codes_for_one_point_are_rejected(self):
        path = self._write([
            {"storage_point": "FIX.FIX-01-01", "code": "73"},
            {"storage_point": "FIX.FIX-01-01", "code": "18"},
        ])
        errors = validate_scan_codes(path)
        self.assertTrue(any("more than one" in error for error in errors), errors)

    def test_unknown_storage_point_is_rejected(self):
        path = self._write([{"storage_point": "FIX.FIX-99-99", "code": "73"}])
        errors = check_scan_code_refs(path, STORAGE, WCS)
        self.assertTrue(any("FIX.FIX-99-99" in error for error in errors), errors)

    def test_compile_copies_code_only_onto_listed_points(self):
        points = [{"id": "FIX.FIX-01-01"}, {"id": "FIX.FIX-01-02"}]
        errors = apply_scan_codes(points, {"scan_codes": [
            {"storage_point": "FIX.FIX-01-01", "code": "73"},
        ]})
        self.assertEqual(errors, [])
        self.assertEqual(points[0]["scan_code"], "73")
        self.assertNotIn("scan_code", points[1])

    def test_scan_code_change_is_operational(self):
        # A changed code means relabelling the location on the floor.
        self.assertIn("scan_code", OPERATIONAL_FIELDS)


if __name__ == "__main__":
    unittest.main()
