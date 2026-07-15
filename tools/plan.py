#!/usr/bin/env python3
"""Create a safe reconciliation plan between two compiled artifacts.

The first artifact is the currently applied managed snapshot and the second
the desired snapshot. This tool never mutates a WMS and never plans a physical
delete; missing desired objects are deactivated or rejected by policy.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml


OPERATIONAL_FIELDS = {
    "allowed_load_unit_types", "blocked", "blocked_reason",
    "capacity_per_point", "capacity_volume", "controller", "layout_variant",
    "channel", "channel_depth", "channel_position", "entry_side", "exit_side",
    "max_weight", "physical_bay", "position", "size", "storage_type", "zone",
}

OPERATIONAL_ENTITY_TYPES = {
    "activity_area", "channel", "controller", "conveyor_main", "conveyor_segment",
    "door", "equipment", "lane", "movement_rule", "replenishment_strategy",
    "reporting_point", "section", "storage_type", "telegram_action",
    "work_center",
}


def load_artifact(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: artifact must be a YAML object")
    required = {
        "api_version", "metadata", "target", "import_policy",
        "artifact", "entities", "extensions",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"{path}: missing fields: {', '.join(missing)}")
    normalized_entities = {
        entity_type: sorted(items, key=lambda item: item.get("id", ""))
        for entity_type, items in sorted(data["entities"].items())
    }
    desired_state = {
        "api_version": data["api_version"],
        "dataset_id": data["metadata"].get("dataset_id"),
        "target": data["target"],
        "entities": normalized_entities,
        "extensions": data["extensions"],
    }
    canonical = json.dumps(
        desired_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    actual_hash = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    declared_hash = data["artifact"].get("content_hash")
    if declared_hash != actual_hash:
        raise ValueError(
            f"{path}: content_hash mismatch: declared={declared_hash!r}, actual={actual_hash!r}"
        )
    actual_counts = {
        entity_type: len(items) for entity_type, items in normalized_entities.items()
    }
    if data["artifact"].get("entity_counts") != actual_counts:
        raise ValueError(f"{path}: artifact.entity_counts does not match entities")
    extension_count = sum(
        len(item.get("extension", {}).get("records", []))
        for item in data["extensions"]
    )
    if data["artifact"].get("extension_record_count") != extension_count:
        raise ValueError(f"{path}: artifact.extension_record_count does not match extensions")
    return data


def index_entities(artifact: dict, path: Path) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for entity_type, items in artifact["entities"].items():
        for entity in items:
            entity_id = entity.get("id")
            if not entity_id:
                raise ValueError(f"{path}: {entity_type} without id")
            key = (entity_type, entity_id)
            if key in result:
                raise ValueError(f"{path}: duplicate {entity_type} id '{entity_id}'")
            result[key] = entity
    return result


def field_changes(current: dict, desired: dict) -> dict:
    return {
        field: {"from": current.get(field), "to": desired.get(field)}
        for field in sorted(set(current) | set(desired))
        if current.get(field) != desired.get(field)
    }


def index_extension_records(artifact: dict) -> dict[tuple[str, str, str], dict]:
    records: dict[tuple[str, str, str], dict] = {}
    for item in artifact.get("extensions", []):
        extension = item["extension"]
        namespace = extension["namespace"]
        version = extension["version"]
        for record in extension.get("records", []):
            key = (namespace, record["entity_type"], record["entity_id"])
            records[key] = {
                "extension_version": version,
                "payload": record["payload"],
            }
    return records


def build_plan(current: dict, desired: dict, current_path: Path, desired_path: Path) -> dict:
    if current["api_version"] != desired["api_version"]:
        raise ValueError("api_version mismatch: migrate artifacts before comparison")
    current_dataset = current["metadata"].get("dataset_id")
    desired_dataset = desired["metadata"].get("dataset_id")
    if current_dataset != desired_dataset:
        raise ValueError(
            f"dataset_id mismatch: current={current_dataset!r}, desired={desired_dataset!r}"
        )
    if current["target"] != desired["target"]:
        raise ValueError("target mismatch: artifacts must address the same WMS scope")

    current_entities = index_entities(current, current_path)
    desired_entities = index_entities(desired, desired_path)
    creates, updates, deactivations, conflicts = [], [], [], []
    extension_creates, extension_updates, extension_removals = [], [], []

    for key in sorted(desired_entities.keys() - current_entities.keys()):
        entity_type, entity_id = key
        creates.append({
            "entity": entity_type, "id": entity_id,
            "classification": (
                "operational" if entity_type in OPERATIONAL_ENTITY_TYPES else "safe"
            ),
            "desired": desired_entities[key],
        })

    for key in sorted(desired_entities.keys() & current_entities.keys()):
        entity_type, entity_id = key
        changes = field_changes(current_entities[key], desired_entities[key])
        if changes:
            classification = (
                "operational"
                if entity_type in OPERATIONAL_ENTITY_TYPES
                or OPERATIONAL_FIELDS.intersection(changes)
                else "safe"
            )
            updates.append({
                "entity": entity_type, "id": entity_id,
                "classification": classification, "changes": changes,
            })

    removal_policy = desired["import_policy"]["removal_policy"]
    for key in sorted(current_entities.keys() - desired_entities.keys()):
        entity_type, entity_id = key
        entry = {
            "entity": entity_type, "id": entity_id,
            "classification": "destructive",
            "reason": "Object is absent from desired managed snapshot",
        }
        if removal_policy == "deactivate":
            deactivations.append(entry)
        else:
            conflicts.append({**entry, "reason": f"Removal rejected by policy for '{entity_type}/{entity_id}'"})

    current_extensions = index_extension_records(current)
    desired_extensions = index_extension_records(desired)
    for key in sorted(desired_extensions.keys() - current_extensions.keys()):
        namespace, entity_type, entity_id = key
        extension_creates.append({
            "namespace": namespace, "entity_type": entity_type, "entity_id": entity_id,
            "classification": "vendor_specific", "desired": desired_extensions[key],
        })
    for key in sorted(desired_extensions.keys() & current_extensions.keys()):
        changes = field_changes(current_extensions[key], desired_extensions[key])
        if changes:
            namespace, entity_type, entity_id = key
            extension_updates.append({
                "namespace": namespace, "entity_type": entity_type, "entity_id": entity_id,
                "classification": "vendor_specific", "changes": changes,
            })
    for key in sorted(current_extensions.keys() - desired_extensions.keys()):
        namespace, entity_type, entity_id = key
        removal = {
            "namespace": namespace, "entity_type": entity_type, "entity_id": entity_id,
            "classification": "destructive",
            "reason": "Extension record is absent from desired snapshot",
        }
        extension_removals.append(removal)
        if removal_policy == "reject":
            conflicts.append({
                **removal,
                "reason": "Extension record removal rejected by import policy",
            })

    unchanged = len(current_entities.keys() & desired_entities.keys()) - len(updates)
    return {
        "api_version": desired["api_version"],
        "dataset_id": desired_dataset,
        "target": desired["target"],
        "expected_current_hash": current["artifact"]["content_hash"],
        "desired_hash": desired["artifact"]["content_hash"],
        "requires_approval": desired["import_policy"]["require_plan_approval"],
        "summary": {
            "create": len(creates), "update": len(updates),
            "deactivate": len(deactivations), "conflict": len(conflicts),
            "extension_create": len(extension_creates),
            "extension_update": len(extension_updates),
            "extension_remove": len(extension_removals),
            "unchanged": unchanged,
        },
        "creates": creates, "updates": updates,
        "deactivations": deactivations, "conflicts": conflicts,
        "extension_creates": extension_creates,
        "extension_updates": extension_updates,
        "extension_removals": extension_removals,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current", type=Path)
    parser.add_argument("desired", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv[1:])
    try:
        current = load_artifact(args.current)
        desired = load_artifact(args.desired)
        plan = build_plan(current, desired, args.current, args.desired)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rendered = yaml.safe_dump(plan, sort_keys=False, allow_unicode=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote reconciliation plan to {args.output}")
    else:
        print(rendered, end="")
    return 1 if plan["conflicts"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
