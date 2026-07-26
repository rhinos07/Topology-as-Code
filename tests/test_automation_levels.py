"""Tests for automation_level as a catalog reference.

automation_level used to be a closed enum repeated in three schemas, with
its behavioural consequences hardcoded as JSON Schema conditionals. Each
catalog entry now states those consequences itself. The rules are
unchanged - these tests pin that, and that a new level needs no schema
edit. See Warehouse-as-Code ADR-0001, measure 3.
"""

import sys
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate import (  # noqa: E402
    check_automation_level_consequences,
    check_automation_level_refs,
    load_automation_levels,
)

PATH = Path("storage.yaml")
LEVELS = {
    "manual": {"id": "manual", "automated": False, "movement_policy": "default_allow"},
    "auto": {
        "id": "auto", "automated": True, "movement_policy": "explicit_only",
        "requires_movement_policy": True,
    },
    "mixed": {"id": "mixed", "automated": True},
}


def _storage(**overrides) -> dict:
    st = {"id": "A", "automation_level": "manual", "movement_policy": "default_allow"}
    st.update(overrides)
    return {"storage_types": [st]}


class AutomationLevelConsequenceTests(unittest.TestCase):
    def test_unknown_level_is_reported(self):
        errors = check_automation_level_consequences(
            PATH, _storage(automation_level="nope"), LEVELS
        )
        self.assertTrue(any("not found in elements/automation_levels.yaml" in e for e in errors))

    def test_unknown_level_is_reported_once_not_per_rule(self):
        """An unknown level has nothing to look up, so the other rules stay quiet."""
        errors = check_automation_level_consequences(
            PATH, _storage(automation_level="nope", controller="CTRL"), LEVELS
        )
        self.assertEqual(len(errors), 1)

    def test_mismatched_movement_policy_is_reported(self):
        errors = check_automation_level_consequences(
            PATH, _storage(automation_level="auto", movement_policy="default_allow"), LEVELS
        )
        self.assertTrue(any("implies movement_policy 'explicit_only'" in e for e in errors))

    def test_matching_movement_policy_is_accepted(self):
        errors = check_automation_level_consequences(
            PATH, _storage(automation_level="auto", movement_policy="explicit_only"), LEVELS
        )
        self.assertEqual(errors, [])

    def test_required_movement_policy_must_be_stated(self):
        st = _storage(automation_level="auto")
        del st["storage_types"][0]["movement_policy"]
        errors = check_automation_level_consequences(PATH, st, LEVELS)
        self.assertTrue(any("requires movement_policy" in e for e in errors))

    def test_implied_policy_may_be_omitted_when_not_required(self):
        """'manual' constrains movement_policy if stated but does not force it -
        preserving exactly what the old JSON Schema conditional did."""
        st = _storage(automation_level="manual")
        del st["storage_types"][0]["movement_policy"]
        self.assertEqual(check_automation_level_consequences(PATH, st, LEVELS), [])

    def test_controller_requires_an_automated_level(self):
        errors = check_automation_level_consequences(
            PATH, _storage(automation_level="manual", controller="CTRL"), LEVELS
        )
        self.assertTrue(any("controller requires an automated" in e for e in errors))

    def test_controller_on_automated_level_is_accepted(self):
        errors = check_automation_level_consequences(
            PATH,
            _storage(automation_level="auto", movement_policy="explicit_only", controller="CTRL"),
            LEVELS,
        )
        self.assertEqual(errors, [])

    def test_level_without_policy_consequence_accepts_either(self):
        for policy in ("default_allow", "explicit_only"):
            errors = check_automation_level_consequences(
                PATH, _storage(automation_level="mixed", movement_policy=policy), LEVELS
            )
            self.assertEqual(errors, [], policy)

    def test_storage_type_without_level_is_skipped(self):
        st = _storage()
        del st["storage_types"][0]["automation_level"]
        self.assertEqual(check_automation_level_consequences(PATH, st, LEVELS), [])


class AutomationLevelReferenceTests(unittest.TestCase):
    def test_unknown_reference_is_reported(self):
        errors = check_automation_level_refs(PATH, "nope", "warehouse", LEVELS)
        self.assertTrue(any("automation_level 'nope'" in e for e in errors))

    def test_known_reference_is_accepted(self):
        self.assertEqual(check_automation_level_refs(PATH, "manual", "warehouse", LEVELS), [])

    def test_absent_reference_is_accepted(self):
        self.assertEqual(check_automation_level_refs(PATH, None, "warehouse", LEVELS), [])


class ShippedCatalogTests(unittest.TestCase):
    def test_catalog_loads_and_ids_match(self):
        levels = load_automation_levels()
        self.assertTrue(levels)
        for level_id, level in levels.items():
            self.assertEqual(level_id, level["id"])
            self.assertIn("automated", level)

    def test_every_referenced_level_exists(self):
        """Covers the whole customers/ tree plus the equipment catalog, not
        just the two example buildings."""
        known = set(load_automation_levels())
        used: list[tuple[Path, str]] = []

        for path in (REPO_ROOT / "customers").rglob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for st in data.get("storage_types", []):
                if st.get("automation_level"):
                    used.append((path, st["automation_level"]))
            level = (data.get("warehouse") or {}).get("automation_level")
            if level:
                used.append((path, level))

        equipment = yaml.safe_load(
            (REPO_ROOT / "elements" / "equipment_types.yaml").read_text(encoding="utf-8")
        ) or {}
        for item in equipment.get("equipment_types", []):
            if item.get("automation_level"):
                used.append((REPO_ROOT / "elements" / "equipment_types.yaml", item["automation_level"]))

        self.assertTrue(used)
        for path, level in used:
            self.assertIn(level, known, f"{path}: '{level}'")

    def test_controller_managed_areas_do_not_claim_a_conveyor(self):
        """The concrete falsehood that motivated measure 3: an AutoStore grid
        has no conveying equipment, so it must not declare conveyor_automated."""
        for path in (REPO_ROOT / "customers").rglob("structure/storage.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for st in data.get("storage_types", []):
                if "AUTOSTORE" in st.get("id", ""):
                    self.assertNotEqual(
                        st.get("automation_level"), "conveyor_automated",
                        f"{path}: storage_type '{st.get('id')}'",
                    )


if __name__ == "__main__":
    unittest.main()
