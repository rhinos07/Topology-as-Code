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

TEMPLATE_CATALOGS = {
    "rack_templates": "rack_templates",
    "lane_templates": "lane_templates",
    "workstation_templates": "workstation_templates",
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


def collect_extension_imports(warehouse_file: Path) -> list[Path]:
    data = load_yaml(warehouse_file)
    imports = data.get("warehouse", {}).get("extension_imports", [])
    return [warehouse_file.parent / rel for rel in imports]


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


def check_temperature_ranges(value, path: Path, location: str = "root") -> list[str]:
    errors: list[str] = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors += check_temperature_ranges(item, path, f"{location}[{index}]")
    elif isinstance(value, dict):
        if set(value) == {"min", "max", "unit"} and value.get("unit") == "C":
            if isinstance(value.get("min"), (int, float)) and isinstance(value.get("max"), (int, float)) and value["min"] > value["max"]:
                errors.append(f"{path}: {location}: temperature min must not exceed max")
        else:
            for key, item in value.items():
                errors += check_temperature_ranges(item, path, f"{location}.{key}")
    return errors


def validate_storage_types(path: Path) -> list[str]:
    errors = validate_file(path, "storage.schema.json")
    data = load_yaml(path)
    errors += check_temperature_ranges(data, path)
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
    validator = make_validator(schema_name)
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
        ("telegram_actions", "telegram_action"),
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
    template_file = ELEMENTS_DIR / "rack_templates.yaml"
    if template_file.exists():
        data = load_yaml(template_file) or {}
        for list_key in TEMPLATE_CATALOGS:
            ids[list_key] = {
                item.get("id") for item in data.get(list_key, []) if item.get("id")
            }
    return ids


def validate_template_catalogs(path: Path) -> list[str]:
    data = load_yaml(path) or {}
    errors: list[str] = validate_file(path, "rack-templates.schema.json")
    errors += check_temperature_ranges(data, path)
    unknown = set(data) - set(TEMPLATE_CATALOGS)
    for key in sorted(unknown):
        errors.append(f"{path}: unknown template catalog '{key}'")
    for list_key, context in TEMPLATE_CATALOGS.items():
        errors += duplicate_id_errors(data.get(list_key, []), context.rstrip("s"), path)
    return errors


def check_storage_refs(
    path: Path,
    ctrl_ids: set[str],
    element_ids: dict[str, set[str]],
) -> list[str]:
    """Checks referential integrity in storage.yaml:
    - storage_type.controller -> controller_definitions.id (wcs.yaml)
    - storage_type.default_attributes.allowed_load_unit_types -> load_unit_types.id
    - storage points/exceptions/layout variants -> load unit and blocking catalogs
    - work_center.workstation_template -> workstation_templates.id
    """
    errors: list[str] = []
    data = load_yaml(path)
    if not data:
        return errors

    lut_ids = element_ids.get("load_unit_types", set())
    br_ids = element_ids.get("blocking_reasons", set())
    hazmat_ids = element_ids.get("hazmat_classes", set())
    workstation_ids = element_ids.get("workstation_templates", set())

    for st in data.get("storage_types", []):
        st_id = st.get("id", "?")

        ctrl = st.get("controller")
        if ctrl and ctrl not in ctrl_ids:
            errors.append(
                f"{path}: storage_type '{st_id}': controller '{ctrl}' not found in wcs.yaml controller_definitions"
            )

        for lut in (st.get("default_attributes") or {}).get("allowed_load_unit_types", []):
            if lut not in lut_ids:
                errors.append(
                    f"{path}: storage_type '{st_id}': default_attributes.allowed_load_unit_types '{lut}'"
                    f" not found in elements/load_unit_types.yaml"
                )

        for hazmat in (st.get("default_attributes") or {}).get("hazmat_classes", []):
            if hazmat not in hazmat_ids:
                errors.append(
                    f"{path}: storage_type '{st_id}': default_attributes.hazmat_classes "
                    f"'{hazmat}' not found in elements/hazmat_classes.yaml"
                )

        for variant in st.get("layout_variants", []):
            lut = variant.get("load_unit_type")
            if lut and lut not in lut_ids:
                errors.append(
                    f"{path}: storage_type '{st_id}': layout_variant '{variant.get('id', '?')}' "
                    f"load_unit_type '{lut}' not found in elements/load_unit_types.yaml"
                )

        for field in ("storage_points", "exceptions"):
            for entry in st.get(field, []):
                for lut in entry.get("allowed_load_unit_types", []):
                    if lut not in lut_ids:
                        errors.append(
                            f"{path}: storage_type '{st_id}': {field} at "
                            f"'{entry.get('coordinate', '?')}': allowed_load_unit_types "
                            f"'{lut}' not found in elements/load_unit_types.yaml"
                        )

        for exc in [*st.get("storage_points", []), *st.get("exceptions", [])]:
            reason = exc.get("blocked_reason")
            if reason and reason not in br_ids:
                errors.append(
                    f"{path}: storage_type '{st_id}': exception at '{exc.get('coordinate', '?')}':"
                    f" blocked_reason '{reason}' not found in elements/blocking_reasons.yaml"
                )

    for wc in data.get("work_centers", []):
        template = wc.get("workstation_template")
        if template and template not in workstation_ids:
            errors.append(
                f"{path}: work_center '{wc.get('id', '?')}': workstation_template "
                f"'{template}' not found in elements/rack_templates.yaml workstation_templates"
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
    - equipment.type/controller/served_points -> referenced entities
    - telegram action points -> reporting_points.id
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
        if eq_type and eq_type not in et_ids:
            errors.append(
                f"{path}: equipment '{eq_id}': type '{eq_type}' not found in elements/equipment_types.yaml"
            )
        ctrl = eq.get("controller")
        if ctrl and ctrl not in ctrl_ids:
            errors.append(
                f"{path}: equipment '{eq_id}': controller '{ctrl}' not found in controller_definitions"
            )

    reporting_point_ids = {
        rp.get("id") for rp in data.get("reporting_points", []) if rp.get("id")
    }
    for eq in data.get("equipment", []):
        for point in eq.get("served_points", []):
            if point not in reporting_point_ids:
                errors.append(
                    f"{path}: equipment '{eq.get('id', '?')}': served_point '{point}' "
                    "not found in reporting_points"
                )

    for index, action in enumerate(data.get("telegram_actions", []), start=1):
        for field in ("from_point", "to_point", "next_target"):
            point = action.get(field)
            if point and point not in reporting_point_ids:
                errors.append(
                    f"{path}: telegram_action #{index}: {field} '{point}' "
                    "not found in reporting_points"
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
        if st not in storage_type_ids:
            errors.append(f"{path}: {context}: storage_type '{st}' not found in storage.yaml")
        else:
            for sec in _as_id_list(endpoint.get("section")):
                if st in storage_type_ids and sec not in sections.get(st, set()):
                    errors.append(
                        f"{path}: {context}: section '{sec}' not found in storage_type '{st}'"
                    )

    for aa in _as_id_list(endpoint.get("activity_area")):
        if aa not in activity_area_ids:
            errors.append(f"{path}: {context}: activity_area '{aa}' not found in storage.yaml")

    for wc in _as_id_list(endpoint.get("work_center")):
        if wc not in work_center_ids:
            errors.append(f"{path}: {context}: work_center '{wc}' not found in storage.yaml")

    for rp in _as_id_list(endpoint.get("reporting_point")):
        if rp not in reporting_point_ids:
            errors.append(f"{path}: {context}: reporting_point '{rp}' not found in wcs.yaml")

    return errors


def check_movement_rule_refs(
    movement_path: Path,
    storage_data: dict | None,
    wcs_data: dict | None,
    lanes_data: dict | None,
    element_ids: dict[str, set[str]],
) -> list[str]:
    """Checks referential integrity in movement_rules.yaml:
    - from/to/exclude_to endpoint fields -> storage_type, section, activity_area,
      work_center, reporting_point ids (from storage.yaml / wcs.yaml)
    - allowed_load_unit_types -> load_unit_types.id (elements/)
    - trigger -> process_types.id (elements/)
    - via_segment -> conveyor_segments.id (lanes.yaml)
    - applies_to_policy -> referenced storage_type movement policies
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
    segment_ids = {
        segment.get("id")
        for segment in (lanes_data or {}).get("conveyor_segments", [])
        if segment.get("id")
    }
    storage_policies = {
        st.get("id"): st.get("movement_policy")
        for st in (storage_data or {}).get("storage_types", [])
        if st.get("id")
    }

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
            if lut not in lut_ids:
                errors.append(
                    f"{movement_path}: movement_rule '{rule_id}':"
                    f" allowed_load_unit_types '{lut}' not found in elements/load_unit_types.yaml"
                )

        trigger = rule.get("trigger")
        if trigger and trigger not in pt_ids:
            errors.append(
                f"{movement_path}: movement_rule '{rule_id}':"
                f" trigger '{trigger}' not found in elements/process_types.yaml"
            )

        via_segment = rule.get("via_segment")
        if via_segment and via_segment not in segment_ids:
            errors.append(
                f"{movement_path}: movement_rule '{rule_id}': via_segment "
                f"'{via_segment}' not found in lanes.yaml conveyor_segments"
            )

        declared_policy = rule.get("applies_to_policy")
        if declared_policy:
            referenced_storage_types: set[str] = set()
            for role in ("from", "to"):
                referenced_storage_types.update(
                    st for st in _as_id_list((rule.get(role) or {}).get("storage_type"))
                    if st != "*"
                )
            actual_policies = {
                storage_policies.get(st_id) for st_id in referenced_storage_types
                if storage_policies.get(st_id)
            }
            if len(actual_policies) == 1 and declared_policy not in actual_policies:
                errors.append(
                    f"{movement_path}: movement_rule '{rule_id}': applies_to_policy "
                    f"'{declared_policy}' contradicts referenced storage_type policy "
                    f"'{next(iter(actual_policies))}'"
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
            if st and st not in storage_type_ids:
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
            if aa and aa not in activity_area_ids:
                errors.append(
                    f"{path}: replenishment_strategy '{strat_id}':"
                    f" {role}.activity_area '{aa}' not found in storage.yaml"
                )

    return errors


def check_lane_refs(
    lanes_path: Path,
    storage_data: dict | None,
    wcs_data: dict | None,
    element_ids: dict[str, set[str]],
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
        template = lane.get("template")
        if template and template not in element_ids.get("lane_templates", set()):
            errors.append(
                f"{lanes_path}: lane '{lane_id}': template '{template}' not found "
                "in elements/rack_templates.yaml lane_templates"
            )
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


def check_topology_id_uniqueness(
    storage_path: Path,
    storage_data: dict | None,
    wcs_data: dict | None,
) -> list[str]:
    """Checks the shared, untyped topology endpoint namespace."""
    owners: dict[str, str] = {}
    errors: list[str] = []
    collections = (
        ("storage_type", (storage_data or {}).get("storage_types", [])),
        ("door", (storage_data or {}).get("doors", [])),
        ("work_center", (storage_data or {}).get("work_centers", [])),
        ("reporting_point", (wcs_data or {}).get("reporting_points", [])),
    )
    for kind, items in collections:
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            previous = owners.get(item_id)
            if previous and previous != kind:
                errors.append(
                    f"{storage_path}: topology id '{item_id}' is used by both "
                    f"{previous} and {kind}; lane and conveyor references would be ambiguous"
                )
            owners[item_id] = kind
    return errors


def check_storage_coordinate_integrity(
    storage_path: Path,
    storage_data: dict | None,
    wcs_data: dict | None,
) -> list[str]:
    """Expands coordinate identities in memory and checks uniqueness/references."""
    errors: list[str] = []
    compiled_ids: set[str] = set()

    def register(st_id: str, coordinate: str) -> None:
        point_id = f"{st_id}.{coordinate}"
        if point_id in compiled_ids:
            errors.append(f"{storage_path}: duplicate compiled storage_point id '{point_id}'")
        compiled_ids.add(point_id)

    for st in (storage_data or {}).get("storage_types", []):
        st_id = st.get("id", "?")
        generated_coordinates: set[str] = set()
        try:
            if "storage_point_generator" in st:
                generator = st["storage_point_generator"]
                for aisle in range(1, generator.get("aisles", 1) + 1):
                    for stack in range(1, generator.get("stacks", 1) + 1):
                        for level in range(1, generator.get("levels", 1) + 1):
                            coordinate = generator["coordinate_pattern"].format(
                                aisle=aisle, stack=stack, level=level
                            )
                            generated_coordinates.add(coordinate)
                            register(st_id, coordinate)
            elif "layout_variants" in st:
                grid = st.get("layout_grid", {})
                for variant in st["layout_variants"]:
                    for aisle in range(1, grid.get("aisles", 1) + 1):
                        for bay in range(1, grid.get("bays", 1) + 1):
                            for slot in range(1, variant["positions_per_bay"] + 1):
                                coordinate = variant["coordinate_pattern"].format(
                                    aisle=aisle, bay=bay, slot=slot
                                )
                                register(st_id, coordinate)
            else:
                for point in st.get("storage_points", []):
                    register(st_id, point["coordinate"])
        except (KeyError, ValueError, IndexError) as exc:
            errors.append(
                f"{storage_path}: storage_type '{st_id}': invalid coordinate_pattern: {exc}"
            )

        for exception in st.get("exceptions", []):
            coordinate = exception.get("coordinate")
            if coordinate and coordinate not in generated_coordinates:
                errors.append(
                    f"{storage_path}: storage_type '{st_id}': exception coordinate "
                    f"'{coordinate}' does not match a generated storage point"
                )

    for kind, items in (
        ("work_center", (storage_data or {}).get("work_centers", [])),
        ("reporting_point", (wcs_data or {}).get("reporting_points", [])),
    ):
        for item in items:
            if not item.get("storage_point_ref"):
                continue
            item_id = item.get("id")
            if item_id in compiled_ids:
                errors.append(
                    f"{storage_path}: {kind} storage_point_ref id '{item_id}' collides "
                    "with a compiled storage_point id"
                )
            compiled_ids.add(item_id)
    return errors


def _axis_values(selector: dict | None, maximum: int) -> list[int]:
    if not selector:
        return list(range(1, maximum + 1))
    if not isinstance(selector, dict):
        return []
    if "values" in selector:
        return selector["values"] if isinstance(selector["values"], list) else []
    if "from" in selector and "to" in selector:
        return list(range(selector["from"], selector["to"] + 1))
    return []


def check_section_membership(path: Path, storage_data: dict | None) -> list[str]:
    errors: list[str] = []
    for st in (storage_data or {}).get("storage_types", []):
        sections = st.get("sections", [])
        if not sections:
            continue
        st_id = st.get("id", "?")
        section_ids = {section.get("id") for section in sections if section.get("id")}
        coordinates: set[str] = set()
        selector_matches: dict[str, set[str]] = {section_id: set() for section_id in section_ids}

        generator = st.get("storage_point_generator")
        variants = st.get("layout_variants")
        if generator:
            for aisle in range(1, generator.get("aisles", 1) + 1):
                for stack in range(1, generator.get("stacks", 1) + 1):
                    for level in range(1, generator.get("levels", 1) + 1):
                        coordinates.add(generator["coordinate_pattern"].format(
                            aisle=aisle, stack=stack, level=level
                        ))
        elif variants:
            grid = st.get("layout_grid", {})
            for variant in variants:
                for aisle in range(1, grid.get("aisles", 1) + 1):
                    for bay in range(1, grid.get("bays", 1) + 1):
                        for slot in range(1, variant["positions_per_bay"] + 1):
                            coordinates.add(variant["coordinate_pattern"].format(
                                aisle=aisle, bay=bay, slot=slot
                            ))
        else:
            coordinates = {
                point.get("coordinate") for point in st.get("storage_points", [])
                if point.get("coordinate")
            }

        for section in sections:
            section_id = section["id"]
            selector = section.get("selector")
            if not selector:
                continue
            if "coordinates" in selector:
                selected = set(selector["coordinates"])
                unknown = selected - coordinates
                for coordinate in sorted(unknown):
                    errors.append(
                        f"{path}: storage_type '{st_id}' section '{section_id}': "
                        f"selector coordinate '{coordinate}' does not exist"
                    )
                selector_matches[section_id] = selected & coordinates
                continue

            if generator:
                unsupported = set(selector).intersection({"bays", "slots"})
                maxima = {
                    "aisles": generator.get("aisles", 1),
                    "stacks": generator.get("stacks", 1),
                    "levels": generator.get("levels", 1),
                }
                axes = ("aisles", "stacks", "levels")
                patterns = [(None, generator["coordinate_pattern"])]
            elif variants:
                unsupported = set(selector).intersection({"stacks", "levels"})
                grid = st.get("layout_grid", {})
                maxima = {"aisles": grid.get("aisles", 1), "bays": grid.get("bays", 1)}
                axes = ("aisles", "bays", "slots")
                patterns = [(variant["positions_per_bay"], variant["coordinate_pattern"]) for variant in variants]
            else:
                unsupported = set(selector)
                maxima, axes, patterns = {}, (), []
            if unsupported:
                errors.append(
                    f"{path}: storage_type '{st_id}' section '{section_id}': selector axes "
                    f"{sorted(unsupported)} are not available for this point definition"
                )
                continue
            for axis, axis_selector in selector.items():
                if isinstance(axis_selector, dict) and "from" in axis_selector and "to" in axis_selector and axis_selector["from"] > axis_selector["to"]:
                    errors.append(
                        f"{path}: storage_type '{st_id}' section '{section_id}': "
                        f"selector {axis}.from must not exceed to"
                    )
            selected: set[str] = set()
            if generator:
                for aisle in _axis_values(selector.get("aisles"), maxima["aisles"]):
                    for stack in _axis_values(selector.get("stacks"), maxima["stacks"]):
                        for level in _axis_values(selector.get("levels"), maxima["levels"]):
                            if 1 <= aisle <= maxima["aisles"] and 1 <= stack <= maxima["stacks"] and 1 <= level <= maxima["levels"]:
                                selected.add(generator["coordinate_pattern"].format(
                                    aisle=aisle, stack=stack, level=level
                                ))
            elif variants:
                for positions, pattern in patterns:
                    for aisle in _axis_values(selector.get("aisles"), maxima["aisles"]):
                        for bay in _axis_values(selector.get("bays"), maxima["bays"]):
                            for slot in _axis_values(selector.get("slots"), positions):
                                if 1 <= aisle <= maxima["aisles"] and 1 <= bay <= maxima["bays"] and 1 <= slot <= positions:
                                    selected.add(pattern.format(aisle=aisle, bay=bay, slot=slot))
            selector_matches[section_id] = selected

        direct: dict[str, str] = {}
        default_section = (st.get("default_attributes") or {}).get("section")
        if default_section:
            direct.update({coordinate: default_section for coordinate in coordinates})
        for entry in [*st.get("storage_points", []), *st.get("exceptions", [])]:
            if entry.get("section"):
                direct[entry["coordinate"]] = entry["section"]

        final_members: dict[str, set[str]] = {section_id: set() for section_id in section_ids}
        for coordinate in coordinates:
            if coordinate in direct:
                membership = {direct[coordinate]}
            else:
                membership = {
                    section_id for section_id, selected in selector_matches.items()
                    if coordinate in selected
                }
            unknown = membership - section_ids
            for section_id in sorted(unknown):
                errors.append(
                    f"{path}: storage_type '{st_id}' coordinate '{coordinate}': "
                    f"section '{section_id}' is not declared"
                )
            membership &= section_ids
            if len(membership) > 1:
                errors.append(
                    f"{path}: storage_type '{st_id}' coordinate '{coordinate}' matches "
                    f"multiple sections {sorted(membership)}"
                )
            for section_id in membership:
                final_members[section_id].add(coordinate)
            if st.get("section_membership", {}).get("require_full_coverage") and not membership:
                errors.append(
                    f"{path}: storage_type '{st_id}' coordinate '{coordinate}' has no section"
                )

        for section_id, members in final_members.items():
            if not members:
                errors.append(
                    f"{path}: storage_type '{st_id}' section '{section_id}' has no members"
                )
    return errors


def _compiled_storage_point_ids(storage_data: dict | None, wcs_data: dict | None) -> set[str]:
    ids: set[str] = set()
    for st in (storage_data or {}).get("storage_types", []):
        st_id = st.get("id")
        try:
            if "storage_point_generator" in st:
                generator = st["storage_point_generator"]
                for aisle in range(1, generator.get("aisles", 1) + 1):
                    for stack in range(1, generator.get("stacks", 1) + 1):
                        for level in range(1, generator.get("levels", 1) + 1):
                            coordinate = generator["coordinate_pattern"].format(
                                aisle=aisle, stack=stack, level=level
                            )
                            ids.add(f"{st_id}.{coordinate}")
            elif "layout_variants" in st:
                grid = st.get("layout_grid", {})
                for variant in st["layout_variants"]:
                    for aisle in range(1, grid.get("aisles", 1) + 1):
                        for bay in range(1, grid.get("bays", 1) + 1):
                            for slot in range(1, variant["positions_per_bay"] + 1):
                                coordinate = variant["coordinate_pattern"].format(
                                    aisle=aisle, bay=bay, slot=slot
                                )
                                ids.add(f"{st_id}.{coordinate}")
            else:
                ids.update(
                    f"{st_id}.{point['coordinate']}" for point in st.get("storage_points", [])
                )
        except (KeyError, ValueError, IndexError):
            pass
    ids.update(
        item["id"] for item in (storage_data or {}).get("work_centers", [])
        if item.get("storage_point_ref") and item.get("id")
    )
    ids.update(
        item["id"] for item in (wcs_data or {}).get("reporting_points", [])
        if item.get("storage_point_ref") and item.get("id")
    )
    return ids


def build_extension_entity_index(
    storage_data: dict | None,
    wcs_data: dict | None,
    lanes_data: dict | None,
    movement_data: dict | None,
    replenishment_data: dict | None,
) -> dict[str, set[str]]:
    storage_types = (storage_data or {}).get("storage_types", [])
    return {
        "storage_point": _compiled_storage_point_ids(storage_data, wcs_data),
        "storage_type": {item["id"] for item in storage_types if item.get("id")},
        "section": {
            f"{st['id']}.{section['id']}" for st in storage_types if st.get("id")
            for section in st.get("sections", []) if section.get("id")
        },
        "activity_area": {
            item["id"] for item in (storage_data or {}).get("activity_areas", []) if item.get("id")
        },
        "work_center": {
            item["id"] for item in (storage_data or {}).get("work_centers", []) if item.get("id")
        },
        "door": {item["id"] for item in (storage_data or {}).get("doors", []) if item.get("id")},
        "reporting_point": {
            item["id"] for item in (wcs_data or {}).get("reporting_points", []) if item.get("id")
        },
        "controller": {
            item["id"] for item in (wcs_data or {}).get("controller_definitions", []) if item.get("id")
        },
        "equipment": {item["id"] for item in (wcs_data or {}).get("equipment", []) if item.get("id")},
        "telegram_action": {
            item["id"] for item in (wcs_data or {}).get("telegram_actions", []) if item.get("id")
        },
        "lane": {item["id"] for item in (lanes_data or {}).get("lanes", []) if item.get("id")},
        "conveyor_segment": {
            item["id"] for item in (lanes_data or {}).get("conveyor_segments", []) if item.get("id")
        },
        "conveyor_main": {"CONVEYOR_MAIN"} if (lanes_data or {}).get("conveyor_main") else set(),
        "movement_rule": {
            item["id"] for item in (movement_data or {}).get("movement_rules", []) if item.get("id")
        },
        "replenishment_strategy": {
            item["id"] for item in (replenishment_data or {}).get("replenishment_strategies", [])
            if item.get("id")
        },
    }


def validate_extensions(
    warehouse_file: Path,
    extension_files: list[Path],
    entity_index: dict[str, set[str]],
) -> list[str]:
    errors: list[str] = []
    warehouse_data = load_yaml(warehouse_file) or {}
    dataset_id = (warehouse_data.get("metadata") or {}).get("dataset_id")
    namespaces: set[str] = set()
    for path in extension_files:
        if not path.exists():
            errors.append(f"{warehouse_file}: extension import missing: {path}")
            continue
        errors += validate_file(path, "extension.schema.json")
        data = load_yaml(path) or {}
        extension = data.get("extension") or {}
        namespace = extension.get("namespace")
        if namespace in namespaces:
            errors.append(
                f"{warehouse_file}: duplicate extension namespace '{namespace}'"
            )
        if namespace:
            namespaces.add(namespace)
        if extension.get("dataset_id") != dataset_id:
            errors.append(
                f"{path}: extension.dataset_id '{extension.get('dataset_id')}' does not "
                f"match warehouse dataset_id '{dataset_id}'"
            )
        record_keys: set[tuple[str, str]] = set()
        for record in extension.get("records", []):
            entity_type = record.get("entity_type")
            entity_id = record.get("entity_id")
            key = (entity_type, entity_id)
            if key in record_keys:
                errors.append(
                    f"{path}: duplicate extension record {entity_type}/{entity_id}"
                )
            record_keys.add(key)
            if entity_type in entity_index and entity_id not in entity_index[entity_type]:
                errors.append(
                    f"{path}: extension record {entity_type}/{entity_id} references "
                    "an unknown entity"
                )
    return errors


def _activity_area_nodes(storage_data: dict | None) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for area in (storage_data or {}).get("activity_areas", []):
        result[area.get("id")] = {
            str(ref).split(".", 1)[0] for ref in area.get("bins_from", [])
        }
    return result


def _movement_endpoint_nodes(endpoint: dict | None, activity_nodes: dict[str, set[str]]) -> set[str]:
    if not isinstance(endpoint, dict):
        return set()
    nodes: set[str] = set()
    nodes.update(st for st in _as_id_list(endpoint.get("storage_type")) if st != "*")
    nodes.update(_as_id_list(endpoint.get("work_center")))
    nodes.update(_as_id_list(endpoint.get("reporting_point")))
    for area in _as_id_list(endpoint.get("activity_area")):
        nodes.update(activity_nodes.get(area, set()))
    return nodes


def _has_path(adjacency: dict[str, set[str]], source: str, target: str) -> bool:
    if source == target:
        return True
    pending = [source]
    seen = {source}
    while pending:
        node = pending.pop()
        for neighbor in adjacency.get(node, set()):
            if neighbor == target:
                return True
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    return False


def check_graph_reachability(
    movement_path: Path,
    movement_data: dict | None,
    storage_data: dict | None,
    wcs_data: dict | None,
    lanes_data: dict | None,
) -> list[str]:
    """Checks executable automated/segment-bound rules against the topology graph.

    Lanes and conveyor_main are bidirectional, conveyor_segments are directed.
    Storage types and reporting points owned by the same controller are mutually
    reachable inside that controller boundary (e.g. an opaque AutoStore grid).
    Manual rules remain implicit because forklift/walking paths are intentionally
    not exhaustively modeled.
    """
    errors: list[str] = []
    adjacency: dict[str, set[str]] = {}

    def add_edge(source: str, target: str, bidirectional: bool = False) -> None:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())
        if bidirectional:
            adjacency[target].add(source)

    for lane in (lanes_data or {}).get("lanes", []):
        points = lane.get("connects", [])
        for source in points:
            for target in points:
                if source != target:
                    add_edge(source, target, bidirectional=True)
    main = (lanes_data or {}).get("conveyor_main") or {}
    for source in main.get("connects", []):
        for target in main.get("connects", []):
            if source != target:
                add_edge(source, target, bidirectional=True)
    segments = {
        segment.get("id"): segment
        for segment in (lanes_data or {}).get("conveyor_segments", [])
        if segment.get("id")
    }
    for segment in segments.values():
        add_edge(segment["from"], segment["to"])

    controller_nodes: dict[str, set[str]] = {}
    for st in (storage_data or {}).get("storage_types", []):
        if st.get("controller"):
            controller_nodes.setdefault(st["controller"], set()).add(st["id"])
    for rp in (wcs_data or {}).get("reporting_points", []):
        if rp.get("controller"):
            controller_nodes.setdefault(rp["controller"], set()).add(rp["id"])
    for nodes in controller_nodes.values():
        for source in nodes:
            for target in nodes:
                if source != target:
                    add_edge(source, target)

    activity_nodes = _activity_area_nodes(storage_data)
    for rule in (movement_data or {}).get("movement_rules", []):
        if not rule.get("allowed"):
            continue
        via_segment = rule.get("via_segment")
        requires_path = rule.get("execution") == "automated" or bool(via_segment)
        if not requires_path:
            continue
        sources = _movement_endpoint_nodes(rule.get("from"), activity_nodes)
        targets = _movement_endpoint_nodes(rule.get("to"), activity_nodes)
        if not sources or not targets:
            errors.append(
                f"{movement_path}: movement_rule '{rule.get('id', '?')}': automated or "
                "segment-bound rule requires resolvable from and to endpoints"
            )
            continue
        if via_segment in segments:
            segment = segments[via_segment]
            if segment["from"] not in sources or segment["to"] not in targets:
                errors.append(
                    f"{movement_path}: movement_rule '{rule.get('id', '?')}': via_segment "
                    f"'{via_segment}' connects {segment['from']} -> {segment['to']}, which "
                    "does not match the rule endpoints"
                )
        for source in sorted(sources):
            for target in sorted(targets):
                if not _has_path(adjacency, source, target):
                    errors.append(
                        f"{movement_path}: movement_rule '{rule.get('id', '?')}': no directed "
                        f"topology path from '{source}' to '{target}'"
                    )
    return errors


def validate_warehouse_file(warehouse_file: Path, element_ids: dict[str, set[str]] = {}) -> list[str]:
    """Validates a single building-level warehouse.yaml and everything it imports."""
    if not warehouse_file.exists():
        return [f"warehouse file missing: {warehouse_file}"]

    all_errors = validate_file(warehouse_file, "warehouse.schema.json")

    imported_paths = collect_imports(warehouse_file)
    if len(imported_paths) != len(set(imported_paths)):
        all_errors.append(f"{warehouse_file}: duplicate warehouse import path")
    import_names = [path.name for path in imported_paths]
    duplicate_names = sorted({name for name in import_names if import_names.count(name) > 1})
    for name in duplicate_names:
        all_errors.append(
            f"{warehouse_file}: multiple imports named '{name}' are ambiguous"
        )
    imports: dict[str, Path] = {}
    for imported in imported_paths:
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
    lanes_data = load_yaml(imports["lanes.yaml"]) if "lanes.yaml" in imports else {}
    movement_data = load_yaml(imports["movement_rules.yaml"]) if "movement_rules.yaml" in imports else {}
    replenishment_data = load_yaml(imports["replenishment.yaml"]) if "replenishment.yaml" in imports else {}

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
        all_errors += check_topology_id_uniqueness(
            imports["storage.yaml"], storage_data, wcs_data
        )
        all_errors += check_storage_coordinate_integrity(
            imports["storage.yaml"], storage_data, wcs_data
        )
        all_errors += check_section_membership(imports["storage.yaml"], storage_data)

    if "wcs.yaml" in imports:
        all_errors += check_wcs_refs(imports["wcs.yaml"], element_ids)

    if "movement_rules.yaml" in imports:
        all_errors += check_execution_consistency(
            imports["movement_rules.yaml"], storage_data, wcs_data
        )
        all_errors += check_movement_rule_refs(
            imports["movement_rules.yaml"], storage_data, wcs_data, lanes_data, element_ids
        )
        all_errors += check_graph_reachability(
            imports["movement_rules.yaml"], movement_data,
            storage_data, wcs_data, lanes_data,
        )

    if "replenishment.yaml" in imports:
        all_errors += check_replenishment_refs(imports["replenishment.yaml"], storage_data)

    if "lanes.yaml" in imports:
        all_errors += check_lane_refs(
            imports["lanes.yaml"], storage_data, wcs_data, element_ids
        )

    extension_files = collect_extension_imports(warehouse_file)
    if len(extension_files) != len(set(extension_files)):
        all_errors.append(f"{warehouse_file}: duplicate extension import path")
    entity_index = build_extension_entity_index(
        storage_data, wcs_data, lanes_data, movement_data, replenishment_data
    )
    all_errors += validate_extensions(warehouse_file, extension_files, entity_index)

    return all_errors


def validate_facility_file(facility_file: Path, element_ids: dict[str, set[str]] = {}) -> list[str]:
    """Validates a facility.yaml and cascades into every building it lists."""
    if not facility_file.exists():
        return [f"facility file missing: {facility_file}"]

    all_errors = validate_file(facility_file, "facility.schema.json")

    facility_data = load_yaml(facility_file) or {}
    facility = facility_data.get("facility", {})
    expected_facility = facility.get("reference_number") or facility.get("id")
    building_files = collect_relative_refs(facility_file, "facility", "buildings")
    building_ids: set[str] = set()
    dataset_ids: set[str] = set()
    for building_file in building_files:
        all_errors += validate_warehouse_file(building_file, element_ids)
        if not building_file.exists():
            continue
        warehouse_data = load_yaml(building_file) or {}
        building_id = (warehouse_data.get("target") or {}).get("building")
        dataset_id = (warehouse_data.get("metadata") or {}).get("dataset_id")
        target_facility = (warehouse_data.get("target") or {}).get("facility")
        if building_id in building_ids:
            all_errors.append(
                f"{facility_file}: duplicate target.building '{building_id}' in facility"
            )
        if building_id:
            building_ids.add(building_id)
        if dataset_id in dataset_ids:
            all_errors.append(
                f"{facility_file}: duplicate warehouse metadata.dataset_id '{dataset_id}'"
            )
        if dataset_id:
            dataset_ids.add(dataset_id)
        if expected_facility and target_facility != expected_facility:
            all_errors.append(
                f"{building_file}: target.facility '{target_facility}' does not match "
                f"parent facility '{expected_facility}'"
            )

    return all_errors


def validate_company_file(company_file: Path, element_ids: dict[str, set[str]] = {}) -> list[str]:
    """Validates a company.yaml and cascades into every facility it lists."""
    all_errors = validate_file(company_file, "company.schema.json")

    company_data = load_yaml(company_file) or {}
    company_id = (company_data.get("company") or {}).get("id")
    facility_ids: set[str] = set()
    for facility_file in collect_relative_refs(company_file, "company", "facilities"):
        all_errors += validate_facility_file(facility_file, element_ids)
        if not facility_file.exists():
            continue
        facility_data = load_yaml(facility_file) or {}
        facility_id = (facility_data.get("facility") or {}).get("id")
        if facility_id in facility_ids:
            all_errors.append(
                f"{company_file}: duplicate facility id '{facility_id}'"
            )
        if facility_id:
            facility_ids.add(facility_id)
        for building_file in collect_relative_refs(facility_file, "facility", "buildings"):
            if not building_file.exists():
                continue
            warehouse_data = load_yaml(building_file) or {}
            target_tenant = (warehouse_data.get("target") or {}).get("tenant")
            if company_id and target_tenant != company_id:
                all_errors.append(
                    f"{building_file}: target.tenant '{target_tenant}' does not match "
                    f"parent company '{company_id}'"
                )

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

    template_file = ELEMENTS_DIR / "rack_templates.yaml"
    if template_file.exists():
        all_errors += validate_template_catalogs(template_file)

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
