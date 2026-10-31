import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from compile import build_import_artifact  # noqa: E402
from plan import build_plan  # noqa: E402
from validate import make_validator, validate_extensions  # noqa: E402
from test_import_lifecycle import warehouse_data  # noqa: E402


EXTENSION_VALIDATOR = make_validator("extension.schema.json")


def extension(payload=None, entity_id="A.01", dataset_id="tenant/site/building"):
    return {
        "api_version": "topology-as-code/extension-v1",
        "extension": {
            "namespace": "com.example.wms",
            "version": "1.0",
            "dataset_id": dataset_id,
            "records": [{
                "entity_type": "storage_point",
                "entity_id": entity_id,
                "payload": payload or {"vendor_key": 42},
            }],
        },
    }


class ExtensionTests(unittest.TestCase):
    def test_payload_is_intentionally_open(self):
        value = extension({"unknown": {"nested": [1, True, "x"]}})
        self.assertEqual(list(EXTENSION_VALIDATOR.iter_errors(value)), [])

    def test_envelope_remains_closed(self):
        value = extension()
        value["extension"]["typo"] = True
        self.assertTrue(list(EXTENSION_VALIDATOR.iter_errors(value)))

    def test_dataset_and_entity_reference_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            warehouse = root / "warehouse.yaml"
            sidecar = root / "extension.yaml"
            warehouse.write_text(yaml.safe_dump(warehouse_data()), encoding="utf-8")
            sidecar.write_text(
                yaml.safe_dump(extension(entity_id="MISSING", dataset_id="wrong/dataset")),
                encoding="utf-8",
            )
            errors = validate_extensions(
                warehouse, [sidecar], {"storage_point": {"A.01"}}
            )
        self.assertTrue(any("does not match warehouse dataset_id" in error for error in errors))
        self.assertTrue(any("unknown entity" in error for error in errors))

    def test_roundtrip_hash_includes_payload_but_not_record_order(self):
        first_extension = extension({"vendor_key": 42})
        second_extension = extension({"vendor_key": 43})
        first = build_import_artifact(warehouse_data(), [{"id": "A.01"}], [first_extension])
        second = build_import_artifact(warehouse_data(), [{"id": "A.01"}], [second_extension])
        self.assertNotEqual(first["artifact"]["content_hash"], second["artifact"]["content_hash"])
        self.assertEqual(first["extensions"][0]["extension"]["records"][0]["payload"]["vendor_key"], 42)

    def test_plan_reports_vendor_specific_update(self):
        current = build_import_artifact(
            warehouse_data(), [{"id": "A.01"}], [extension({"vendor_key": 42})]
        )
        desired = build_import_artifact(
            warehouse_data("2"), [{"id": "A.01"}], [extension({"vendor_key": 43})]
        )
        plan = build_plan(current, desired, Path("current.yaml"), Path("desired.yaml"))
        self.assertEqual(plan["summary"]["extension_update"], 1)
        self.assertEqual(plan["extension_updates"][0]["classification"], "vendor_specific")

    def test_reject_policy_blocks_extension_removal(self):
        current = build_import_artifact(
            warehouse_data(), [{"id": "A.01"}], [extension()]
        )
        desired_data = warehouse_data("2", "reject")
        desired = build_import_artifact(desired_data, [{"id": "A.01"}], [])
        plan = build_plan(current, desired, Path("current.yaml"), Path("desired.yaml"))
        self.assertEqual(plan["summary"]["extension_remove"], 1)
        self.assertEqual(plan["summary"]["conflict"], 1)


class CompiledEntityTypeTests(unittest.TestCase):
    """entity_type is checked against what the compiler emits, not a
    fixed list in the schema."""

    HALL_3 = REPO_ROOT / "customers" / "example_customer" / "facilities" / "facility_pa11" / "buildings" / "hall_3" / "warehouse.yaml"

    def test_schema_accepts_any_entity_type_name(self):
        value = extension()
        value["extension"]["records"][0]["entity_type"] = "bay"
        self.assertEqual(list(EXTENSION_VALIDATOR.iter_errors(value)), [])

    def test_index_contains_new_compiled_entities(self):
        from validate import build_extension_entity_index, collect_imports, load_storage_templates, load_yaml, resolve_storage_templates
        imports = {p.name: load_yaml(p) for p in collect_imports(self.HALL_3)}
        storage = imports["storage.yaml"]
        resolve_storage_templates(storage, load_storage_templates())
        index = build_extension_entity_index(
            load_yaml(self.HALL_3), storage, imports.get("wcs.yaml"), imports.get("lanes.yaml"),
            imports.get("movement_rules.yaml"), imports.get("replenishment.yaml"),
        )
        self.assertIn("HBR.F01-01", index["bay"])
        self.assertIn("HBR.F01-01-L01", index["shelf"])
        self.assertIn("HBR.A01", index["route_aisle"])

    def test_unknown_entity_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            warehouse = root / "warehouse.yaml"
            sidecar = root / "extension.yaml"
            warehouse.write_text(yaml.safe_dump(warehouse_data()), encoding="utf-8")
            value = extension(dataset_id=warehouse_data()["metadata"]["dataset_id"])
            value["extension"]["records"][0]["entity_type"] = "spaceship"
            sidecar.write_text(yaml.safe_dump(value), encoding="utf-8")
            errors = validate_extensions(warehouse, [sidecar], {"storage_point": {"A.01"}})
        self.assertTrue(any("not a compiled entity type" in error for error in errors), errors)

    def test_hall_3_sidecar_with_bay_and_route_aisle_is_valid(self):
        from validate import collect_element_ids, validate_warehouse_file
        self.assertEqual(validate_warehouse_file(self.HALL_3, collect_element_ids()), [])


if __name__ == "__main__":
    unittest.main()
