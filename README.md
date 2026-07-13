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

Runtime state (current inventory, occupancy, resource availability) does
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
│   ├── resource_types.yaml       # Resource classes and capabilities
│   ├── process_types.yaml        # Inbound/outbound/internal movement categories
│   └── blocking_reasons.yaml     # Reasons a storage_point can be blocked
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
│   └── validate.py       # Validation script (schema + consistency checks)
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

- [ ] Complete JSON Schemas in `schemas/` (currently a basic skeleton)
- [ ] Extend `tools/validate.py` with consistency checks (referential
      integrity between files: does every `movement_rule` reference an
      existing `storage_type`?)
- [ ] Implement storage point generator logic (template → concrete points)
- [ ] Compiler step: YAML → runtime entities (Linq2db model)
- [ ] Optional: import mapper for AutomationML (CAEX) as an alternative source
- [ ] Set up a TeamCity pipeline instead of/in addition to GitHub Actions
