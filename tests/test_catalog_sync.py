"""Tests for the pinned MasterData-as-Code catalogs (way A of
Warehouse-as-Code backlog C1): load_unit_types.yaml and
storage_conditions.yaml are byte-identical copies pinned to a source
commit, and storage_type.storage_condition is checked against them.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from sync_catalogs import check, load_sources, sha256, update  # noqa: E402
from validate import _check_storage_condition, check_replenishment_refs  # noqa: E402

ELEMENTS = REPO_ROOT / "elements"


class PinnedCopiesTest(unittest.TestCase):
    def test_committed_copies_match_their_pin(self):
        self.assertEqual(check(load_sources(), ELEMENTS, None), [])

    def test_all_pins_share_one_source_commit(self):
        refs = {entry["ref"] for entry in load_sources()["catalog_sources"]}
        self.assertEqual(len(refs), 1)

    def test_edited_copy_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            elements = Path(tmp)
            for entry in load_sources()["catalog_sources"]:
                shutil.copy(ELEMENTS / entry["file"], elements / entry["file"])
            (elements / "storage_conditions.yaml").write_text("storage_conditions: []\n", encoding="utf-8")
            errors = check(load_sources(), elements, None)
        self.assertTrue(any("do not edit the copy" in e for e in errors))

    def test_update_copies_and_repins(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_root, elements = Path(tmp) / "src", Path(tmp) / "elements"
            (source_root / "elements").mkdir(parents=True)
            elements.mkdir()
            sources = {"catalog_sources": [{
                "file": "storage_conditions.yaml", "repo": "x/y", "ref": "old",
                "path": "elements/storage_conditions.yaml", "sha256": "old",
            }]}
            source_file = source_root / "elements" / "storage_conditions.yaml"
            source_file.write_text("storage_conditions: []\n", encoding="utf-8")
            updated = update(sources, elements, source_root, "new-ref")
            self.assertEqual(updated["catalog_sources"][0]["ref"], "new-ref")
            self.assertEqual(updated["catalog_sources"][0]["sha256"], sha256(source_file))
            self.assertEqual(check(updated, elements, source_root), [])


class StorageConditionCheckTest(unittest.TestCase):
    def errors(self, **attrs):
        return _check_storage_condition(Path("storage.yaml"), "ST", attrs)

    def test_matching_range_passes(self):
        self.assertEqual(self.errors(storage_condition="chilled", temperature_zone={"min": 2, "max": 8, "unit": "C"}), [])

    def test_range_outside_class_fails(self):
        errors = self.errors(storage_condition="chilled", temperature_zone={"min": 0, "max": 8, "unit": "C"})
        self.assertTrue(any("lies outside storage_condition 'chilled'" in e for e in errors))

    def test_ambient_string_must_match_ambient_class(self):
        self.assertEqual(self.errors(storage_condition="ambient", temperature_zone="ambient"), [])
        self.assertTrue(self.errors(storage_condition="chilled", temperature_zone="ambient"))

    def test_unknown_class_fails(self):
        self.assertTrue(any("not found in elements/storage_conditions.yaml" in e for e in self.errors(storage_condition="tropical")))

    def test_condition_without_zone_passes(self):
        self.assertEqual(self.errors(storage_condition="frozen"), [])


class UomReferenceTest(unittest.TestCase):
    STORAGE = {"storage_types": [{"id": "HBR"}, {"id": "FIX"}], "activity_areas": []}

    def errors(self, from_uom, to_uom):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replenishment.yaml"
            path.write_text(yaml.safe_dump({"replenishment_strategies": [{
                "id": "R", "source": {"storage_type": "HBR"}, "destination": {"storage_type": "FIX"},
                "unit_conversion": {"from_uom": from_uom, "to_uom": to_uom},
            }]}), encoding="utf-8")
            return check_replenishment_refs(path, self.STORAGE)

    def test_masterdata_uoms_pass(self):
        self.assertEqual(self.errors("pallet", "each"), [])

    def test_unknown_uom_fails(self):
        # 'piece' was the drift D1a: MasterData-as-Code calls the unit 'each'.
        self.assertTrue(any("to_uom 'piece' not found" in e for e in self.errors("pallet", "piece")))


if __name__ == "__main__":
    unittest.main()
