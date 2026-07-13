#!/usr/bin/env python3
"""
Validates a warehouse.yaml (and all files imported by it) against
the JSON schemas in schemas/ and runs simple consistency checks.

Usage:
    python tools/validate.py customers/example_customer/warehouse.yaml

Extension planned (see README "Next Steps"):
    - Referential integrity across file boundaries (movement_rule ->
      existing storage_type, replenishment source/destination ->
      existing activity_area, etc.)
    - Check: for movement_policy=explicit_only, a matching movement_rule
      must exist for every lane
"""

import sys
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, RefResolver

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schemas"


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def make_validator(schema_name: str) -> Draft7Validator:
    schema = load_schema(schema_name)
    resolver = RefResolver(base_uri=f"{SCHEMA_DIR.as_uri()}/", referrer=schema)
    return Draft7Validator(schema, resolver=resolver)


VALIDATORS = {
    "warehouse.yaml": ("warehouse.schema.json", "warehouse"),
}


def collect_imports(warehouse_file: Path) -> list[Path]:
    data = load_yaml(warehouse_file)
    imports = data.get("warehouse", {}).get("imports", [])
    base_dir = warehouse_file.parent
    return [base_dir / rel for rel in imports]


def validate_file(path: Path, schema_name: str, root_key: str | None) -> list[str]:
    errors = []
    data = load_yaml(path)
    if data is None:
        return [f"{path}: File is empty or invalid."]

    validator = make_validator(schema_name)
    target = data if root_key is None else data
    for err in validator.iter_errors(target):
        loc = " -> ".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{path}: [{loc}] {err.message}")
    return errors


def validate_storage_types(path: Path) -> list[str]:
    errors = []
    data = load_yaml(path)
    schema = load_schema("storage-type.schema.json")
    validator = Draft7Validator(schema)
    for st in data.get("storage_types", []):
        for err in validator.iter_errors(st):
            errors.append(f"{path}: storage_type '{st.get('id', '?')}': {err.message}")
    return errors


def validate_movement_rules(path: Path) -> list[str]:
    errors = []
    data = load_yaml(path)
    schema = load_schema("movement-rule.schema.json")
    validator = Draft7Validator(schema)
    for rule in data.get("movement_rules", []):
        for err in validator.iter_errors(rule):
            errors.append(f"{path}: movement_rule '{rule.get('id', '?')}': {err.message}")
    return errors


def validate_replenishment(path: Path) -> list[str]:
    errors = []
    data = load_yaml(path)
    schema = load_schema("replenishment-strategy.schema.json")
    validator = Draft7Validator(schema)
    for strat in data.get("replenishment_strategies", []):
        for err in validator.iter_errors(strat):
            errors.append(f"{path}: replenishment_strategy '{strat.get('id', '?')}': {err.message}")
    return errors


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if len(argv) != 2:
        print("Usage: python tools/validate.py <path-to-warehouse.yaml>")
        return 2

    warehouse_file = Path(argv[1]).resolve()
    if not warehouse_file.exists():
        print(f"File not found: {warehouse_file}")
        return 2

    all_errors: list[str] = []

    all_errors += validate_file(warehouse_file, "warehouse.schema.json", "warehouse")

    for imported in collect_imports(warehouse_file):
        if not imported.exists():
            all_errors.append(f"{warehouse_file}: imported file missing: {imported}")
            continue

        name = imported.name
        if name == "storage.yaml":
            all_errors += validate_storage_types(imported)
        elif name == "movement_rules.yaml":
            all_errors += validate_movement_rules(imported)
        elif name == "replenishment.yaml":
            all_errors += validate_replenishment(imported)
        # lanes.yaml, wcs.yaml: currently only existence/parse check via load_yaml
        else:
            data = load_yaml(imported)
            if data is None:
                all_errors.append(f"{imported}: File is empty or invalid.")

    if all_errors:
        print(f"❌ {len(all_errors)} validation errors found:\n")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print("✅ Validation successful.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
