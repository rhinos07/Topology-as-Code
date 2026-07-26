"""Tests for storage_type.template - the authoring-layer indirection that
resolves into default_attributes before anything downstream sees it.

The central guarantee, covered by
test_templating_does_not_change_effective_attributes: templating a
storage_type is a source-level refactor only. It must never change the
effective attributes, and therefore never the compiled artifact or its
content hash. See Warehouse-as-Code ADR-0001.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate import (  # noqa: E402
    check_storage_refs,
    load_storage_templates,
    resolve_storage_templates,
)

TEMPLATES = {
    "euro_bay": {
        "id": "euro_bay",
        "description": "ignored on merge",
        "max_weight": {"value": 1000, "unit": "kg"},
        "capacity_per_point": 1,
        "size": {
            "width": {"value": 1.2, "unit": "m"},
            "depth": {"value": 0.8, "unit": "m"},
        },
        "allowed_load_unit_types": ["pallet_euro"],
    },
}


class StorageTemplateResolutionTests(unittest.TestCase):
    def test_template_values_land_in_default_attributes(self):
        data = {"storage_types": [{"id": "A", "template": "euro_bay"}]}
        resolve_storage_templates(data, TEMPLATES)
        attributes = data["storage_types"][0]["default_attributes"]
        self.assertEqual(attributes["max_weight"], {"value": 1000, "unit": "kg"})
        self.assertEqual(attributes["allowed_load_unit_types"], ["pallet_euro"])

    def test_meta_keys_are_not_merged(self):
        """id/description describe the template, not the storage location."""
        data = {"storage_types": [{"id": "A", "template": "euro_bay"}]}
        resolve_storage_templates(data, TEMPLATES)
        attributes = data["storage_types"][0]["default_attributes"]
        self.assertNotIn("description", attributes)
        self.assertNotIn("id", attributes)

    def test_own_attributes_win_key_by_key(self):
        data = {"storage_types": [{
            "id": "A",
            "template": "euro_bay",
            "default_attributes": {"capacity_per_point": 9, "zone": "Z1"},
        }]}
        resolve_storage_templates(data, TEMPLATES)
        attributes = data["storage_types"][0]["default_attributes"]
        self.assertEqual(attributes["capacity_per_point"], 9)   # overridden
        self.assertEqual(attributes["zone"], "Z1")              # local only
        self.assertEqual(attributes["max_weight"]["value"], 1000)  # inherited

    def test_merge_is_shallow_so_nested_override_replaces_wholesale(self):
        """Documented semantics: overriding 'size' replaces the whole object
        rather than merging into it - same as a storage_point exception."""
        data = {"storage_types": [{
            "id": "A",
            "template": "euro_bay",
            "default_attributes": {"size": {"height": {"value": 2, "unit": "m"}}},
        }]}
        resolve_storage_templates(data, TEMPLATES)
        size = data["storage_types"][0]["default_attributes"]["size"]
        self.assertEqual(set(size), {"height"})

    def test_resolution_does_not_mutate_the_catalog(self):
        data = {"storage_types": [{
            "id": "A",
            "template": "euro_bay",
            "default_attributes": {"size": {"height": {"value": 2, "unit": "m"}}},
        }]}
        resolve_storage_templates(data, TEMPLATES)
        data["storage_types"][0]["default_attributes"]["max_weight"]["value"] = 7
        self.assertEqual(TEMPLATES["euro_bay"]["max_weight"]["value"], 1000)

    def test_storage_type_without_template_is_untouched(self):
        data = {"storage_types": [{"id": "A"}]}
        resolve_storage_templates(data, TEMPLATES)
        self.assertNotIn("default_attributes", data["storage_types"][0])

    def test_unknown_template_is_left_unresolved(self):
        """check_storage_refs reports it; resolution must not invent values."""
        data = {"storage_types": [{"id": "A", "template": "nope"}]}
        resolve_storage_templates(data, TEMPLATES)
        self.assertNotIn("default_attributes", data["storage_types"][0])

    def test_templating_does_not_change_effective_attributes(self):
        """The ADR-0001 guarantee: an inline storage_type and a templated one
        describing the same location resolve to exactly the same attributes."""
        inline = {"storage_types": [{
            "id": "A",
            "default_attributes": {
                "max_weight": {"value": 1000, "unit": "kg"},
                "capacity_per_point": 1,
                "size": {
                    "width": {"value": 1.2, "unit": "m"},
                    "depth": {"value": 0.8, "unit": "m"},
                },
                "allowed_load_unit_types": ["pallet_euro"],
                "zone": "Z1",
            },
        }]}
        templated = {"storage_types": [{
            "id": "A",
            "template": "euro_bay",
            "default_attributes": {"zone": "Z1"},
        }]}
        resolve_storage_templates(inline, TEMPLATES)
        resolve_storage_templates(templated, TEMPLATES)
        self.assertEqual(
            inline["storage_types"][0]["default_attributes"],
            templated["storage_types"][0]["default_attributes"],
        )


class StorageTemplateReferenceTests(unittest.TestCase):
    def _errors(self, storage: dict) -> list[str]:
        element_ids = {"storage_templates": {"euro_bay"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.yaml"
            path.write_text(yaml.safe_dump(storage), encoding="utf-8")
            return check_storage_refs(path, set(), element_ids)

    def test_unknown_template_reference_is_an_error(self):
        errors = self._errors({"storage_types": [{"id": "A", "template": "missing"}]})
        self.assertTrue(any("template 'missing'" in e for e in errors))

    def test_known_template_reference_is_accepted(self):
        errors = self._errors({"storage_types": [{"id": "A", "template": "euro_bay"}]})
        self.assertEqual([e for e in errors if "template" in e], [])

    def test_inherited_load_unit_types_are_reference_checked(self):
        """Resolved data reaches check_storage_refs, so a bad load unit id in a
        template is caught at every use site rather than slipping through."""
        storage = {"storage_types": [{"id": "A", "template": "euro_bay"}]}
        resolve_storage_templates(storage, {
            "euro_bay": {"id": "euro_bay", "allowed_load_unit_types": ["missing_lu"]},
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.yaml"
            path.write_text("storage_types: []\n", encoding="utf-8")
            errors = check_storage_refs(
                path, set(), {"load_unit_types": {"pallet_euro"}}, storage
            )
        self.assertTrue(any("missing_lu" in e for e in errors))


class ShippedCatalogTests(unittest.TestCase):
    def test_repo_catalog_loads_and_every_template_has_an_id(self):
        templates = load_storage_templates()
        self.assertTrue(templates)
        for template_id, template in templates.items():
            self.assertEqual(template_id, template["id"])

    def test_every_referenced_template_exists(self):
        """Guards the whole customers/ tree, not just the two example buildings."""
        known = set(load_storage_templates())
        for path in (REPO_ROOT / "customers").rglob("structure/storage.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for st in data.get("storage_types", []):
                template = st.get("template")
                if template is not None:
                    self.assertIn(template, known, f"{path}: storage_type '{st.get('id')}'")


if __name__ == "__main__":
    unittest.main()
