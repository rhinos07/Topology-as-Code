#!/usr/bin/env python3
"""
Validiert eine warehouse.yaml (und alle darin importierten Dateien) gegen
die JSON-Schemas in schemas/ und fuehrt einfache Konsistenzchecks aus.

Nutzung:
    python tools/validate.py customers/example_customer/warehouse.yaml

Erweiterung geplant (siehe README "Naechste Schritte"):
    - Referenz-Integritaet ueber Dateigrenzen hinweg (movement_rule ->
      existierender storage_type, replenishment source/destination ->
      existierende activity_area, etc.)
    - Pruefung: bei movement_policy=explicit_only muss fuer jede Lane eine
      passende movement_rule existieren
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
        return [f"{path}: Datei ist leer oder ungueltig."]

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
    if len(argv) != 2:
        print("Nutzung: python tools/validate.py <pfad-zu-warehouse.yaml>")
        return 2

    warehouse_file = Path(argv[1]).resolve()
    if not warehouse_file.exists():
        print(f"Datei nicht gefunden: {warehouse_file}")
        return 2

    all_errors: list[str] = []

    all_errors += validate_file(warehouse_file, "warehouse.schema.json", "warehouse")

    for imported in collect_imports(warehouse_file):
        if not imported.exists():
            all_errors.append(f"{warehouse_file}: importierte Datei fehlt: {imported}")
            continue

        name = imported.name
        if name == "storage.yaml":
            all_errors += validate_storage_types(imported)
        elif name == "movement_rules.yaml":
            all_errors += validate_movement_rules(imported)
        elif name == "replenishment.yaml":
            all_errors += validate_replenishment(imported)
        # lanes.yaml, mfr.yaml: aktuell nur Existenz-/Parse-Check ueber load_yaml
        else:
            data = load_yaml(imported)
            if data is None:
                all_errors.append(f"{imported}: Datei ist leer oder ungueltig.")

    if all_errors:
        print(f"❌ {len(all_errors)} Validierungsfehler gefunden:\n")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print("✅ Validierung erfolgreich.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
