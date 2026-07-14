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


def _endpoint_controller(
    endpoint: dict | None,
    storage_controllers: dict,
    reporting_point_controllers: dict,
) -> str | None:
    """Resolves the controller an endpoint sits under, or None. Only
    storage_type and reporting_point endpoints carry a controller;
    work_centers/doors/activity_areas don't (they are manual/logical)."""
    if not isinstance(endpoint, dict):
        return None
    if endpoint.get("reporting_point"):
        return reporting_point_controllers.get(endpoint["reporting_point"])
    if endpoint.get("storage_type"):
        return storage_controllers.get(endpoint["storage_type"])
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


def parse_measure(s: str) -> float | None:
    """Parses a measurement string like '0.8m' or '1000kg' into a float.
    Returns None if the string is absent or cannot be parsed."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    for suffix in ("m", "kg"):
        if s.endswith(suffix):
            try:
                return float(s[: -len(suffix)])
            except ValueError:
                return None
    return None


def _dims_fit(lut_dims: dict, st_size: dict) -> bool:
    """Returns True if the load unit fits inside the storage point's envelope.

    A single 90° horizontal rotation is allowed (swapping width and depth) since
    a load unit can be placed either way in a bay.  Height is always compared
    directly – load units cannot be tilted on their side.

    When all four footprint dimensions are available, both orientations are
    considered.  When only one of the load unit's footprint dimensions is known,
    it must fit within the largest available storage footprint dimension (the most
    conservative safe check given the missing data).
    """
    lut_w = parse_measure(lut_dims.get("width", ""))
    lut_d = parse_measure(lut_dims.get("depth", ""))
    lut_h = parse_measure(lut_dims.get("height", ""))
    st_w = parse_measure(st_size.get("width", ""))
    st_d = parse_measure(st_size.get("depth", ""))
    st_h = parse_measure(st_size.get("height", ""))

    if lut_h is not None and st_h is not None and lut_h > st_h:
        return False

    if lut_w is not None and lut_d is not None and st_w is not None and st_d is not None:
        # Full rotation check when all four footprint dimensions are present
        fits_straight = lut_w <= st_w and lut_d <= st_d
        fits_rotated = lut_d <= st_w and lut_w <= st_d
        if not (fits_straight or fits_rotated):
            return False
    elif st_w is not None and st_d is not None:
        # Partial load unit data: a known dimension must fit in the larger storage axis
        st_max = max(st_w, st_d)
        if lut_w is not None and lut_w > st_max:
            return False
        if lut_d is not None and lut_d > st_max:
            return False

    return True


def check_load_unit_physical_compatibility(path: Path) -> list[str]:
    """Checks that a storage_type's size and max_weight are physically compatible
    with each of its allowed_load_unit_types:

    - Dimensional fit: load unit dimensions (w/d/h) must fit within the storage
      point's declared size.  A 90° horizontal rotation is allowed; tilting is not.
    - Weight capacity: a load unit's max_weight must not exceed the storage point's
      max_weight (a fully-loaded unit must fit within the structural limit).

    Checks are applied to:
    - default_attributes (covers all generated storage_points unless overridden)
    - exceptions and storage_points that declare their own allowed_load_unit_types,
      using that entry's own size/max_weight if set, otherwise inheriting from
      default_attributes.
    """
    errors: list[str] = []
    data = load_yaml(path)
    if not data:
        return errors

    lut_catalog_path = ELEMENTS_DIR / "load_unit_types.yaml"
    if not lut_catalog_path.exists():
        return errors
    lut_catalog = load_yaml(lut_catalog_path)
    if not lut_catalog:
        return errors
    lut_by_id: dict[str, dict] = {
        lut["id"]: lut
        for lut in lut_catalog.get("load_unit_types", [])
        if lut.get("id")
    }

    for st in data.get("storage_types", []):
        st_id = st.get("id", "?")
        da = st.get("default_attributes") or {}
        da_size = da.get("size")
        da_max_weight = da.get("max_weight")

        def _check(
            lut_ids: list[str],
            size: dict | None,
            max_weight_str: str | None,
            context: str,
        ) -> list[str]:
            errs: list[str] = []
            st_mw = parse_measure(max_weight_str) if max_weight_str else None
            for lut_id in lut_ids:
                lut = lut_by_id.get(lut_id)
                if not lut:
                    continue  # referential integrity is checked separately
                lut_dims = lut.get("dimensions")
                if size and lut_dims and not _dims_fit(lut_dims, size):
                    errs.append(
                        f"{path}: storage_type '{st_id}': {context}: "
                        f"load_unit_type '{lut_id}' dimensions {lut_dims} "
                        f"do not fit storage size {size}"
                    )
                if st_mw is not None:
                    lut_mw = parse_measure(lut.get("max_weight", ""))
                    if lut_mw is not None and lut_mw > st_mw:
                        errs.append(
                            f"{path}: storage_type '{st_id}': {context}: "
                            f"load_unit_type '{lut_id}' max_weight ({lut.get('max_weight')}) "
                            f"exceeds storage max_weight ({max_weight_str})"
                        )
            return errs

        da_luts = da.get("allowed_load_unit_types", [])
        if da_luts:
            errors += _check(da_luts, da_size, da_max_weight, "default_attributes")

        for exc in st.get("exceptions", []):
            exc_luts = exc.get("allowed_load_unit_types")
            if exc_luts is not None:
                coord = exc.get("coordinate", "?")
                errors += _check(
                    exc_luts,
                    exc.get("size") or da_size,
                    exc.get("max_weight") or da_max_weight,
                    f"exception at '{coord}'",
                )

        for sp in st.get("storage_points", []):
            sp_luts = sp.get("allowed_load_unit_types")
            if sp_luts is not None:
                coord = sp.get("coordinate", "?")
                errors += _check(
                    sp_luts,
                    sp.get("size") or da_size,
                    sp.get("max_weight") or da_max_weight,
                    f"storage_point '{coord}'",
                )

    return errors


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
    """Validates all cross-reference fields of a single movement-rule endpoint."""
    errors: list[str] = []
    if not isinstance(endpoint, dict):
        return errors

    st = endpoint.get("storage_type")
    if st and st != "*":
        if storage_type_ids and st not in storage_type_ids:
            errors.append(f"{path}: {context}: storage_type '{st}' not found in storage.yaml")
        else:
            sec = endpoint.get("section")
            if sec and st in storage_type_ids and sec not in sections.get(st, set()):
                errors.append(
                    f"{path}: {context}: section '{sec}' not found in storage_type '{st}'"
                )

    aa = endpoint.get("activity_area")
    if aa and activity_area_ids and aa not in activity_area_ids:
        errors.append(f"{path}: {context}: activity_area '{aa}' not found in storage.yaml")

    wc = endpoint.get("work_center")
    if wc and work_center_ids and wc not in work_center_ids:
        errors.append(f"{path}: {context}: work_center '{wc}' not found in storage.yaml")

    rp = endpoint.get("reporting_point")
    if rp and reporting_point_ids and rp not in reporting_point_ids:
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

    all_errors = validate_file(warehouse_file, "warehouse.schema.json", "warehouse")

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
            all_errors += validate_file(imported, "lanes.schema.json", None)
        elif name == "wcs.yaml":
            all_errors += validate_file(imported, "wcs.schema.json", None)
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
        all_errors += check_load_unit_physical_compatibility(imports["storage.yaml"])

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

    all_errors = validate_file(facility_file, "facility.schema.json", "facility")

    for building_file in collect_relative_refs(facility_file, "facility", "buildings"):
        all_errors += validate_warehouse_file(building_file, element_ids)

    return all_errors


def validate_company_file(company_file: Path, element_ids: dict[str, set[str]] = {}) -> list[str]:
    """Validates a company.yaml and cascades into every facility it lists."""
    all_errors = validate_file(company_file, "company.schema.json", "company")

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
