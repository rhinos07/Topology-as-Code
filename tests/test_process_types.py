"""Tests for the process_types catalog - the movement-triggering processes
movement_rule.trigger and OrderOrchestration-as-Code's workflow_trigger
reference. Pins the full set, so removing a type (which would break rules
in sibling repos) is a deliberate test change, not an accident.
"""

import sys
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate import make_validator  # noqa: E402

CATALOG = yaml.safe_load((REPO_ROOT / "elements" / "process_types.yaml").read_text(encoding="utf-8"))
HALL_3_RULES = REPO_ROOT / "customers" / "example_customer" / "facilities" / "facility_pa11" / "buildings" / "hall_3" / "strategies" / "movement_rules.yaml"

EXPECTED = {
    "putaway_task": "inbound",
    "goods_receipt": "inbound",
    "returns_receipt": "inbound",
    "pick_task": "outbound",
    "pack_task": "outbound",
    "load_task": "outbound",
    "replenishment_task": "internal_movement",
    "bin_return": "internal_movement",
    "bin_provision": "internal_movement",
    "clarification": "internal_movement",
    "quality_inspection": "internal_movement",
    "relocation": "internal_movement",
    "count_task": "internal_movement",
    "cross_dock_task": "cross_dock",
}


class ProcessTypeCatalogTest(unittest.TestCase):
    def test_every_entry_matches_the_schema(self):
        validator = make_validator("process-type.schema.json")
        for entry in CATALOG["process_types"]:
            with self.subTest(id=entry.get("id")):
                self.assertEqual(list(validator.iter_errors(entry)), [])

    def test_catalog_is_exactly_the_expected_set(self):
        actual = {entry["id"]: entry["category"] for entry in CATALOG["process_types"]}
        self.assertEqual(actual, EXPECTED)

    def test_ids_are_unique(self):
        ids = [entry["id"] for entry in CATALOG["process_types"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_returns_door_has_a_rule(self):
        # DOOR_03 (direction: returns) feeds SEC_STAGING_RETURNS; without a
        # returns_receipt rule that staging section was unreachable by any
        # process type.
        rules = yaml.safe_load(HALL_3_RULES.read_text(encoding="utf-8"))["movement_rules"]
        triggers = {rule.get("trigger") for rule in rules}
        self.assertIn("returns_receipt", triggers)
        self.assertIn("goods_receipt", triggers)


if __name__ == "__main__":
    unittest.main()
