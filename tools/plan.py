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
    "max_weight", "physical_bay", "position", "size", "storage_type",
}


def load_artifact(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: artifact must be a YAML object")
    required = {"api_version", "metadata", "target", "import_policy", "artifact", "storage_points"}
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"{path}: missing fields: {', '.join(missing)}")
    sorted_points = sorted(data["storage_points"], key=lambda point: point.get("id", ""))
    desired_state = {
        "api_version": data["api_version"],
        "dataset_id": data["metadata"].get("dataset_id"),
        "target": data["target"],
        "storage_points": sorted_points,
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
    if data["artifact"].get("entity_count") != len(data["storage_points"]):
        raise ValueError(f"{path}: artifact.entity_count does not match storage_points")
    return data


def index_points(artifact: dict, path: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for point in artifact["storage_points"]:
        point_id = point.get("id")
        if not point_id:
            raise ValueError(f"{path}: storage_point without id")
        if point_id in result:
            raise ValueError(f"{path}: duplicate storage_point id '{point_id}'")
        result[point_id] = point
    return result


def field_changes(current: dict, desired: dict) -> dict:
    return {
        field: {"from": current.get(field), "to": desired.get(field)}
        for field in sorted(set(current) | set(desired))
        if current.get(field) != desired.get(field)
    }


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

    current_points = index_points(current, current_path)
    desired_points = index_points(desired, desired_path)
    creates, updates, deactivations, conflicts = [], [], [], []

    for point_id in sorted(desired_points.keys() - current_points.keys()):
        creates.append({
            "entity": "storage_point", "id": point_id,
            "classification": "safe", "desired": desired_points[point_id],
        })

    for point_id in sorted(desired_points.keys() & current_points.keys()):
        changes = field_changes(current_points[point_id], desired_points[point_id])
        if changes:
            classification = "operational" if OPERATIONAL_FIELDS.intersection(changes) else "safe"
            updates.append({
                "entity": "storage_point", "id": point_id,
                "classification": classification, "changes": changes,
            })

    removal_policy = desired["import_policy"]["removal_policy"]
    for point_id in sorted(current_points.keys() - desired_points.keys()):
        entry = {
            "entity": "storage_point", "id": point_id,
            "classification": "destructive",
            "reason": "Object is absent from desired managed snapshot",
        }
        if removal_policy == "deactivate":
            deactivations.append(entry)
        else:
            conflicts.append({**entry, "reason": f"Removal rejected by policy for '{point_id}'"})

    unchanged = len(current_points.keys() & desired_points.keys()) - len(updates)
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
            "unchanged": unchanged,
        },
        "creates": creates, "updates": updates,
        "deactivations": deactivations, "conflicts": conflicts,
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
