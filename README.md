# Warehouse-as-Code

Warehouse-as-Code: declarative, version-controlled description of warehouse
structures, material-flow communication, and process strategies
(replenishment, movement rules) as YAML, validated via CI, compiled into
runtime entities.

## Core Principle

| Layer | What | Change Frequency | Who Changes It |
|---|---|---|---|
| `elements/` | Reusable templates (rack types, etc.) | very rarely | Technician/Architect |
| `customers/<customer>/structure/` | Physical warehouse structure of a customer | rarely (during rebuilds) | Technician, strict review |
| `customers/<customer>/strategies/` | Replenishment, movement, and slotting rules | frequently | Logistics planner, lenient review |

Runtime state (current inventory, occupancy, equipment availability) does
**not** live here, but in the WMS runtime database. These repos only
describe the **desired state** of the structure and rules — analogous to
Terraform: the code describes the infrastructure, not its current live
status.

## Repo Structure

```
warehouse-definitions/
├── schemas/              # JSON Schema for validating all YAML files
├── elements/             # Reusable templates and catalogs
│   ├── rack_templates.yaml       # Rack/lane/workstation templates
│   ├── load_unit_types.yaml      # Pallet/container/carton definitions
│   ├── equipment_types.yaml      # Equipment classes and capabilities
│   ├── process_types.yaml        # Inbound/outbound/internal movement/cross-dock categories
│   ├── blocking_reasons.yaml     # Reasons a storage_point can be blocked
│   └── hazmat_classes.yaml       # Hazardous material / compliance classifications
├── customers/
│   └── <customer>/
│       ├── warehouse.yaml        # Top level, imports the other files
│       ├── structure/            # Physical structure
│       │   ├── storage.yaml      # Storage types + storage point generators
│       │   ├── lanes.yaml        # Conveyor technology / lanes / conveyor segments
│       │   └── wcs.yaml          # Warehouse Control System: reporting points, PLC, telegram actions
│       └── strategies/           # Process rules
│           ├── replenishment.yaml
│           └── movement_rules.yaml
├── tools/
│   ├── validate.py       # Validation script (schema + consistency checks)
│   └── compile.py        # Expands storage_point_generator/layout_variants into
│                         #   concrete storage_point instances (build/ output)
├── docs/
│   └── entity-glossary.md
└── .github/workflows/validate.yaml   # CI pipeline (example, may need porting to TeamCity)
```

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python tools/validate.py customers/example_customer/warehouse.yaml

# Expand storage_point_generator/layout_variants into concrete storage_points
python tools/compile.py customers/example_customer/warehouse.yaml --output build/storage_points.yaml
```

## Core Concepts (Quick Reference)

- **storage_point** — smallest physical/logical storage unit (formerly "bin").
  Can be a rack location or a block location, differs in access model
  (`direct` vs. `lifo`). Details: `docs/entity-glossary.md`.
- **storage_type** — storage area that groups storage_points (e.g. high-bay rack).
- **activity_area** — functional cross-cutting grouping, orthogonal to the
  physical hierarchy (a storage_point can belong to multiple activity_areas).
- **reporting_point** — communication point between WMS and PLC, is
  technically always also modeled as a storage_point.
- **movement_rule** — defines allowed/forbidden goods movements between
  areas. Two policies: `default_allow` (manual areas, exceptions
  explicit) vs. `explicit_only` (automated/conveyor areas, every
  route must exist explicitly).
- **replenishment_strategy** — replenishment rules (min/max, order-driven,
  zero-stock, predictive), references structure but is not itself structure.

Full glossary: [`docs/entity-glossary.md`](docs/entity-glossary.md)

## Next Steps for This Repo

- [x] Implement storage point generator logic (template → concrete points):
      `tools/compile.py` expands `storage_point_generator` and
      `layout_variants` into concrete `storage_point` instances. It runs
      independently of `validate.py` and does not re-check schema
      conformance - run `validate.py` first. It does warn (not error) when
      an `exceptions[].coordinate` doesn't match any generated point
      (likely a typo), and errors on duplicate generated ids.
- [ ] Optional: import mapper for AutomationML (CAEX) as an alternative source
- [ ] Map `compile.py` output to actual WMS/runtime entities (e.g. a
      Linq2db model) - currently it only produces an intermediate YAML,
      not a WMS-specific import format

### Open Validation Gaps

`tools/validate.py` currently validates each file only against its own
JSON Schema. It does **not** check consistency across files or within a
file's cross-references. Known gaps:

1. **No cross-file referential integrity.** A typo in a referenced ID is
   not caught. Affected references:
   - `movement_rule.from/to.storage_type` (`movement_rules.yaml`) → `storage_type.id` (`storage.yaml`)
   - `movement_rule.allowed_load_unit_types` (`movement_rules.yaml`) → `load_unit_types.id` (`elements/`)
   - `movement_rule.trigger` (`movement_rules.yaml`) → `process_types.id` (`elements/`)
   - `storage_type.default_attributes.allowed_load_unit_types` (`storage.yaml`) → `load_unit_types.id` (`elements/`)
   - `storage_type.exceptions[].blocked_reason` (`storage.yaml`) → `blocking_reasons.id` (`elements/`)
   - `door.staging_section` → `storage_type.sections[].id` (both in `storage.yaml`)
   - `reporting_point.plc` → `plc_definitions.id` (both in `wcs.yaml`)
   - `equipment.type` (`wcs.yaml`) → `equipment_types.id` (`elements/`)
   - `activity_area.bins_from` → `storage_type`/`section` ids (`storage.yaml`)
   - `replenishment_strategy.source/destination` (`replenishment.yaml`) → `storage_type`/`activity_area` (`storage.yaml`)
   - `lane.connects` / `conveyor_segment.from/to` (`lanes.yaml`) → `storage_type`/`door`/`reporting_point`/`work_center` ids (multiple files)
2. **`explicit_only` completeness is not checked.** For a `storage_type`
   with `movement_policy: "explicit_only"`, every `lane`/`conveyor_segment`
   should have a matching `movement_rule` — not verified.
3. **`layout_variants` exclusivity is undeclared to tooling.** That two
   variants (e.g. "2 industrial pallets" vs. "3 euro pallets" per bay)
   physically overlap is documented but not machine-checked.
4. ~~`storage_point_generator`/`layout_variants` are not compiled.~~
   **Resolved** by `tools/compile.py` — see "Next Steps" above. Note this
   only expands templates into concrete points; it does not validate them
   against the JSON schemas (run `validate.py` first).
5. **No physical compatibility check** between a `storage_type`'s
   `size`/`max_weight` and its referenced `allowed_load_unit_types` (e.g.
   whether a euro pallet actually fits a 0.4m-wide bay).
6. **Minor:** `jsonschema.RefResolver` is deprecated (warning on every
   run, still functional). `validate_file()`'s `root_key` parameter is
   currently unused dead code.

### Structural Gaps (Not Yet Modeled)

- **No multi-facility hierarchy.** Currently one `warehouse` per customer
  folder, no Company → Facility → Building structure (as in Manhattan WMS).
- **No UOM hierarchy concept** (Each → Case → Pallet conversion ratios)
  beyond what `replenishment_strategy.unit_conversion` already covers.
- **No slotting optimization module** — deliberately excluded, this is
  runtime/analytics territory, not structure.
- **No yard management** (yard, trailers, check-in points) — deliberately
  excluded as runtime, same reasoning as Warehouse Tasks/Waves.

### Out of Scope (By Design)

Per architectural principle 1 (structure vs. runtime state, see
`docs/entity-glossary.md`), the following intentionally do **not** belong
in this repo:

- Runtime state: current inventory, occupancy, waves, warehouse tasks/orders
- Product master data: items/SKUs, batches
- Labor management: engineered labor standards, workforce scheduling

These live in the WMS runtime database or ERP/product master, not here.
