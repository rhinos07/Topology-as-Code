#!/usr/bin/env python3
"""
Compiles a building's complete desired state into normalized, typed entities.
Sources, all reached via the warehouse.yaml imports:
  - storage.yaml: storage_point_generator / layout_variants / explicit
    storage_points on each storage_type, plus work_centers flagged
    storage_point_ref: true
  - wcs.yaml: reporting_points flagged storage_point_ref: true

This is the deterministic artifact step before planning/apply: the WMS sees
concrete storage_points plus all building-owned topology, controller and
strategy entities, never generator/variant template syntax. Run
tools/validate.py first; this script assumes schema-valid input.

Usage:
    python tools/compile.py customers/example_customer/warehouse.yaml
    python tools/compile.py customers/example_customer/warehouse.yaml --output build/warehouse-artifact.yaml
"""

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate import collect_extension_imports, collect_imports, load_yaml  # noqa: E402


ARTIFACT_API_VERSION = "topology-as-code/artifact-v1"
COMPILER_VERSION = "1.0.0"


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
                channel_coordinate = pattern.format(aisle=aisle, stack=stack, level=level)
                depths = range(1, storage_type["channel_depth"] + 1) if storage_type.get("access_model") == "channel" else (None,)
                for depth in depths:
                    coordinate = (
                        f"{channel_coordinate}-D{depth:02d}"
                        if depth is not None else channel_coordinate
                    )
                    exception = exceptions_by_coord.get(coordinate)
                    attributes = merge_attributes(default_attributes, exception)
                    point = {
                        "id": f"{storage_type['id']}.{coordinate}",
                        "storage_type": storage_type["id"],
                        "coordinate": coordinate,
                        **attributes,
                    }
                    if depth is not None:
                        point.update({
                            "capacity_per_point": 1,
                            "channel": f"{storage_type['id']}.{channel_coordinate}",
                            "channel_position": depth,
                        })
                    points.append(point)

    used_coordinates = {p["coordinate"] for p in points}
    for coordinate in exceptions_by_coord:
        if coordinate not in used_coordinates:
            warnings.append(
                f"storage_type '{storage_type['id']}': exception coordinate "
                f"'{coordinate}' does not match any generated point (typo?)"
            )

    return points, warnings


def expand_explicit_storage_points(storage_type: dict) -> tuple[list[dict], list[str]]:
    """Passes through explicitly enumerated storage_points, merging each
    entry over default_attributes (same semantics as an exception). Used
    for storage_types that declare a small, fixed set of points - e.g. a
    single opaque block place for a controller-managed area - instead of
    generating them from a grid."""
    default_attributes = storage_type.get("default_attributes", {})
    points = []
    for entry in storage_type["storage_points"]:
        coordinate = entry["coordinate"]
        attributes = merge_attributes(default_attributes, entry)
        points.append({
            "id": f"{storage_type['id']}.{coordinate}",
            "storage_type": storage_type["id"],
            "coordinate": coordinate,
            **attributes,
        })
    return points, []


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


def _axis_matches(selector: dict | None, value: int) -> bool:
    if not selector:
        return True
    if "values" in selector:
        return value in selector["values"]
    return selector["from"] <= value <= selector["to"]


def section_selector_coordinates(storage_type: dict, selector: dict) -> set[str]:
    if "coordinates" in selector:
        return set(selector["coordinates"])
    coordinates: set[str] = set()
    if "storage_point_generator" in storage_type:
        if set(selector).intersection({"bays", "slots"}):
            return set()
        generator = storage_type["storage_point_generator"]
        for aisle in range(1, generator.get("aisles", 1) + 1):
            for stack in range(1, generator.get("stacks", 1) + 1):
                for level in range(1, generator.get("levels", 1) + 1):
                    depths = range(1, storage_type["channel_depth"] + 1) if storage_type.get("access_model") == "channel" else (None,)
                    for depth in depths:
                        if not (
                            _axis_matches(selector.get("aisles"), aisle)
                            and _axis_matches(selector.get("stacks"), stack)
                            and _axis_matches(selector.get("levels"), level)
                            and (depth is None or _axis_matches(selector.get("depths"), depth))
                        ):
                            continue
                        base = generator["coordinate_pattern"].format(
                            aisle=aisle, stack=stack, level=level
                        )
                        coordinates.add(f"{base}-D{depth:02d}" if depth is not None else base)
    elif "layout_variants" in storage_type:
        if set(selector).intersection({"stacks", "levels"}):
            return set()
        grid = storage_type.get("layout_grid", {})
        for variant in storage_type["layout_variants"]:
            for aisle in range(1, grid.get("aisles", 1) + 1):
                for bay in range(1, grid.get("bays", 1) + 1):
                    for slot in range(1, variant["positions_per_bay"] + 1):
                        if not (
                            _axis_matches(selector.get("aisles"), aisle)
                            and _axis_matches(selector.get("bays"), bay)
                            and _axis_matches(selector.get("slots"), slot)
                        ):
                            continue
                        coordinates.add(variant["coordinate_pattern"].format(
                            aisle=aisle, bay=bay, slot=slot
                        ))
    return coordinates


def assign_point_sections(storage_type: dict, points: list[dict]) -> None:
    selector_membership: dict[str, list[str]] = {}
    for section in storage_type.get("sections", []):
        if "selector" not in section:
            continue
        for coordinate in section_selector_coordinates(storage_type, section["selector"]):
            selector_membership.setdefault(coordinate, []).append(section["id"])

    st_id = storage_type["id"]
    for point in points:
        direct = point.get("section")
        selected = [direct] if direct else selector_membership.get(point["coordinate"], [])
        if selected:
            point["section"] = f"{st_id}.{selected[0]}"


def compile_storage_types(storage_data: dict) -> tuple[list[dict], list[str]]:
    all_points: list[dict] = []
    warnings: list[str] = []

    for storage_type in storage_data.get("storage_types", []):
        if "storage_point_generator" in storage_type:
            points, w = expand_storage_point_generator(storage_type)
        elif "layout_variants" in storage_type:
            points, w = expand_layout_variants(storage_type)
        elif "storage_points" in storage_type:
            points, w = expand_explicit_storage_points(storage_type)
        else:
            warnings.append(
                f"storage_type '{storage_type['id']}': has none of "
                f"storage_point_generator / layout_variants / storage_points "
                f"- skipped (assumed to be manually enumerated elsewhere)."
            )
            continue

        assign_point_sections(storage_type, points)

        all_points += points
        warnings += w

    return all_points, warnings


def expand_storage_point_refs(storage_data: dict, wcs_data: dict | None) -> list[dict]:
    """Emits a storage_point for every work_center (storage.yaml) and
    reporting_point (wcs.yaml) flagged storage_point_ref: true. These are
    not part of a storage_type - they are activity/communication points
    that also carry bookable inventory (WIP, staged goods, an in-transit
    HU at a reporting point) - so they are compiled here rather than via
    a storage_type expansion. See docs/entity-glossary.md principle 6."""
    points: list[dict] = []

    for wc in storage_data.get("work_centers", []):
        if not wc.get("storage_point_ref"):
            continue
        point = {
            "id": wc["id"],
            "ref_kind": "work_center",
            "coordinate": wc["id"],
            "storage_point_ref": True,
        }
        if "step" in wc:
            point["step"] = wc["step"]
        points.append(point)

    for rp in (wcs_data or {}).get("reporting_points", []):
        if not rp.get("storage_point_ref"):
            continue
        point = {
            "id": rp["id"],
            "ref_kind": "reporting_point",
            "coordinate": rp["id"],
            "storage_point_ref": True,
        }
        if "controller" in rp:
            point["controller"] = rp["controller"]
        if "capacity" in rp:
            point["capacity_per_point"] = rp["capacity"]
        points.append(point)

    return points


def point_group(point: dict) -> str:
    """Grouping label for the per-group summary: the storage_type for
    generated/explicit points, or the ref_kind for storage_point_ref points."""
    return point.get("storage_type") or point.get("ref_kind") or "(unknown)"


def find_duplicate_ids(points: list[dict]) -> list[str]:
    seen: set[str] = set()
    errors = []
    for point in points:
        if point["id"] in seen:
            errors.append(f"duplicate storage_point id generated: '{point['id']}'")
        seen.add(point["id"])
    return errors


def normalize_extensions(extensions: list[dict] | None) -> list[dict]:
    normalized = []
    for data in extensions or []:
        extension = dict(data["extension"])
        extension["records"] = sorted(
            extension.get("records", []),
            key=lambda record: (record["entity_type"], record["entity_id"]),
        )
        normalized.append({
            "api_version": data["api_version"],
            "extension": extension,
        })
    return sorted(normalized, key=lambda data: data["extension"]["namespace"])


STORAGE_TYPE_SOURCE_FIELDS = {
    "storage_point_generator", "layout_variants", "layout_grid",
    "storage_points", "default_attributes", "exceptions", "sections",
    "section_membership",
}

UNIT_NORMALIZATION = {
    "kg": (1.0, "kg"), "g": (0.001, "kg"), "t": (1000.0, "kg"),
    "m": (1.0, "m"), "cm": (0.01, "m"), "mm": (0.001, "m"),
    "m3": (1.0, "m3"), "l": (0.001, "m3"),
    "m/s": (1.0, "m/s"), "km/h": (1.0 / 3.6, "m/s"),
    "s": (1.0, "s"), "min": (60.0, "s"),
    "h": (3600.0, "s"), "d": (86400.0, "s"),
}


def normalize_quantities(value):
    """Deep-copy an entity and normalize recognized physical quantities."""
    if isinstance(value, list):
        return [normalize_quantities(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"value", "unit"} and value.get("unit") in UNIT_NORMALIZATION:
        factor, unit = UNIT_NORMALIZATION[value["unit"]]
        normalized = round(float(value["value"]) * factor, 12)
        if normalized.is_integer():
            normalized = int(normalized)
        return {"value": normalized, "unit": unit}
    return {key: normalize_quantities(item) for key, item in value.items()}


def compile_can_edges(
    storage_data: dict | None,
    wcs_data: dict | None,
    lanes_data: dict | None,
) -> list[dict]:
    """Compile every physical connection into canonical directed can-edges."""
    edges: list[dict] = []

    def add(source_type: str, source_id: str, source: str, target: str,
            transport_mode: str, attributes: dict | None = None) -> None:
        edge = {
            "id": f"{source_type}:{source_id}:{source}:{target}",
            "from": source,
            "to": target,
            "source_type": source_type,
            "source_id": source_id,
            "transport_mode": transport_mode,
        }
        edge.update(attributes or {})
        edges.append(edge)

    for connection in (lanes_data or {}).get("connections", []):
        attributes = {
            key: connection[key]
            for key in ("expected_duration", "capacity") if key in connection
        }
        add("connection", connection["id"], connection["from"], connection["to"],
            connection["transport_mode"], attributes)
        if connection["direction"] == "bidirectional":
            add("connection", connection["id"], connection["to"], connection["from"],
                connection["transport_mode"], attributes)

    for segment in (lanes_data or {}).get("conveyor_segments", []):
        attributes = {"capacity": segment["capacity"]} if "capacity" in segment else {}
        add("conveyor_segment", segment["id"], segment["from"], segment["to"],
            "conveyor", attributes)

    for lane in (lanes_data or {}).get("lanes", []):
        for source in lane.get("connects", []):
            for target in lane.get("connects", []):
                if source != target:
                    add("lane", lane["id"], source, target, "lane")

    main = (lanes_data or {}).get("conveyor_main") or {}
    for source in main.get("connects", []):
        for target in main.get("connects", []):
            if source != target:
                attributes = ({"capacity_units_per_hour": main["capacity_units_per_hour"]}
                              if "capacity_units_per_hour" in main else {})
                add("conveyor_main", "CONVEYOR_MAIN", source, target, "conveyor", attributes)

    controller_nodes: dict[str, set[str]] = {}
    for storage_type in (storage_data or {}).get("storage_types", []):
        if storage_type.get("controller"):
            controller_nodes.setdefault(storage_type["controller"], set()).add(storage_type["id"])
    for reporting_point in (wcs_data or {}).get("reporting_points", []):
        if reporting_point.get("controller"):
            controller_nodes.setdefault(reporting_point["controller"], set()).add(reporting_point["id"])
    for controller, nodes in controller_nodes.items():
        for source in nodes:
            for target in nodes:
                if source != target:
                    add("controller_internal", controller, source, target, "controller_internal")

    return sorted(edges, key=lambda edge: edge["id"])


def compile_entity_collections(
    warehouse_data: dict,
    storage_data: dict,
    wcs_data: dict | None,
    lanes_data: dict | None,
    movement_data: dict | None,
    replenishment_data: dict | None,
    points: list[dict],
) -> dict[str, list[dict]]:
    """Normalize every building-owned desired-state entity for WMS import."""
    warehouse = dict(warehouse_data["warehouse"])
    warehouse.pop("imports", None)
    warehouse.pop("extension_imports", None)
    warehouse["id"] = warehouse_data["target"]["building"]

    storage_types = []
    sections = []
    for source in storage_data.get("storage_types", []):
        storage_types.append({
            key: value for key, value in source.items()
            if key not in STORAGE_TYPE_SOURCE_FIELDS
        })
        for section in source.get("sections", []):
            section_entity = {
                key: value for key, value in section.items() if key != "selector"
            }
            sections.append({
                **section_entity,
                "id": f"{source['id']}.{section['id']}",
                "storage_type": source["id"],
                "local_id": section["id"],
            })

    conveyor_main = []
    if (lanes_data or {}).get("conveyor_main"):
        conveyor_main.append({
            "id": "CONVEYOR_MAIN",
            **lanes_data["conveyor_main"],
        })

    channels_by_id: dict[str, dict] = {}
    channel_storage_types = {
        item["id"]: item for item in storage_data.get("storage_types", [])
        if item.get("access_model") == "channel"
    }
    for point in points:
        channel_id = point.get("channel")
        if not channel_id:
            continue
        source = channel_storage_types[point["storage_type"]]
        channel = channels_by_id.setdefault(channel_id, {
            "id": channel_id,
            "storage_type": source["id"],
            "depth": source["channel_depth"],
            "entry_side": source["entry_side"],
            "exit_side": source["exit_side"],
            "flow": "lifo" if source["entry_side"] == source["exit_side"] else "fifo",
            "positions": [],
        })
        if "homogeneity_required" in source:
            channel["homogeneity_required"] = source["homogeneity_required"]
        channel["positions"].append(point["id"])
    channels = list(channels_by_id.values())
    for channel in channels:
        channel["positions"].sort(
            key=lambda point_id: next(
                point["channel_position"] for point in points if point["id"] == point_id
            )
        )

    entities = {
        "warehouse": [warehouse],
        "storage_type": storage_types,
        "storage_point": list(points),
        "channel": channels,
        "section": sections,
        "activity_area": list(storage_data.get("activity_areas", [])),
        "work_center": list(storage_data.get("work_centers", [])),
        "door": list(storage_data.get("doors", [])),
        "controller": list((wcs_data or {}).get("controller_definitions", [])),
        "reporting_point": list((wcs_data or {}).get("reporting_points", [])),
        "equipment": list((wcs_data or {}).get("equipment", [])),
        "telegram_action": list((wcs_data or {}).get("telegram_actions", [])),
        "lane": list((lanes_data or {}).get("lanes", [])),
        "conveyor_segment": list((lanes_data or {}).get("conveyor_segments", [])),
        "conveyor_main": conveyor_main,
        "can_edge": compile_can_edges(storage_data, wcs_data, lanes_data),
        "movement_rule": list((movement_data or {}).get("movement_rules", [])),
        "replenishment_strategy": list(
            (replenishment_data or {}).get("replenishment_strategies", [])
        ),
    }
    return {
        entity_type: sorted(items, key=lambda item: item["id"])
        for entity_type, items in sorted(entities.items())
    }


def build_import_artifact(
    warehouse_data: dict,
    points: list[dict],
    extensions: list[dict] | None = None,
    entities: dict[str, list[dict]] | None = None,
) -> dict:
    """Build a deterministic, WMS-neutral reconciliation artifact."""
    normalized_entities = entities or {
        "storage_point": sorted(points, key=lambda point: point["id"])
    }
    normalized_entities = {
        entity_type: sorted(
            [normalize_quantities(copy.deepcopy(item)) for item in items],
            key=lambda item: item["id"],
        )
        for entity_type, items in sorted(normalized_entities.items())
    }
    normalized_extensions = normalize_extensions(extensions)
    desired_state = {
        "api_version": warehouse_data["api_version"],
        "artifact_api_version": ARTIFACT_API_VERSION,
        "dataset_id": warehouse_data["metadata"]["dataset_id"],
        "target": warehouse_data["target"],
        "entities": normalized_entities,
        "extensions": normalized_extensions,
    }
    canonical = json.dumps(
        desired_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    content_hash = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return {
        "api_version": warehouse_data["api_version"],
        "artifact_api_version": ARTIFACT_API_VERSION,
        "metadata": dict(warehouse_data["metadata"]),
        "target": dict(warehouse_data["target"]),
        "import_policy": dict(warehouse_data["import_policy"]),
        "artifact": {
            "compiler_version": COMPILER_VERSION,
            "content_hash": content_hash,
            "entity_counts": {
                entity_type: len(items)
                for entity_type, items in normalized_entities.items()
            },
            "extension_record_count": sum(
                len(data["extension"].get("records", []))
                for data in normalized_extensions
            ),
        },
        "entities": normalized_entities,
        "extensions": normalized_extensions,
    }


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("warehouse_file", type=Path)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write the complete desired-state artifact to this YAML file. "
             "Without this, only a storage-point summary is printed.",
    )
    args = parser.parse_args(argv[1:])

    warehouse_file = args.warehouse_file.resolve()
    if not warehouse_file.exists():
        print(f"File not found: {warehouse_file}")
        return 2

    imports = collect_imports(warehouse_file)
    storage_file = next((p for p in imports if p.name == "storage.yaml"), None)
    if storage_file is None or not storage_file.exists():
        print(f"{warehouse_file}: no storage.yaml import found")
        return 2

    wcs_file = next((p for p in imports if p.name == "wcs.yaml"), None)
    wcs_data = load_yaml(wcs_file) if wcs_file and wcs_file.exists() else None
    lanes_file = next((p for p in imports if p.name == "lanes.yaml"), None)
    lanes_data = load_yaml(lanes_file) if lanes_file and lanes_file.exists() else None
    movement_file = next((p for p in imports if p.name == "movement_rules.yaml"), None)
    movement_data = load_yaml(movement_file) if movement_file and movement_file.exists() else None
    replenishment_file = next((p for p in imports if p.name == "replenishment.yaml"), None)
    replenishment_data = (
        load_yaml(replenishment_file)
        if replenishment_file and replenishment_file.exists() else None
    )

    warehouse_data = load_yaml(warehouse_file)
    storage_data = load_yaml(storage_file)
    extension_data = [
        load_yaml(path) for path in collect_extension_imports(warehouse_file)
        if path.exists()
    ]
    points, warnings = compile_storage_types(storage_data)
    points += expand_storage_point_refs(storage_data, wcs_data)
    errors = find_duplicate_ids(points)

    counts: dict[str, int] = {}
    for point in points:
        group = point_group(point)
        counts[group] = counts.get(group, 0) + 1

    print("Compiled storage_points per storage_type / ref_kind:")
    for group, count in counts.items():
        print(f"  {group}: {count}")
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
        entities = compile_entity_collections(
            warehouse_data, storage_data, wcs_data, lanes_data,
            movement_data, replenishment_data, points,
        )
        artifact = build_import_artifact(
            warehouse_data, points, extension_data, entities
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            yaml.safe_dump(artifact, f, sort_keys=False, allow_unicode=True)
        print(f"  content_hash: {artifact['artifact']['content_hash']}")
        total_entities = sum(artifact["artifact"]["entity_counts"].values())
        print(f"\n✅ Wrote {total_entities} desired-state entities to {args.output}")
    else:
        print("\nPass --output <file> to write the complete desired-state artifact.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
