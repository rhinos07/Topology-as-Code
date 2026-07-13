#!/usr/bin/env python3
"""
Validates a company.yaml, facility.yaml, or warehouse.yaml (and everything
it references, cascading down the Company -> Facility -> Building
hierarchy) against the JSON schemas in schemas/ and runs simple
consistency checks.

Usage:
    python tools/validate.py customers/example_customer/company.yaml
    python tools/validate.py customers/example_customer/facilities/facility_pa11/facility.yaml
    python tools/validate.py customers/example_customer/facilities/facility_pa11/buildings/hall_3/warehouse.yaml

Any of the three levels can be passed directly; validation cascades
downward from whichever level you start at (company -> all facilities ->
all buildings; facility -> all buildings; warehouse -> just that building).

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
ELEMENTS_DIR = REPO_ROOT / "elements"

ELEMENT_CATALOGS = {
    "load_unit_types.yaml": ("load-unit-type.schema.json", "load_unit_types"),
    "equipment_types.yaml": ("equipment-type.schema.json", "equipment_types"),
    "process_types.yaml": ("process-type.schema.json", "process_types"),
    "blocking_reasons.yaml": ("blocking-reason.schema.json", "blocking_reasons"),
    "hazmat_classes.yaml": ("hazmat-class.schema.json", "hazmat_classes"),
}


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


def collect_relative_refs(path: Path, root_key: str, list_key: str) -> list[Path]:
    data = load_yaml(path)
    refs = data.get(root_key, {}).get(list_key, [])
    base_dir = path.parent
    return [base_dir / rel for rel in refs]


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


def validate_doors(path: Path) -> list[str]:
    errors = []
    data = load_yaml(path)
    schema = load_schema("door.schema.json")
    validator = Draft7Validator(schema)
    for door in data.get("doors", []):
        for err in validator.iter_errors(door):
            errors.append(f"{path}: door '{door.get('id', '?')}': {err.message}")
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


def validate_element_catalog(path: Path, schema_name: str, list_key: str) -> list[str]:
    errors = []
    data = load_yaml(path)
    if data is None:
        return [f"{path}: File is empty or invalid."]
    schema = load_schema(schema_name)
    validator = Draft7Validator(schema)
    for item in data.get(list_key, []):
        for err in validator.iter_errors(item):
            errors.append(f"{path}: {list_key} '{item.get('id', '?')}': {err.message}")
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


def validate_warehouse_file(warehouse_file: Path) -> list[str]:
    """Validates a single building-level warehouse.yaml and everything it imports."""
    if not warehouse_file.exists():
        return [f"warehouse file missing: {warehouse_file}"]

    all_errors = validate_file(warehouse_file, "warehouse.schema.json", "warehouse")

    for imported in collect_imports(warehouse_file):
        if not imported.exists():
            all_errors.append(f"{warehouse_file}: imported file missing: {imported}")
            continue

        name = imported.name
        if name == "storage.yaml":
            all_errors += validate_storage_types(imported)
            all_errors += validate_doors(imported)
        elif name == "movement_rules.yaml":
            all_errors += validate_movement_rules(imported)
        elif name == "replenishment.yaml":
            all_errors += validate_replenishment(imported)
        elif name == "lanes.yaml":
            all_errors += validate_file(imported, "lanes.schema.json", None)
        elif name == "wcs.yaml":
            all_errors += validate_file(imported, "wcs.schema.json", None)
        else:
            data = load_yaml(imported)
            if data is None:
                all_errors.append(f"{imported}: File is empty or invalid.")

    return all_errors


def validate_facility_file(facility_file: Path) -> list[str]:
    """Validates a facility.yaml and cascades into every building it lists."""
    if not facility_file.exists():
        return [f"facility file missing: {facility_file}"]

    all_errors = validate_file(facility_file, "facility.schema.json", "facility")

    for building_file in collect_relative_refs(facility_file, "facility", "buildings"):
        all_errors += validate_warehouse_file(building_file)

    return all_errors


def validate_company_file(company_file: Path) -> list[str]:
    """Validates a company.yaml and cascades into every facility it lists."""
    all_errors = validate_file(company_file, "company.schema.json", "company")

    for facility_file in collect_relative_refs(company_file, "company", "facilities"):
        all_errors += validate_facility_file(facility_file)

    return all_errors


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if len(argv) != 2:
        print("Usage: python tools/validate.py <path-to-company|facility|warehouse.yaml>")
        return 2

    target_file = Path(argv[1]).resolve()
    if not target_file.exists():
        print(f"File not found: {target_file}")
        return 2

    all_errors: list[str] = []

    for filename, (schema_name, list_key) in ELEMENT_CATALOGS.items():
        catalog_file = ELEMENTS_DIR / filename
        if catalog_file.exists():
            all_errors += validate_element_catalog(catalog_file, schema_name, list_key)

    data = load_yaml(target_file)
    if data is None:
        all_errors.append(f"{target_file}: File is empty or invalid.")
    elif "company" in data:
        all_errors += validate_company_file(target_file)
    elif "facility" in data:
        all_errors += validate_facility_file(target_file)
    elif "warehouse" in data:
        all_errors += validate_warehouse_file(target_file)
    else:
        all_errors.append(
            f"{target_file}: unrecognized root key (expected one of "
            f"'company', 'facility', 'warehouse')."
        )

    if all_errors:
        print(f"❌ {len(all_errors)} validation errors found:\n")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print("✅ Validation successful.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
