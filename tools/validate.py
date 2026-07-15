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
    - Check: for movement_policy=explicit_only, a matching movement_rule
      must exist for every lane
"""

import sys
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

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


def _build_schema_registry() -> Registry:
    """Pre-load all local schemas into a referencing.Registry."""
    resources = []
    for schema_file in SCHEMA_DIR.glob("*.json"):
        schema_data = json.loads(schema_file.read_text(encoding="utf-8"))
        uri = schema_data.get("$id") or f"{SCHEMA_DIR.as_uri()}/{schema_file.name}"
        resources.append((uri, Resource.from_contents(schema_data, default_specification=DRAFT7)))
    return Registry().with_resources(resources)


SCHEMA_REGISTRY: Registry = _build_schema_registry()


def make_validator(schema_name: str) -> Draft7Validator:
    schema = load_schema(schema_name)
    return Draft7Validator(schema, registry=SCHEMA_REGISTRY)


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


def validate_file(path: Path, schema_name: str) -> list[str]:
    errors = []
    data = load_yaml(path)
    if data is None:
        return [f"{path}: File is empty or invalid."]

    validator = make_validator(schema_name)
    for err in validator.iter_errors(data):
        loc = " -> ".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{path}: [{loc}] {err.message}")
    return errors


def duplicate_id_errors(items: list[dict], context: str, path: Path) -> list[str]:
    """Reports duplicate non-empty IDs in an entity collection."""
    seen: set[str] = set()
    errors: list[str] = []
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        if item_id in seen:
            errors.append(f"{path}: duplicate {context} id '{item_id}'")
        seen.add(item_id)
    return errors


def validate_storage_types(path: Path) -> list[str]:
    errors = validate_file(path, "storage.schema.json")
    data = load_yaml(path)
    storage_types = data.get("storage_types", [])
    errors += duplicate_id_errors(storage_types, "storage_type", path)
    for st in storage_types:
        errors += duplicate_id_errors(st.get("sections", []), f"section in storage_type '{st.get('id', '?')}'", path)
        errors += duplicate_id_errors(st.get("layout_variants", []), f"layout_variant in storage_type '{st.get('id', '?')}'", path)
        for field in ("storage_points", "exceptions"):
            coordinates = [entry.get("coordinate") for entry in st.get(field, []) if entry.get("coordinate")]
            duplicates = sorted({value for value in coordinates if coordinates.count(value) > 1})
            for coordinate in duplicates:
                errors.append(
                    f"{path}: storage_type '{st.get('id', '?')}': duplicate {field} coordinate '{coordinate}'"
                )
    errors += duplicate_id_errors(data.get("activity_areas", []), "activity_area", path)
    errors += duplicate_id_errors(data.get("work_centers", []), "work_center", path)
    return errors


def validate_doors(path: Path) -> list[str]:
    errors = []
    data = load_yaml(path)
    schema = load_schema("door.schema.json")
    validator = Draft7Validator(schema)
    doors = data.get("doors", [])
    errors += duplicate_id_errors(doors, "door", path)
    for door in doors:
        for err in validator.iter_errors(door):
            errors.append(f"{path}: door '{door.get('id', '?')}': {err.message}")
    return errors


def validate_movement_rules(path: Path) -> list[str]:
    errors = []
    data = load_yaml(path)
    schema = load_schema("movement-rule.schema.json")
    validator = Draft7Validator(schema)
    rules = data.get("movement_rules", [])
    errors += duplicate_id_errors(rules, "movement_rule", path)
    for rule in rules:
        for err in validator.iter_errors(rule):
            errors.append(f"{path}: movement_rule '{rule.get('id', '?')}': {err.message}")
    return errors


def _as_id_list(value) -> list[str]:
    """Normalizes a movement-rule endpoint field that may be a single id
    or a list of ids (schemas/movement-rule.schema.json 'idOrIds')."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _endpoint_controller(
    endpoint: dict | None,
    storage_controllers: dict,
    reporting_point_controllers: dict,
) -> str | None:
    """Resolves the controller an endpoint sits under, or None. Only
    storage_type and reporting_point endpoints carry a controller;
    work_centers/doors/activity_areas don't (they are manual/logical).
    If an endpoint lists several ids, they must all resolve to the same
    controller for the endpoint to count as sitting under one - a mixed
    group can't be treated as a single automated leg."""
    if not isinstance(endpoint, dict):
        return None

    rps = _as_id_list(endpoint.get("reporting_point"))
    if rps:
        controllers = {reporting_point_controllers.get(rp) for rp in rps}
        return controllers.pop() if len(controllers) == 1 else None

    sts = [st for st in _as_id_list(endpoint.get("storage_type")) if st != "*"]
    if sts:
        controllers = {storage_controllers.get(st) for st in sts}
        return controllers.pop() if len(controllers) == 1 else None

    return None


def check_execution_consistency(
    movement_path: Path,
    storage_data: dict | None,
    wcs_data: dict | None,
) -> list[str]:
    """For every movement_rule that declares 'execution', checks it against
    what the endpoints imply: 'automated' iff both endpoints sit under the
    same controller, else 'manual'. Flags contradictions."""
    errors: list[str] = []
    movement_data = load_yaml(movement_path)
    if movement_data is None:
        return errors

    storage_controllers = {
        st.get("id"): st.get("controller")
        for st in (storage_data or {}).get("storage_types", [])
    }
    reporting_point_controllers = {
        rp.get("id"): rp.get("controller")
        for rp in (wcs_data or {}).get("reporting_points", [])
    }

    for rule in movement_data.get("movement_rules", []):
        declared = rule.get("execution")
        if declared is None:
            continue
        c_from = _endpoint_controller(rule.get("from"), storage_controllers, reporting_point_controllers)
        c_to = _endpoint_controller(rule.get("to"), storage_controllers, reporting_point_controllers)
        implied = "automated" if (c_from and c_to and c_from == c_to) else "manual"
        if declared != implied:
            errors.append(
                f"{movement_path}: movement_rule '{rule.get('id', '?')}': "
                f"execution '{declared}' contradicts the endpoints "
                f"(from controller={c_from!r}, to controller={c_to!r} -> "
                f"implies '{implied}'). 'automated' requires both endpoints "
                f"under the same controller."
            )
    return errors


def validate_element_catalog(path: Path, schema_name: str, list_key: str) -> list[str]:
    errors = []
    data = load_yaml(path)
    if data is None:
        return [f"{path}: File is empty or invalid."]
    schema = load_schema(schema_name)
    validator = Draft7Validator(schema)
    items = data.get(list_key, [])
    errors += duplicate_id_errors(items, list_key.rstrip("s"), path)
    for item in items:
        for err in validator.iter_errors(item):
            errors.append(f"{path}: {list_key} '{item.get('id', '?')}': {err.message}")
    return errors


def validate_replenishment(path: Path) -> list[str]:
    errors = []
    data = load_yaml(path)
    schema = load_schema("replenishment-strategy.schema.json")
    validator = Draft7Validator(schema)
    strategies = data.get("replenishment_strategies", [])
    errors += duplicate_id_errors(strategies, "replenishment_strategy", path)
    for strat in strategies:
        for err in validator.iter_errors(strat):
            errors.append(f"{path}: replenishment_strategy '{strat.get('id', '?')}': {err.message}")
    return errors


def validate_wcs(path: Path) -> list[str]:
    errors = validate_file(path, "wcs.schema.json")
    data = load_yaml(path) or {}
    for key, context in (
        ("controller_definitions", "controller_definition"),
        ("reporting_points", "reporting_point"),
        ("equipment", "equipment"),
    ):
        errors += duplicate_id_errors(data.get(key, []), context, path)
    for controller in data.get("controller_definitions", []):
        errors += duplicate_id_errors(
            controller.get("channels", []),
            f"channel in controller '{controller.get('id', '?')}'",
            path,
        )
    return errors


def validate_lanes(path: Path) -> list[str]:
    errors = validate_file(path, "lanes.schema.json")
    data = load_yaml(path) or {}
    errors += duplicate_id_errors(data.get("lanes", []), "lane", path)
    errors += duplicate_id_errors(data.get("conveyor_segments", []), "conveyor_segment", path)
    return errors


def collect_element_ids() -> dict[str, set[str]]:
    """Loads all element catalogs and returns a mapping of list_key -> set of known IDs.
    Only catalogs whose files exist are included; missing files are silently skipped."""
    ids: dict[str, set[str]] = {}
    for filename, (_schema_name, list_key) in ELEMENT_CATALOGS.items():
        catalog_file = ELEMENTS_DIR / filename
        if catalog_file.exists():
            data = load_yaml(catalog_file)
            if data:
                ids[list_key] = {
                    item.get("id")
                    for item in data.get(list_key, [])
                    if item.get("id")
                }
    return ids


def check_storage_refs(
    path: Path,
    ctrl_ids: set[str],
    element_ids: dict[str, set[str]],
) -> list[str]:
    """Checks referential integrity in storage.yaml:
    - storage_type.controller -> controller_definitions.id (wcs.yaml)
    - storage_type.default_attributes.allowed_load_unit_types -> load_unit_types.id
    - storage_type.exceptions[].blocked_reason -> blocking_reasons.id
    """
    errors: list[str] = []
    data = load_yaml(path)
    if not data:
        return errors

    lut_ids = element_ids.get("load_unit_types", set())
    br_ids = element_ids.get("blocking_reasons", set())

    for st in data.get("storage_types", []):
        st_id = st.get("id", "?")

        ctrl = st.get("controller")
        if ctrl and ctrl_ids and ctrl not in ctrl_ids:
            errors.append(
                f"{path}: storage_type '{st_id}': controller '{ctrl}' not found in wcs.yaml controller_definitions"
            )

        for lut in (st.get("default_attributes") or {}).get("allowed_load_unit_types", []):
            if lut_ids and lut not in lut_ids:
                errors.append(
                    f"{path}: storage_type '{st_id}': default_attributes.allowed_load_unit_types '{lut}'"
                    f" not found in elements/load_unit_types.yaml"
                )

        for exc in st.get("exceptions", []):
            reason = exc.get("blocked_reason")
            if reason and br_ids and reason not in br_ids:
                errors.append(
                    f"{path}: storage_type '{st_id}': exception at '{exc.get('coordinate', '?')}':"
                    f" blocked_reason '{reason}' not found in elements/blocking_reasons.yaml"
                )

    return errors


def check_door_staging_refs(path: Path) -> list[str]:
    """Checks that door.staging_section references a valid storage_type section
    (format 'STORAGE_TYPE_ID.SECTION_ID'), both defined in the same storage.yaml."""
    errors: list[str] = []
    data = load_yaml(path)
    if not data:
        return errors

    sections: dict[str, set[str]] = {}
    for st in data.get("storage_types", []):
        st_id = st.get("id")
        if st_id:
            sections[st_id] = {sec.get("id") for sec in st.get("sections", []) if sec.get("id")}

    for door in data.get("doors", []):
        door_id = door.get("id", "?")
        staging = door.get("staging_section")
        if not staging:
            continue
        parts = str(staging).split(".", 1)
        if len(parts) != 2:
            errors.append(
                f"{path}: door '{door_id}': staging_section '{staging}' is not in 'STORAGE_TYPE_ID.SECTION_ID' format"
            )
            continue
        st_id, sec_id = parts
        if st_id not in sections:
            errors.append(
                f"{path}: door '{door_id}': staging_section '{staging}': storage_type '{st_id}' not found"
            )
        elif sec_id not in sections[st_id]:
            errors.append(
                f"{path}: door '{door_id}': staging_section '{staging}':"
                f" section '{sec_id}' not found in storage_type '{st_id}'"
            )

    return errors


def check_wcs_refs(path: Path, element_ids: dict[str, set[str]]) -> list[str]:
    """Checks referential integrity within wcs.yaml:
    - reporting_point.controller -> controller_definitions.id
    - equipment.type -> equipment_types.id
    """
    errors: list[str] = []
    data = load_yaml(path)
    if not data:
        return errors

    ctrl_ids = {cd.get("id") for cd in data.get("controller_definitions", []) if cd.get("id")}
    et_ids = element_ids.get("equipment_types", set())

    for rp in data.get("reporting_points", []):
        rp_id = rp.get("id", "?")
        ctrl = rp.get("controller")
        if ctrl and ctrl not in ctrl_ids:
            errors.append(
                f"{path}: reporting_point '{rp_id}': controller '{ctrl}' not found in controller_definitions"
            )

    for eq in data.get("equipment", []):
        eq_id = eq.get("id", "?")
        eq_type = eq.get("type")
        if eq_type and et_ids and eq_type not in et_ids:
            errors.append(
                f"{path}: equipment '{eq_id}': type '{eq_type}' not found in elements/equipment_types.yaml"
            )

    return errors


def check_activity_area_refs(path: Path) -> list[str]:
    """Checks that activity_area.bins_from entries reference valid storage_types
    and sections, both defined in the same storage.yaml.
    Format: 'STORAGE_TYPE_ID' or 'STORAGE_TYPE_ID.SECTION_ID'."""
    errors: list[str] = []
    data = load_yaml(path)
    if not data:
        return errors

    sections: dict[str, set[str]] = {}
    storage_type_ids: set[str] = set()
    for st in data.get("storage_types", []):
        st_id = st.get("id")
        if st_id:
            storage_type_ids.add(st_id)
            sections[st_id] = {sec.get("id") for sec in st.get("sections", []) if sec.get("id")}

    for aa in data.get("activity_areas", []):
        aa_id = aa.get("id", "?")
        for ref in aa.get("bins_from", []):
            parts = str(ref).split(".", 1)
            st_id = parts[0]
            if st_id not in storage_type_ids:
                errors.append(
                    f"{path}: activity_area '{aa_id}': bins_from '{ref}': storage_type '{st_id}' not found"
                )
            elif len(parts) == 2:
                sec_id = parts[1]
                if sec_id not in sections.get(st_id, set()):
                    errors.append(
                        f"{path}: activity_area '{aa_id}': bins_from '{ref}':"
                        f" section '{sec_id}' not found in storage_type '{st_id}'"
                    )

    return errors


def _validate_endpoint_refs(
    path: Path,
    context: str,
    endpoint: dict | None,
    storage_type_ids: set[str],
    sections: dict[str, set[str]],
    activity_area_ids: set[str],
    work_center_ids: set[str],
    reporting_point_ids: set[str],
) -> list[str]:
    """Validates all cross-reference fields of a single movement-rule endpoint.
    Each field may be a single id or a list of ids (schemas/movement-rule.schema.json
    'idOrIds', e.g. several ports fed by one rule) - every id is checked."""
    errors: list[str] = []
    if not isinstance(endpoint, dict):
        return errors

    for st in _as_id_list(endpoint.get("storage_type")):
        if st == "*":
            continue
        if storage_type_ids and st not in storage_type_ids:
            errors.append(f"{path}: {context}: storage_type '{st}' not found in storage.yaml")
        else:
            for sec in _as_id_list(endpoint.get("section")):
                if st in storage_type_ids and sec not in sections.get(st, set()):
                    errors.append(
                        f"{path}: {context}: section '{sec}' not found in storage_type '{st}'"
                    )

    for aa in _as_id_list(endpoint.get("activity_area")):
        if activity_area_ids and aa not in activity_area_ids:
            errors.append(f"{path}: {context}: activity_area '{aa}' not found in storage.yaml")

    for wc in _as_id_list(endpoint.get("work_center")):
        if work_center_ids and wc not in work_center_ids:
            errors.append(f"{path}: {context}: work_center '{wc}' not found in storage.yaml")

    for rp in _as_id_list(endpoint.get("reporting_point")):
        if reporting_point_ids and rp not in reporting_point_ids:
            errors.append(f"{path}: {context}: reporting_point '{rp}' not found in wcs.yaml")

    return errors


def check_movement_rule_refs(
    movement_path: Path,
    storage_data: dict | None,
    wcs_data: dict | None,
    element_ids: dict[str, set[str]],
) -> list[str]:
    """Checks referential integrity in movement_rules.yaml:
    - from/to/exclude_to endpoint fields -> storage_type, section, activity_area,
      work_center, reporting_point ids (from storage.yaml / wcs.yaml)
    - allowed_load_unit_types -> load_unit_types.id (elements/)
    - trigger -> process_types.id (elements/)
    """
    errors: list[str] = []
    data = load_yaml(movement_path)
    if not data:
        return errors

    storage_type_ids: set[str] = set()
    sections: dict[str, set[str]] = {}
    activity_area_ids: set[str] = set()
    work_center_ids: set[str] = set()
    reporting_point_ids: set[str] = set()

    for st in (storage_data or {}).get("storage_types", []):
        st_id = st.get("id")
        if st_id:
            storage_type_ids.add(st_id)
            sections[st_id] = {sec.get("id") for sec in st.get("sections", []) if sec.get("id")}
    for aa in (storage_data or {}).get("activity_areas", []):
        if aa.get("id"):
            activity_area_ids.add(aa["id"])
    for wc in (storage_data or {}).get("work_centers", []):
        if wc.get("id"):
            work_center_ids.add(wc["id"])
    for rp in (wcs_data or {}).get("reporting_points", []):
        if rp.get("id"):
            reporting_point_ids.add(rp["id"])

    lut_ids = element_ids.get("load_unit_types", set())
    pt_ids = element_ids.get("process_types", set())

    for rule in data.get("movement_rules", []):
        rule_id = rule.get("id", "?")
        for role in ("from", "to", "exclude_to"):
            errors += _validate_endpoint_refs(
                movement_path,
                f"movement_rule '{rule_id}'.{role}",
                rule.get(role),
                storage_type_ids,
                sections,
                activity_area_ids,
                work_center_ids,
                reporting_point_ids,
            )

        for lut in rule.get("allowed_load_unit_types", []):
            if lut_ids and lut not in lut_ids:
                errors.append(
                    f"{movement_path}: movement_rule '{rule_id}':"
                    f" allowed_load_unit_types '{lut}' not found in elements/load_unit_types.yaml"
                )

        trigger = rule.get("trigger")
        if trigger and pt_ids and trigger not in pt_ids:
            errors.append(
                f"{movement_path}: movement_rule '{rule_id}':"
                f" trigger '{trigger}' not found in elements/process_types.yaml"
            )

    return errors


def check_replenishment_refs(
    path: Path,
    storage_data: dict | None,
) -> list[str]:
    """Checks referential integrity in replenishment.yaml:
    - source/destination.storage_type -> storage_type.id
    - source/destination.section -> section.id within that storage_type
    - source/destination.activity_area -> activity_area.id
    """
    errors: list[str] = []
    data = load_yaml(path)
    if not data or not storage_data:
        return errors

    storage_type_ids: set[str] = set()
    sections: dict[str, set[str]] = {}
    activity_area_ids: set[str] = set()

    for st in storage_data.get("storage_types", []):
        st_id = st.get("id")
        if st_id:
            storage_type_ids.add(st_id)
            sections[st_id] = {sec.get("id") for sec in st.get("sections", []) if sec.get("id")}
    for aa in storage_data.get("activity_areas", []):
        if aa.get("id"):
            activity_area_ids.add(aa["id"])

    for strat in data.get("replenishment_strategies", []):
        strat_id = strat.get("id", "?")
        for role in ("source", "destination"):
            endpoint = strat.get(role)
            if not isinstance(endpoint, dict):
                continue

            st = endpoint.get("storage_type")
            if st and storage_type_ids and st not in storage_type_ids:
                errors.append(
                    f"{path}: replenishment_strategy '{strat_id}':"
                    f" {role}.storage_type '{st}' not found in storage.yaml"
                )

            sec = endpoint.get("section")
            if sec and st and st in storage_type_ids and sec not in sections.get(st, set()):
                errors.append(
                    f"{path}: replenishment_strategy '{strat_id}':"
                    f" {role}.section '{sec}' not found in storage_type '{st}'"
                )

            aa = endpoint.get("activity_area")
            if aa and activity_area_ids and aa not in activity_area_ids:
                errors.append(
                    f"{path}: replenishment_strategy '{strat_id}':"
                    f" {role}.activity_area '{aa}' not found in storage.yaml"
                )

    return errors


def check_lane_refs(
    lanes_path: Path,
    storage_data: dict | None,
    wcs_data: dict | None,
) -> list[str]:
    """Checks referential integrity in lanes.yaml:
    - lane.connects -> storage_type / door / work_center ids
    - conveyor_segment.from/to -> storage_type / door / reporting_point / work_center ids
    - conveyor_main.connects -> storage_type / door / work_center ids
    """
    errors: list[str] = []
    data = load_yaml(lanes_path)
    if not data:
        return errors

    known_ids: set[str] = set()
    for st in (storage_data or {}).get("storage_types", []):
        if st.get("id"):
            known_ids.add(st["id"])
    for door in (storage_data or {}).get("doors", []):
        if door.get("id"):
            known_ids.add(door["id"])
    for wc in (storage_data or {}).get("work_centers", []):
        if wc.get("id"):
            known_ids.add(wc["id"])
    for rp in (wcs_data or {}).get("reporting_points", []):
        if rp.get("id"):
            known_ids.add(rp["id"])

    if not known_ids:
        return errors

    for lane in data.get("lanes", []):
        lane_id = lane.get("id", "?")
        for conn in lane.get("connects", []):
            if conn not in known_ids:
                errors.append(
                    f"{lanes_path}: lane '{lane_id}': connects '{conn}'"
                    f" not found in storage_types/doors/work_centers"
                )

    for seg in data.get("conveyor_segments", []):
        seg_id = seg.get("id", "?")
        for field in ("from", "to"):
            ref = seg.get(field)
            if ref and ref not in known_ids:
                errors.append(
                    f"{lanes_path}: conveyor_segment '{seg_id}': {field} '{ref}'"
                    f" not found in storage_types/doors/reporting_points/work_centers"
                )

    cm = data.get("conveyor_main")
    if isinstance(cm, dict):
        for conn in cm.get("connects", []):
            if conn not in known_ids:
                errors.append(
                    f"{lanes_path}: conveyor_main: connects '{conn}'"
                    f" not found in storage_types/doors/work_centers"
                )

    return errors


def validate_warehouse_file(warehouse_file: Path, element_ids: dict[str, set[str]] = {}) -> list[str]:
    """Validates a single building-level warehouse.yaml and everything it imports."""
    if not warehouse_file.exists():
        return [f"warehouse file missing: {warehouse_file}"]

    all_errors = validate_file(warehouse_file, "warehouse.schema.json")

    imports: dict[str, Path] = {}
    for imported in collect_imports(warehouse_file):
        if not imported.exists():
            all_errors.append(f"{warehouse_file}: imported file missing: {imported}")
            continue

        imports[imported.name] = imported
        name = imported.name
        if name == "storage.yaml":
            all_errors += validate_storage_types(imported)
            all_errors += validate_doors(imported)
        elif name == "movement_rules.yaml":
            all_errors += validate_movement_rules(imported)
        elif name == "replenishment.yaml":
            all_errors += validate_replenishment(imported)
        elif name == "lanes.yaml":
            all_errors += validate_lanes(imported)
        elif name == "wcs.yaml":
            all_errors += validate_wcs(imported)
        else:
            data = load_yaml(imported)
            if data is None:
                all_errors.append(f"{imported}: File is empty or invalid.")

    # Load shared data once for all cross-file checks below.
    storage_data = load_yaml(imports["storage.yaml"]) if "storage.yaml" in imports else {}
    wcs_data = load_yaml(imports["wcs.yaml"]) if "wcs.yaml" in imports else {}

    # Cross-file checks: run regardless of element_ids availability so that
    # within-file references (e.g. door -> staging section) are always verified.

    if "storage.yaml" in imports:
        ctrl_ids = {
            cd.get("id")
            for cd in (wcs_data or {}).get("controller_definitions", [])
            if cd.get("id")
        }
        all_errors += check_storage_refs(imports["storage.yaml"], ctrl_ids, element_ids)
        all_errors += check_door_staging_refs(imports["storage.yaml"])
        all_errors += check_activity_area_refs(imports["storage.yaml"])

    if "wcs.yaml" in imports:
        all_errors += check_wcs_refs(imports["wcs.yaml"], element_ids)

    if "movement_rules.yaml" in imports:
        all_errors += check_execution_consistency(
            imports["movement_rules.yaml"], storage_data, wcs_data
        )
        all_errors += check_movement_rule_refs(
            imports["movement_rules.yaml"], storage_data, wcs_data, element_ids
        )

    if "replenishment.yaml" in imports:
        all_errors += check_replenishment_refs(imports["replenishment.yaml"], storage_data)

    if "lanes.yaml" in imports:
        all_errors += check_lane_refs(imports["lanes.yaml"], storage_data, wcs_data)

    return all_errors


def validate_facility_file(facility_file: Path, element_ids: dict[str, set[str]] = {}) -> list[str]:
    """Validates a facility.yaml and cascades into every building it lists."""
    if not facility_file.exists():
        return [f"facility file missing: {facility_file}"]

    all_errors = validate_file(facility_file, "facility.schema.json")

    for building_file in collect_relative_refs(facility_file, "facility", "buildings"):
        all_errors += validate_warehouse_file(building_file, element_ids)

    return all_errors


def validate_company_file(company_file: Path, element_ids: dict[str, set[str]] = {}) -> list[str]:
    """Validates a company.yaml and cascades into every facility it lists."""
    all_errors = validate_file(company_file, "company.schema.json")

    for facility_file in collect_relative_refs(company_file, "company", "facilities"):
        all_errors += validate_facility_file(facility_file, element_ids)

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

    element_ids = collect_element_ids()

    data = load_yaml(target_file)
    if data is None:
        all_errors.append(f"{target_file}: File is empty or invalid.")
    elif "company" in data:
        all_errors += validate_company_file(target_file, element_ids)
    elif "facility" in data:
        all_errors += validate_facility_file(target_file, element_ids)
    elif "warehouse" in data:
        all_errors += validate_warehouse_file(target_file, element_ids)
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
