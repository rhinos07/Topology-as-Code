"""Tests for storage_type.technology - which KIND OF SYSTEM fulfils an area.

Deliberately distinct from automation_level (how an area is operated); the
two are independent, and test_technology_is_independent_of_automation_level
pins that with the case that motivated the split. The catalog is the anchor
for a vocabulary Allocation-as-Code and OrderOrchestration-as-Code had no
shared definition for - see WMS-POC Finding #8.
"""

import sys
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import derive_activity_area_technology  # noqa: E402
from validate import check_storage_refs, load_yaml  # noqa: E402

ELEMENT_IDS = {"storage_technologies": {"autostore", "manual_warehouse"}}


def _catalog_ids() -> set[str]:
    data = load_yaml(REPO_ROOT / "elements" / "storage_technologies.yaml") or {}
    return {item["id"] for item in data.get("storage_technologies", [])}


class ActivityAreaDerivationTests(unittest.TestCase):
    def test_single_source_technology_is_derived(self):
        areas = derive_activity_area_technology({
            "storage_types": [{"id": "FIX", "technology": "manual_warehouse"}],
            "activity_areas": [{"id": "PICK", "bins_from": ["FIX"]}],
        })
        self.assertEqual(areas[0]["technology"], "manual_warehouse")

    def test_section_qualified_reference_resolves_to_its_storage_type(self):
        areas = derive_activity_area_technology({
            "storage_types": [{"id": "HBR", "technology": "shuttle"}],
            "activity_areas": [{"id": "PUTAWAY", "bins_from": ["HBR.SEC_FAST", "HBR.SEC_SLOW"]}],
        })
        self.assertEqual(areas[0]["technology"], "shuttle")

    def test_mixed_sources_yield_no_technology_and_no_error(self):
        """An activity_area spanning technologies is legitimate - INVENTORY_ZONE_A
        really does cover HBR and BLK - so it gets no technology rather than
        being rejected. A consumer needing one has to say so itself."""
        areas = derive_activity_area_technology({
            "storage_types": [
                {"id": "HBR", "technology": "shuttle"},
                {"id": "BLK", "technology": "manual_warehouse"},
            ],
            "activity_areas": [{"id": "INVENTORY", "bins_from": ["HBR.SEC_SLOW", "BLK"]}],
        })
        self.assertNotIn("technology", areas[0])

    def test_storage_type_without_technology_yields_none(self):
        areas = derive_activity_area_technology({
            "storage_types": [{"id": "FIX"}],
            "activity_areas": [{"id": "PICK", "bins_from": ["FIX"]}],
        })
        self.assertNotIn("technology", areas[0])

    def test_other_activity_area_fields_are_preserved(self):
        areas = derive_activity_area_technology({
            "storage_types": [{"id": "FIX", "technology": "manual_warehouse"}],
            "activity_areas": [{"id": "PICK", "activity": "picking", "bins_from": ["FIX"]}],
        })
        self.assertEqual(areas[0]["activity"], "picking")
        self.assertEqual(areas[0]["bins_from"], ["FIX"])

    def test_source_data_is_not_mutated(self):
        storage_data = {
            "storage_types": [{"id": "FIX", "technology": "manual_warehouse"}],
            "activity_areas": [{"id": "PICK", "bins_from": ["FIX"]}],
        }
        derive_activity_area_technology(storage_data)
        self.assertNotIn("technology", storage_data["activity_areas"][0])


class TechnologyReferenceTests(unittest.TestCase):
    def _errors(self, storage: dict) -> list[str]:
        return check_storage_refs(Path("storage.yaml"), set(), ELEMENT_IDS, storage)

    def test_unknown_technology_is_reported(self):
        errors = self._errors({"storage_types": [{"id": "A", "technology": "teleporter"}]})
        self.assertTrue(any("technology 'teleporter'" in e for e in errors))

    def test_known_technology_is_accepted(self):
        errors = self._errors({"storage_types": [{"id": "A", "technology": "autostore"}]})
        self.assertEqual([e for e in errors if "technology" in e], [])

    def test_absent_technology_is_accepted(self):
        errors = self._errors({"storage_types": [{"id": "A"}]})
        self.assertEqual([e for e in errors if "technology" in e], [])


class ShippedCatalogTests(unittest.TestCase):
    def test_every_referenced_technology_exists(self):
        known = _catalog_ids()
        self.assertTrue(known)
        for path in (REPO_ROOT / "customers").rglob("structure/storage.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for st in data.get("storage_types", []):
                technology = st.get("technology")
                if technology is not None:
                    self.assertIn(technology, known, f"{path}: storage_type '{st.get('id')}'")

    def test_technology_is_independent_of_automation_level(self):
        """The case that motivated a separate field: HBR and CHANNEL_A are both
        conveyor_automated but are different systems, and an order routed to
        one cannot be served by the other."""
        path = (
            REPO_ROOT / "customers/example_customer/facilities/facility_pa11"
            / "buildings/hall_3/structure/storage.yaml"
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        by_id = {st["id"]: st for st in data["storage_types"]}
        self.assertEqual(by_id["HBR"]["automation_level"], by_id["CHANNEL_A"]["automation_level"])
        self.assertNotEqual(by_id["HBR"]["technology"], by_id["CHANNEL_A"]["technology"])


if __name__ == "__main__":
    unittest.main()
