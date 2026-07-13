# Entity Glossary

Terms modeled after SAP EWM/MFS terminology (for compatibility with
industry vocabulary), but named independently where it made more sense
(`storage_point` instead of "Bin").

## Structure

| Term | Meaning |
|---|---|
| `warehouse` | Top level, a warehouse complex/a building |
| `storage_type` | Storage area, groups `storage_point`s (e.g. high-bay rack, block storage) |
| `section` | Subdivision of a `storage_type` by properties (e.g. access frequency) |
| `storage_point` | Smallest physical/logical storage unit (formerly "storage bin"). Rack location or block location. |
| `storage_point_generator` | Generates `storage_point`s from a grid instead of enumerating them individually |
| `activity_area` | Functional cross-cutting grouping, orthogonal to the physical hierarchy. A `storage_point` can belong to multiple `activity_area`s simultaneously. |
| `work_center` | Physical unit for activities such as packing, weighing |
| `door` / `staging_area` | Doors for goods receipt/dispatch |
| `lane` / `conveyor_segment` | Physical connection/conveyor technology between areas (**"can"**) |
| `reporting_point` | Communication point between WMS and PLC; is technically always also modeled as a `storage_point` |
| `resource` / `vehicle` | Executing element. `resource` = WMS-controlled, `vehicle` = PLC-autonomous with its own order buffer |
| `plc_definition` (system/installation) | A distinct technical automation system within the warehouse (e.g. one AS/RS/shuttle system with its own PLC). A warehouse can contain several. Carries `name` and `reference_number` for identification (see `structure/wcs.yaml`). |

## Rack Location vs. Block Location

| | Rack location (`access_model: rack`) | Block location (`access_model: block`) |
|---|---|---|
| Access | Every point individually reachable (`access_order: direct`) | Only from front/top (`access_order: lifo`) |
| Capacity | Usually 1 load unit per `storage_point` | Multiple load units per `storage_point` (depth x height) |
| Article mix | Any | Usually only one article at a time (`homogeneity_required`) |
| Typical for | High-bay rack, shuttle storage | Large quantities, seasonal goods |

## Process Rules (not physical structure, own lifecycle)

| Term | Meaning |
|---|---|
| `movement_rule` | Defines whether a goods movement is functionally permitted (**"may"**) — independent of physical reachability (`lane`) |
| `movement_policy` | `default_allow` (manual areas, only prohibitions explicit) vs. `explicit_only` (conveyor areas, every route must be explicitly defined) |
| `replenishment_strategy` | Replenishment rule: `min_max`, `quantity_based`, `zero_stock`, `predictive` |

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
