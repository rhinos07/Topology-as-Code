# Warehouse-as-Code

Warehouse-as-Code: declarative, version-controlled description of warehouse
structures, material-flow communication, and process strategies
(replenishment, movement rules) as YAML, validated via CI, compiled into
runtime entities.

## Related Projects

Part of a family of sibling "-as-Code" repos sharing the same declarative
pattern (JSON Schema validation, `structure/` vs. `strategies/`,
`elements/` catalogs):

| Repo | Covers |
|---|---|
| **Warehouse-as-Code** (this repo) | Physical warehouse structure, material-flow communication, movement/replenishment rules |
| [`OrderOrchestration-as-Code`](https://github.com/rhinos07/OrderOrchestration-as-Code) | How incoming orders are split, and which downstream workflow each split triggers |
| [`MasterData-as-Code`](https://github.com/rhinos07/MasterData-as-Code) | Item/article master data, packaging/UOM hierarchy, sourcing & lifecycle rules |

Two of this repo's own `elements/` catalogs are shared-vocabulary
candidates for the siblings above, not yet acted on:
- `elements/process_types.yaml` (`putaway_task`, `pick_task`, …) - meant
  to be the same vocabulary `OrderOrchestration-as-Code`'s
  `workflow_trigger` values reference when they hand off to the
  warehouse.
- `elements/load_unit_types.yaml` (`pallet_euro`, `carton`, …) -
  conceptually packaging *master data*; a candidate to eventually move
  to `MasterData-as-Code` (see that repo's "Shared Vocabulary" section).

Both catalogs currently stay put and duplicated where needed - don't
extract/move them until real drift or duplication pain shows up.

## Core Principle

| Layer | What | Change Frequency | Who Changes It |
|---|---|---|---|
| `elements/` | Reusable templates (rack types, etc.) | very rarely | Technician/Architect |
| `customers/<customer>/company.yaml` | Tenant/organization identity | very rarely (onboarding/offboarding) | Admin |
| `customers/<customer>/facilities/<facility>/facility.yaml` | Site/plant identity (one per physical site) | rarely | Admin/Technician |
| `.../buildings/<building>/structure/` | Physical warehouse structure of one building | rarely (during rebuilds) | Technician, strict review |
| `.../buildings/<building>/strategies/` | Replenishment, movement, and slotting rules | frequently | Logistics planner, lenient review |

A company can have multiple facilities (sites/plants), and each facility
can have multiple buildings (halls) - Company → Facility → Building. Each
building has its own `warehouse.yaml` plus `structure/`/`strategies/`, as
described below.

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
│   └── <customer>/                       # = Company
│       ├── company.yaml                  # Top level, lists facilities
│       └── facilities/
│           └── <facility>/               # = Facility (site/plant)
│               ├── facility.yaml         # Lists buildings
│               └── buildings/
│                   └── <building>/       # = Building (hall)
│                       ├── warehouse.yaml        # Imports structure/strategies below
│                       ├── structure/            # Physical structure
│                       │   ├── storage.yaml      # Storage types + storage point generators
│                       │   ├── lanes.yaml        # Conveyor technology / lanes / conveyor segments
│                       │   └── wcs.yaml          # Warehouse Control System: reporting points, controllers, telegram actions
│                       └── strategies/           # Process rules
│                           ├── replenishment.yaml
│                           └── movement_rules.yaml
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

# Validates a company.yaml, facility.yaml, or warehouse.yaml - cascades
# down to every facility/building it references
python tools/validate.py customers/example_customer/company.yaml

# Expand storage_point_generator/layout_variants into concrete storage_points
# for one specific building
python tools/compile.py customers/example_customer/facilities/facility_pa11/buildings/hall_3/warehouse.yaml --output build/warehouse-artifact.yaml

# Compare the last applied snapshot with the newly compiled desired snapshot
python tools/plan.py build/applied.yaml build/warehouse-artifact.yaml --output build/plan.yaml
```

## Import Lifecycle

Every building-level `warehouse.yaml` owns a stable scope through
`metadata.dataset_id` and declares its WMS target and reconciliation policy.
It is a complete desired snapshot for that scope; unmanaged WMS objects are
always preserved.

`compile.py` writes a deterministic artifact containing every building-owned
desired-state entity: warehouse, storage types and points, sections, activity
areas, work centers, doors, controllers, reporting points, equipment, telegram
actions, lanes and conveyor topology, movement rules and replenishment
strategies. Its `artifact.content_hash` depends on the target, dataset ID,
sorted entities and extensions, but not on the human revision. Re-importing
the same hash is therefore idempotent.

`plan.py` is read-only and diffs all entity types generically. It emits creates,
field-level updates, deactivations and conflicts. Missing desired objects are
never physically deleted:
`removal_policy: deactivate` plans a deactivation, while `reject` reports a
conflict. `expected_current_hash` enables an eventual WMS adapter to reject a
stale plan.

Renames are intentionally not inferred. Changing an ID appears as a
destructive deactivation plus a create and requires review. Inventory, open
task and reference checks must be performed by the target-specific WMS adapter
before applying a deactivation.

## Strict Schema Validation

Domain objects are closed with `additionalProperties: false`; customer-specific
fields must not be added ad hoc. Storage types must choose exactly one of
`storage_point_generator`, `layout_variants`, or explicit `storage_points`.
Conditional rules also enforce access-model, automation-policy, blocking and
movement-rule invariants. `tools/validate.py` supplements JSON Schema with
ID and coordinate uniqueness checks across the imported files.

Section membership is also explicit. Generated areas assign ranges with a
`section.selector`, while an explicit point or generator exception may set a
local `section` directly. Direct assignment overrides selectors. Membership is
exclusive, selector overlap and empty sections are rejected, and
`section_membership.require_full_coverage` can require every point to belong to
exactly one section. Compiled points carry canonical IDs such as
`HBR.SEC_FAST`.

Physical quantities use structured values from `schemas/quantity.schema.json`
instead of unit-bearing strings:

```yaml
max_weight: {value: 500, unit: "kg"}
width: {value: 1200, unit: "mm"}
max_speed: {value: 10.8, unit: "km/h"}
check_interval: {value: 5, unit: "min"}
```

Allowed input units are dimension-specific. The compiler normalizes mass,
length, volume, speed and duration to kg, m, m3, m/s and s before hashing and
planning, so equivalent quantities do not create false updates. Temperature
zones are either `ambient` or an explicit Celsius range such as
`{min: 2, max: 8, unit: "C"}`.

## WMS-specific Extension Sidecars

Vendor-specific roundtrip data lives in optional namespaced sidecars instead
of weakening the closed domain schemas. A building declares them separately:

```yaml
warehouse:
  extension_imports:
    - "extensions/example-wms.yaml"
```

The sidecar envelope is validated by `schemas/extension.schema.json`. Its
namespace, version and dataset must be valid, every record must reference an
existing entity, and duplicate namespace/entity records are rejected. Only
`records[].payload` is intentionally open and remains opaque to generic tools.

```yaml
api_version: "warehouse-as-code/extension-v1"
extension:
  namespace: "com.example.wms"
  version: "1.0"
  dataset_id: "example_customer/pa11/hall_3"
  records:
    - entity_type: "storage_point"
      entity_id: "HBR.01-01-06"
      payload:
        internal_location_key: 47110815
```

The compiler preserves sidecars in deterministic namespace/record order and
includes them in the artifact hash. The planner reports extension creates,
updates and removals separately; generic tooling never interprets or silently
drops an unknown payload.

## Examples

- `customers/example_customer/` - one building mixing several technologies
  side by side (high-bay rack, block storage, channel storage, a small
  AutoStore grid, cold zone, hazmat) to show most entity types at once.
- `customers/autostore_customer/` - a second, dedicated example: a single
  building that is *entirely* one AutoStore grid - just the grid, its
  ports, the controller and the robot fleet, with no conveyor/lane
  infrastructure modeled (grid-to-port connectivity is intrinsic to the
  AS itself). Shows the minimal pattern for a single-technology
  automation cell. Walked through step by step in
  [`docs/autostore-walkthrough.md`](docs/autostore-walkthrough.md).

## Core Concepts (Quick Reference)

- **storage_point** — smallest physical/logical storage unit (formerly "bin").
  Can be a rack location or a block location, differs in access model
  (`direct` vs. `lifo`). Details: `docs/entity-glossary.md`.
- **storage_type** — storage area that groups storage_points (e.g. high-bay rack).
- **activity_area** — functional cross-cutting grouping, orthogonal to the
  physical hierarchy (a storage_point can belong to multiple activity_areas).
- **reporting_point** — communication point between WMS and a downstream
  controller (PLC, MFC, AS controller, etc.); technically always also
  modeled as a storage_point.
- **movement_rule** — defines allowed/forbidden goods movements between
  areas. Two policies: `default_allow` (manual areas, exceptions
  explicit) vs. `explicit_only` (automated/conveyor areas, every
  route must exist explicitly).
- **replenishment_strategy** — replenishment rules (min/max, order-driven,
  zero-stock, predictive), references structure but is not itself structure.

Full glossary: [`docs/entity-glossary.md`](docs/entity-glossary.md)

## Next Steps for This Repo

- [ ] Optional: import mapper for AutomationML (CAEX) as an alternative source
- [ ] Map the deterministic compile/plan output to actual WMS/runtime entities (e.g. a
      Linq2db model) - currently it only produces an intermediate YAML,
      not a WMS-specific import format

### Validation Coverage and Remaining Gaps

`tools/validate.py` checks all modeled cross-file references, duplicate IDs
and generated coordinates, parent Company/Facility/Building identity, movement
execution/controller consistency, and directed graph reachability for automated
or explicitly segment-bound routes. Lanes and `conveyor_main` are treated as
bidirectional; conveyor segments are directed. Nodes below the same controller
form an implicit controller-internal graph, which supports opaque automation
cells such as AutoStore without inventing internal conveyor segments.

Manual routes are not required to have graph edges because forklift and walking
paths are deliberately not exhaustively modeled. Remaining validation gaps are:

1. **`explicit_only` route completeness:** the validator proves that declared
   automated routes are reachable, but does not infer which additional business
   routes ought to exist for every physical segment.
2. **Runtime layout exclusivity:** alternative layouts compile with a shared
   `physical_bay`; enforcing one active variant per bay remains a WMS concern.
3. **Physical compatibility:** dimensions and weights are normalized, but
   load-unit envelopes are not yet compared with storage-point limits.

### Structural Gaps (Not Yet Modeled)

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
