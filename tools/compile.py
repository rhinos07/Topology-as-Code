#!/usr/bin/env python3
"""
Compiles storage_point_generator and layout_variants templates in a
customer's storage.yaml into concrete storage_point instances.

This is the "terraform apply"-equivalent expansion step: the WMS should
only ever see concrete storage_points, never storage_point_generator or
layout_variants template syntax. Run tools/validate.py first - this
script does not re-validate schema conformance, only expands templates
that are already assumed to be schema-valid.

Usage:
    python tools/compile.py customers/example_customer/warehouse.yaml
    python tools/compile.py customers/example_customer/warehouse.yaml --output build/storage_points.yaml
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate import collect_imports, load_yaml  # noqa: E402


def merge_attributes(default_attributes: dict, exception: dict | None) -> dict:
    merged = dict(default_attributes or {})
    if exception:
        for key, value in exception.items():
            if key == "coordinate":
                continue
            merged[key] = value
    return merged


def expand_storage_point_generator(storage_type: dict) -> tuple[list[dict], list[str]]:
    warnings = []
    gen = storage_type["storage_point_generator"]
    aisles = gen.get("aisles", 1)
    stacks = gen.get("stacks", 1)
    levels = gen.get("levels", 1)
    pattern = gen["coordinate_pattern"]

    exceptions_by_coord = {
        exc["coordinate"]: exc for exc in storage_type.get("exceptions", [])
    }
    default_attributes = storage_type.get("default_attributes", {})

    points = []
    for aisle in range(1, aisles + 1):
        for stack in range(1, stacks + 1):
            for level in range(1, levels + 1):
                coordinate = pattern.format(aisle=aisle, stack=stack, level=level)
                exception = exceptions_by_coord.get(coordinate)
                attributes = merge_attributes(default_attributes, exception)
                points.append({
                    "id": f"{storage_type['id']}.{coordinate}",
                    "storage_type": storage_type["id"],
                    "coordinate": coordinate,
                    **attributes,
                })

    used_coordinates = {p["coordinate"] for p in points}
    for coordinate in exceptions_by_coord:
        if coordinate not in used_coordinates:
            warnings.append(
                f"storage_type '{storage_type['id']}': exception coordinate "
                f"'{coordinate}' does not match any generated point (typo?)"
            )

    return points, warnings


def expand_layout_variants(storage_type: dict) -> tuple[list[dict], list[str]]:
    variants = storage_type["layout_variants"]
    grid = storage_type.get("layout_grid", {})
    aisles = grid.get("aisles", 1)
    bays = grid.get("bays", 1)
    default_attributes = storage_type.get("default_attributes", {})

    points = []
    for variant in variants:
        pattern = variant["coordinate_pattern"]
        positions = variant["positions_per_bay"]
        for aisle in range(1, aisles + 1):
            for bay in range(1, bays + 1):
                for slot in range(1, positions + 1):
                    coordinate = pattern.format(aisle=aisle, bay=bay, slot=slot)
                    attributes = dict(default_attributes)
                    attributes["allowed_load_unit_types"] = [variant["load_unit_type"]]
                    points.append({
                        "id": f"{storage_type['id']}.{coordinate}",
                        "storage_type": storage_type["id"],
                        "coordinate": coordinate,
                        "layout_variant": variant["id"],
                        "physical_bay": f"{storage_type['id']}-{aisle:02d}-{bay:02d}",
                        **attributes,
                    })

    return points, []


def compile_storage_types(storage_data: dict) -> tuple[list[dict], list[str]]:
    all_points: list[dict] = []
    warnings: list[str] = []

    for storage_type in storage_data.get("storage_types", []):
        if "storage_point_generator" in storage_type:
            points, w = expand_storage_point_generator(storage_type)
        elif "layout_variants" in storage_type:
            points, w = expand_layout_variants(storage_type)
        else:
            warnings.append(
                f"storage_type '{storage_type['id']}': has neither "
                f"storage_point_generator nor layout_variants - skipped "
                f"(assumed to be manually enumerated elsewhere)."
            )
            continue

        all_points += points
        warnings += w

    return all_points, warnings


def find_duplicate_ids(points: list[dict]) -> list[str]:
    seen: set[str] = set()
    errors = []
    for point in points:
        if point["id"] in seen:
            errors.append(f"duplicate storage_point id generated: '{point['id']}'")
        seen.add(point["id"])
    return errors


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("warehouse_file", type=Path)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write the compiled storage_points to this YAML file. "
             "Without this, only a per-storage_type summary is printed.",
    )
    args = parser.parse_args(argv[1:])

    warehouse_file = args.warehouse_file.resolve()
    if not warehouse_file.exists():
        print(f"File not found: {warehouse_file}")
        return 2

    storage_file = next(
        (p for p in collect_imports(warehouse_file) if p.name == "storage.yaml"),
        None,
    )
    if storage_file is None or not storage_file.exists():
        print(f"{warehouse_file}: no storage.yaml import found")
        return 2

    storage_data = load_yaml(storage_file)
    points, warnings = compile_storage_types(storage_data)
    errors = find_duplicate_ids(points)

    counts: dict[str, int] = {}
    for point in points:
        counts[point["storage_type"]] = counts.get(point["storage_type"], 0) + 1

    print("Compiled storage_points per storage_type:")
    for storage_type_id, count in counts.items():
        print(f"  {storage_type_id}: {count}")
    print(f"  TOTAL: {len(points)}")

    if warnings:
        print(f"\n⚠ {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print(f"\n❌ {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            yaml.safe_dump({"storage_points": points}, f, sort_keys=False, allow_unicode=True)
        print(f"\n✅ Wrote {len(points)} storage_points to {args.output}")
    else:
        print("\nPass --output <file> to write the full expanded storage_point list.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
