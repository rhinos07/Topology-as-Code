# Entity Glossary

Terms modeled after SAP EWM/MFS terminology (for compatibility with
industry vocabulary), but named independently where it made more sense
(`storage_point` instead of "Bin").

## Organizational Hierarchy

| Term | Meaning |
|---|---|
| `company` | Top-level tenant/organization (`company.yaml`). Lists one or more `facility` files. Maps to a `customers/<customer>/` folder. |
| `facility` | A physical site/plant/distribution center belonging to a `company` (`facility.yaml`). Lists one or more `warehouse`/building files. Carries `reference_number` (plant code, e.g. `PA11`) and `address`. |
| `warehouse` (Building) | A single building/hall within a `facility`. Imports its own `structure/` and `strategies/`. |

A `company` can have multiple `facility`s (sites), and each `facility` can
have multiple `warehouse`/buildings (halls) - **Company → Facility →
Building**. `tools/validate.py` accepts a path at any of the three
levels and cascades validation downward automatically.

## Structure

| Term | Meaning |
|---|---|
| `warehouse` | Building-level entity within a `facility` - see Organizational Hierarchy above |
| `storage_type` | Storage area, groups `storage_point`s (e.g. high-bay rack, block storage). A building can have arbitrarily many `storage_type`s side by side (e.g. HRL + AutoStore + manual block storage in one `warehouse`). Optional `controller` field links it to a `controller_definitions` entry in the building's `wcs.yaml` when it's operated by a specific automation system. |
| `section` | Subdivision of a `storage_type` by properties (e.g. access frequency) |
| `storage_point` | Smallest physical/logical storage unit (formerly "storage bin"). Rack location or block location. Can carry `size` ({width, depth, height}), `position` (logical string or metric {x, y, z}), `capacity_volume` (cube capacity for volume-based checks), and `hazmat_classes` via `default_attributes`/`exceptions` in `storage_type`. |
| `storage_point_generator` | Generates `storage_point`s from a grid instead of enumerating them individually |
| `storage_points` (explicit) | Explicitly enumerated `storage_point`s, as an alternative to `storage_point_generator`/`layout_variants`. For the handful of cases where there is nothing to generate from a grid - e.g. a single opaque block place standing in for a controller-managed area (an AutoStore grid, an AS/RS booked as a whole), where the internal bin/column detail lives in the controller, not in this repo. Each entry needs a `coordinate` and inherits `default_attributes` unless it overrides them. |
| `layout_variants` | Alternative to `storage_point_generator` for bays that support more than one physical layout (e.g. "2/3 Platz-Lagerung": a bay fits either 2 industrial pallets or 3 euro pallets, never both). Each variant has its own `coordinate_pattern` and `load_unit_type`; the coordinate sets overlap physically but not as strings. Mutual exclusivity at booking time is a runtime concern, not enforced by this schema. Requires a sibling `layout_grid` ({aisles, bays}) defining how many physical bays exist. |
| `activity_area` | Functional cross-cutting grouping, orthogonal to the physical hierarchy. A `storage_point` can belong to multiple `activity_area`s simultaneously. |
| `work_center` | Physical unit for activities such as packing, weighing. Set `storage_point_ref: true` if WIP inventory can be booked directly at the work center (same pattern as `reporting_point`). |
| `door` / `staging_area` | Doors for goods receipt/dispatch/returns. `direction`: `inbound`, `outbound`, or `returns` (kept separate from `inbound` so returns can be routed/inspected differently). |
| `lane` / `conveyor_segment` | Physical connection/conveyor technology between areas (**"can"**) |
| `reporting_point` | Communication point between WMS and a downstream `controller`; is technically always also modeled as a `storage_point` |
| `equipment` | Executing element (shuttle, forklift, AGV). `mode: wms_controlled` = WMS decides the route explicitly (SAP EWM: "Resource"), `mode: controller_autonomous` = the downstream controller is autonomous with its own order buffer (SAP EWM: "Vehicle"). Named `equipment` rather than SAP's "Resource" to avoid collision with the Terraform `resource` keyword this repo's analogy relies on; matches Manhattan WMS terminology instead. Optional `controller` binds the equipment to a `controller_definition`: for `controller_autonomous` equipment that is *how* a movement is routed to it - a movement whose endpoints share that `controller` (via `storage_type.controller`/`reporting_point.controller`) is handed to the controller, which dispatches the equipment and confirms completion over its channel. This is why an AutoStore fleet needs neither the grid in its `served_points` nor an explicit per-`movement_rule` executor: the shared `controller` is the link. |
| `controller_definition` (system/installation) | A distinct technical automation system within the warehouse that the WCS delegates tasks to - a PLC, a Material Flow Controller (MFC), a vendor-specific AS controller (e.g. AutoStore), etc. Deliberately generic: "PLC" was too narrow since not every downstream system is a PLC. A warehouse can contain several. Carries `name` and `reference_number` for identification (see `structure/wcs.yaml`). Referenced by `reporting_point.controller` and `storage_type.controller`. |

## Rack vs. Block vs. Channel Location

| | Rack (`access_model: rack`) | Block (`access_model: block`) | Channel (`access_model: channel`) |
|---|---|---|---|
| Access | Every point individually reachable (`access_order: direct`) | Only from front/top (`access_order: lifo`) | Only in sequence, front-to-back (`channel_depth` positions) |
| Flow direction | n/a | Single side (LIFO) | `entry_side` = `exit_side` → LIFO. `entry_side` ≠ `exit_side` → FIFO (flow-through) |
| Capacity | Usually 1 load unit per `storage_point` | Multiple load units per `storage_point` (depth x height) | Multiple load units per channel (`channel_depth`) |
| Article mix | Any | Usually only one article at a time (`homogeneity_required`) | Usually only one article per channel (`homogeneity_required`) |
| Typical for | High-bay rack, shuttle storage | Large quantities, seasonal goods | Satellite/drive-in racking, flow racks, high-turnover pallet channels |

**Channel logic (Kanallogik):** A `channel` is a storage lane with several positions in
depth (`channel_depth`). Positions must be filled/emptied strictly in sequence — you
cannot access a position in the middle without moving the ones in front of it. If
`entry_side` and `exit_side` are the same, it behaves like a LIFO block (last pallet in,
first pallet out). If they differ, it's a FIFO flow channel (goods enter one side, exit
the opposite side — typical for satellite/drive-in racking or gravity flow racks).

## Process Rules (not physical structure, own lifecycle)

| Term | Meaning |
|---|---|
| `movement_rule` | Defines whether a goods movement is functionally permitted (**"may"**) — independent of physical reachability (`lane`) |
| `movement_rule.execution` | Optional `manual`/`automated` marker for who carries out a leg. `automated` = both endpoints sit under the same `controller`, so that controller's equipment performs it; `manual` = an operator leg, no equipment. Redundant with the endpoints' controllers on purpose — `tools/validate.py` flags an `execution` that contradicts them. The finer mode (`controller_autonomous`/`wms_controlled`) stays on `equipment.mode`. |
| `movement_policy` | `default_allow` (manual areas, only prohibitions explicit) vs. `explicit_only` (conveyor areas, every route must be explicitly defined) |
| `replenishment_strategy` | Replenishment rule: `min_max`, `quantity_based`, `zero_stock`, `predictive` |
| Cross-dock | A `movement_rule` with `trigger: "cross_dock_task"` connecting inbound staging directly to outbound staging, bypassing storage entirely. Modeled as a normal `movement_rule` between two `section`s of the `STAGING` storage_type - no separate entity needed. |

## Reusable Catalogs (`elements/`)

Modeled after SAP EWM master data concepts. Rarely change, shared across
all customers, referenced by ID from structure and strategy files.

| Term | Meaning |
|---|---|
| `load_unit_type` | Physical definition of a load unit (pallet, mesh box, carton). Referenced by `movement_rule.allowed_load_unit_types` (which unit types a *route* accepts) and by `storage_type.default_attributes.allowed_load_unit_types` (which unit types a *storage place* accepts). SAP EWM equivalent: Ladeeinheitentyp (LE-Typ). |
| `equipment_type` | Catalog of equipment classes with their capabilities (payload, speed). Referenced by `equipment.type` in `structure/wcs.yaml`. SAP EWM equivalent: Ressourcentyp. Manhattan WMS equivalent: Equipment Type. |
| `process_type` | Category of warehouse process (inbound/outbound/internal movement). Referenced by `movement_rule.trigger`. SAP EWM equivalent: Prozesstyp/Lagerprozess. |
| `blocking_reason` | Catalog of reasons a `storage_point` can be blocked. Referenced by `storage_type.exceptions[].blocked_reason`. SAP EWM equivalent: Sperrgrund. |
| `hazmat_class` | Catalog of hazardous material / compliance classifications. Referenced by `storage_type.default_attributes.hazmat_classes` to certify/restrict a storage area to specific hazard classes. Manhattan WMS equivalent: compliance zone classification. |

## Key Architectural Principles

1. **Structure vs. runtime state**: These YAML files describe only the
   desired state. Current occupancy, availability, and inventory live in
   the runtime database — analogous to Terraform code vs. actual
   cloud resource status.

2. **`lane`/`conveyor_segment` ("can") vs. `movement_rule` ("may")**:
   A shuttle can physically travel from the cold zone to the ambient
   zone (lane exists), but the movement of goods is functionally
   forbidden (cold chain). Both levels are deliberately modeled separately.

3. **`movement_policy` depending on degree of automation**:
   - Manual areas: physical flexibility always exists (a forklift can
     drive anywhere a path exists) → `default_allow` with
     explicit prohibitions is sufficient.
   - Automated/conveyor areas: the infrastructure itself is
     the constraint, there is no implicit flexibility → every route
     must exist explicitly (`explicit_only`).

4. **`storage_point_generator` instead of enumeration**: With thousands
   of `storage_point`s, a flat listing becomes unmanageable (Git diffs,
   merge conflicts). Templates + explicit `exceptions` keep the file
   compact, independent of the physical warehouse size.

5. **Structure (`structure/`) vs. strategies (`strategies/`)**: Separate
   folders/lifecycles, because they change at different frequencies and
   have different target audiences (technician vs. logistics planner).

6. **`storage_point_ref: true` as a reusable pattern**: Several entities
   are not primarily storage locations but still need to hold bookable
   inventory (WIP, staged goods) — `reporting_point` and `work_center`
   both use this flag rather than being redefined as `storage_type`s.
   This keeps their functional role (communication point, activity
   location) separate from the fact that they also carry stock.
