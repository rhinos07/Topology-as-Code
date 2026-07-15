import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import build_import_artifact  # noqa: E402
from plan import build_plan, load_artifact  # noqa: E402


def warehouse_data(revision="1", removal_policy="deactivate"):
    return {
        "api_version": "warehouse-as-code/v1",
        "metadata": {"dataset_id": "tenant/site/building", "revision": revision},
        "target": {"wms_type": "generic", "tenant": "tenant", "facility": "site", "building": "building"},
        "import_policy": {
            "mode": "reconcile", "removal_policy": removal_policy,
            "unmanaged_objects": "preserve", "require_plan_approval": True,
        },
    }


class ArtifactTests(unittest.TestCase):
    def test_hash_is_order_independent_and_ignores_revision(self):
        points = [
            {"id": "B", "max_weight": {"value": 2, "unit": "kg"}},
            {"id": "A", "max_weight": {"value": 1000, "unit": "g"}},
        ]
        first = build_import_artifact(warehouse_data("1"), points)
        second = build_import_artifact(warehouse_data("2"), list(reversed(points)))
        self.assertEqual(first["artifact"]["content_hash"], second["artifact"]["content_hash"])
        self.assertEqual(
            [point["id"] for point in first["entities"]["storage_point"]],
            ["A", "B"],
        )
        self.assertEqual(
            first["entities"]["storage_point"][0]["max_weight"],
            {"value": 1, "unit": "kg"},
        )

    def test_non_storage_entity_changes_artifact_hash(self):
        current_entities = {
            "storage_point": [{"id": "A"}],
            "movement_rule": [{"id": "R", "allowed": True}],
        }
        desired_entities = {
            "storage_point": [{"id": "A"}],
            "movement_rule": [{"id": "R", "allowed": False, "reason": "closed"}],
        }
        current = build_import_artifact(warehouse_data(), [{"id": "A"}], entities=current_entities)
        desired = build_import_artifact(warehouse_data("2"), [{"id": "A"}], entities=desired_entities)
        self.assertNotEqual(
            current["artifact"]["content_hash"], desired["artifact"]["content_hash"]
        )
        plan = build_plan(current, desired, Path("current.yaml"), Path("desired.yaml"))
        self.assertEqual(plan["summary"]["update"], 1)
        self.assertEqual(plan["updates"][0]["entity"], "movement_rule")

    def test_equivalent_units_produce_the_same_hash(self):
        grams = build_import_artifact(
            warehouse_data(),
            [{"id": "A", "max_weight": {"value": 1000, "unit": "g"}}],
        )
        kilograms = build_import_artifact(
            warehouse_data(),
            [{"id": "A", "max_weight": {"value": 1, "unit": "kg"}}],
        )
        self.assertEqual(
            grams["artifact"]["content_hash"],
            kilograms["artifact"]["content_hash"],
        )


class PlanTests(unittest.TestCase):
    def test_plan_classifies_create_update_and_deactivation(self):
        current = build_import_artifact(warehouse_data(), [{"id": "A", "max_weight": {"value": 1, "unit": "kg"}}, {"id": "REMOVED"}])
        desired = build_import_artifact(warehouse_data("2"), [{"id": "A", "max_weight": {"value": 2, "unit": "kg"}}, {"id": "NEW"}])
        plan = build_plan(current, desired, Path("current.yaml"), Path("desired.yaml"))
        self.assertEqual(plan["summary"], {
            "create": 1, "update": 1, "deactivate": 1, "conflict": 0,
            "extension_create": 0, "extension_update": 0,
            "extension_remove": 0, "unchanged": 0,
        })
        self.assertEqual(plan["updates"][0]["classification"], "operational")
        self.assertEqual(plan["deactivations"][0]["classification"], "destructive")

    def test_reject_policy_turns_removal_into_conflict(self):
        current = build_import_artifact(warehouse_data(), [{"id": "A"}])
        desired = build_import_artifact(warehouse_data("2", "reject"), [])
        plan = build_plan(current, desired, Path("current.yaml"), Path("desired.yaml"))
        self.assertEqual(plan["summary"]["conflict"], 1)
        self.assertEqual(plan["summary"]["deactivate"], 0)

    def test_different_dataset_is_rejected(self):
        current = build_import_artifact(warehouse_data(), [])
        desired_data = warehouse_data("2")
        desired_data["metadata"]["dataset_id"] = "other/site/building"
        desired = build_import_artifact(desired_data, [])
        with self.assertRaisesRegex(ValueError, "dataset_id mismatch"):
            build_plan(current, desired, Path("current.yaml"), Path("desired.yaml"))

    def test_tampered_artifact_is_rejected(self):
        import tempfile
        import yaml

        artifact = build_import_artifact(warehouse_data(), [{"id": "A"}])
        artifact["entities"]["storage_point"][0]["max_weight"] = {"value": 999, "unit": "kg"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.yaml"
            path.write_text(yaml.safe_dump(artifact), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "content_hash mismatch"):
                load_artifact(path)


if __name__ == "__main__":
    unittest.main()
