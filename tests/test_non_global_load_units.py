"""Load unit ids outside the pinned global catalog are warnings, not
errors: they may be customer carriers from MasterData-as-Code
customers/<c>/load_unit_types.yaml, which Warehouse-as-Code checks per
site."""

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import validate  # noqa: E402
from validate import check_storage_refs, collect_element_ids  # noqa: E402


class NonGlobalLoadUnitTests(unittest.TestCase):
    def setUp(self):
        validate.WARNINGS.clear()

    def test_autostore_heights_in_pinned_catalog(self):
        ids = collect_element_ids()["load_unit_types"]
        self.assertTrue({"autostore_bin_220", "autostore_bin_330", "autostore_bin_425"} <= ids)
        self.assertNotIn("autostore_bin", ids)

    def test_storage_point_customer_carrier_is_warning(self):
        storage = {"storage_types": [{
            "id": "A", "access_model": "rack",
            "storage_points": [{"coordinate": "P1", "allowed_load_unit_types": ["acme_crate"]}],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.yaml"
            path.write_text(yaml.safe_dump(storage), encoding="utf-8")
            errors = check_storage_refs(path, set(), collect_element_ids())
        self.assertEqual([e for e in errors if "acme_crate" in e], [])
        self.assertTrue(any("acme_crate" in w and "site level" in w for w in validate.WARNINGS))

    def test_global_carrier_gives_no_warning(self):
        storage = {"storage_types": [{
            "id": "A", "access_model": "rack",
            "default_attributes": {"allowed_load_unit_types": ["pallet_euro"]},
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.yaml"
            path.write_text(yaml.safe_dump(storage), encoding="utf-8")
            check_storage_refs(path, set(), collect_element_ids())
        self.assertEqual(validate.WARNINGS, [])


if __name__ == "__main__":
    unittest.main()
