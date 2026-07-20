# AutoStore Example Walkthrough

This document explains the core modeling concepts of this repo using the
concrete example in [`customers/autostore_customer/`](../customers/autostore_customer/).
For the general term-by-term reference, see
[`entity-glossary.md`](entity-glossary.md); this document instead walks
through *why* the AutoStore example looks the way it does, in the order
the decisions were made.

If you just want the file layout, see the building's
[`warehouse.yaml`](../customers/autostore_customer/facilities/facility_as01/buildings/hall_1/warehouse.yaml):

```
structure/storage.yaml        # the grid, the ports (work_center side), ENTRY/EXIT
structure/wcs.yaml             # controller, the ports (reporting_point side), the robot fleet
strategies/movement_rules.yaml # every allowed goods movement, decant + putaway + pick + return
strategies/replenishment.yaml  # deliberately empty - see "Why there is no replenishment" below
```

`structure/lanes.yaml` contains only the explicit manual operator legs; the
AutoStore controller-internal graph remains opaque.

## 1. The grid is one opaque `storage_point`, not a rack of columns

AutoStore's real physical grid is hundreds of stacked bin columns with a
robot on top. This repo does **not** model those columns. Instead:

```yaml
# structure/storage.yaml
- id: "AUTOSTORE_GRID"
  access_model: "block"
  controller: "CTRL_ASC_01"
  storage_points:
    - coordinate: "AS_GRID"        # the whole grid, one opaque block place
  default_attributes:
    capacity_per_point: 4800       # nominal total bin slots the controller reports
```

Why: the internal column/bin/LIFO mechanics are the AutoStore controller's
job, not the WMS's. The WMS only ever needs "the grid has N bins free",
never "column 07/12 is full". Modeling 300 individual columns would add
detail this repo can't actually use anywhere (no `movement_rule` ever
addresses a single column) and would fight the controller's own black box.

This uses the `storage_points` field (an explicit list), **not**
`storage_point_generator`. A generator is for expanding a *grid of many
points* from a compact pattern - exactly the wrong tool when you want
precisely **one** point. `storage_points` exists for this case: a small,
fixed set of locations that aren't worth (or can't be) generated. See
`docs/entity-glossary.md` → `storage_points (explicit)`, and
`schemas/storage-type.schema.json`.

Compiling this building (`tools/compile.py`) confirms it - the grid
contributes exactly one point, everything else is ports:

```
AUTOSTORE_GRID: 1
work_center:    6   # WC_PORT_01..04, ENTRY, EXIT
reporting_point:4   # RP_PORT_01..04
TOTAL: 11
```

## 2. Every port is modeled *twice*, on purpose

A physical AutoStore port is one place, but it plays two roles that hold
two different kinds of stock, so it's two `storage_point`s:

| | `work_center` (`structure/storage.yaml`) | `reporting_point` (`structure/wcs.yaml`) |
|---|---|---|
| Represents | the operator / ergonomic side | the controller-communication side |
| Holds | the operator's WIP: an inbound carton (induction) or a filled `order_tote` (picking) | the AutoStore bin itself, in the controller's custody |
| Example id | `WC_PORT_03` | `RP_PORT_03` |

This is the `storage_point_ref: true` pattern (glossary principle 6):
neither is a `storage_type` in its own right, but both need to hold
bookable inventory, so both get the flag - and `tools/compile.py`
compiles each into its own `storage_point` (id = the entity's own id).

Because these are genuinely different stock, there has to be an explicit
**handoff** `movement_rule` between them wherever goods cross from one
side to the other - see the flows below. This was a design iteration:
the first version of this example only had *one* leg per port and
routed everything through the `reporting_point`, leaving the four
`work_center`s declared but never referenced by any rule. The handoff
legs (`DECANT_INTO_BIN_*`, `PICK_FROM_BIN_*`) closed that gap.

## 3. Who executes a movement: the shared `controller` is the link

`ROBOT_FLEET_01` (`structure/wcs.yaml`) is `mode: controller_autonomous`
- the WMS never dispatches a specific robot or path, it only ever asks
the AutoStore Controller (`CTRL_ASC_01`) to move a bin. So how does the
model say "this movement is done by the robot fleet"? Not via
`served_points` (which only lists the ports) - via a **shared
`controller`**:

```yaml
# structure/storage.yaml
- id: "AUTOSTORE_GRID"
  controller: "CTRL_ASC_01"

# structure/wcs.yaml
reporting_points:
  - id: "RP_PORT_01"
    controller: "CTRL_ASC_01"

equipment:
  - id: "ROBOT_FLEET_01"
    controller: "CTRL_ASC_01"        # <- the binding
    mode: "controller_autonomous"
```

A movement whose `from`/`to` endpoints **both** carry `controller:
CTRL_ASC_01` is handed to that controller as a whole; the controller
dispatches a robot and reports completion back over its channel
(`CH_ASC_01`). This is exactly why `AUTOSTORE_GRID` deliberately does
**not** appear in `served_points`: the grid's interior stays the
controller's black box (section 1), and the fleet's reach is expressed
through the controller it shares with the grid and the ports, not
through an explicit point list that would leak grid detail back in.

This is also why a movement needs an **explicit** `execution` marker
(see section 5) rather than relying on this derivation alone everywhere:
the derivation only works for automated legs (both endpoints need a
controller). A leg between `ENTRY` and `WC_PORT_01` has no controller on
either side - there's nothing to derive from, so `manual` has to be
stated.

## 4. The two full flows

Both flows are symmetric by design - if you understand one, the other
is a mirror image.

### Induction (decant)

An inbound carton is repacked into an AutoStore bin (shown for port 1 -
port 2 is the identical pattern):

```
ENTRY ────────────────────► WC_PORT_01      MOVE_ENTRY_TO_INDUCTION
  (carton, manual)                          carton arrives as WIP at the port

AUTOSTORE_GRID ────────────► RP_PORT_01      PROVIDE_EMPTY_BIN
  (autostore_bin, automated)                 robot delivers an EMPTY bin

WC_PORT_01 ─────────────────► RP_PORT_01     DECANT_INTO_BIN_01
  (carton → autostore_bin, manual,           operator decants the carton
   conversion_of_load_unit_required)         into the bin - THIS is the
                                              "Einlagerung" moment

RP_PORT_01 ─────────────────► AUTOSTORE_GRID STOW_BIN_TO_GRID
  (autostore_bin, automated)                 robot stows the now-full bin
```

Two things worth noting:
- The bin **itself** never touches the `work_center` - only the goods
  do (as a `carton`, then conceptually inside the bin). The bin's own
  round trip is entirely on the `reporting_point` ↔ `AUTOSTORE_GRID`
  side.
- `bin_provision` (`elements/process_types.yaml`) is a dedicated
  `process_type` for "robot brings an empty bin" - distinct from
  `putaway_task`, which is reserved for the leg that actually adds
  stock to the grid.

### Picking

Shown for port 3 - port 4 is the identical pattern:

```
AUTOSTORE_GRID ────────────► RP_PORT_03      ROUTE_GRID_TO_PICK
  (autostore_bin, automated)                 robot delivers the FULL bin

RP_PORT_03 ─────────────────► WC_PORT_03     PICK_FROM_BIN_01
  (autostore_bin → order_tote, manual,       operator takes the ordered
   conversion_of_load_unit_required)         goods out of the bin into an
                                              order_tote - THIS is the
                                              "Entnahme" moment

RP_PORT_03 ─────────────────► AUTOSTORE_GRID RETURN_BIN_TO_GRID
  (autostore_bin, automated)                 robot re-stows the bin (with
                                              its remaining stock)

WC_PORT_03 ─────────────────► EXIT           MOVE_PICK_TO_EXIT
  (order_tote, manual)                       filled tote leaves the cell
```

Note the bin and the goods **separate** here: the bin goes back into the
grid (`RP_PORT_03 → AUTOSTORE_GRID`), while the picked goods continue on
as an `order_tote` (`WC_PORT_03 → EXIT`) - two different load unit types
moving in two different directions from the same handoff point. The
`RETURN_BIN_TO_GRID` leg starts at `RP_PORT_03`, not `WC_PORT_03`,
because the bin itself never left the reporting_point side.

### Why some legs are one rule for both ports, and some aren't

Notice `PROVIDE_EMPTY_BIN`/`STOW_BIN_TO_GRID`/`ROUTE_GRID_TO_PICK`/
`RETURN_BIN_TO_GRID`/`MOVE_ENTRY_TO_INDUCTION`/`MOVE_PICK_TO_EXIT` above
have no `_01`/`_02` suffix - each is **one** `movement_rule` covering
both ports, via a list instead of a single id:

```yaml
- id: "PROVIDE_EMPTY_BIN"
  from: { storage_type: "AUTOSTORE_GRID" }
  to: { reporting_point: ["RP_PORT_01", "RP_PORT_02"] }
  ...
```

This works because `AUTOSTORE_GRID` is the same physical grid regardless
of which port receives the bin - only the `to` side varies, so it can be
a list (`schemas/movement-rule.schema.json`'s `idOrIds`).

`DECANT_INTO_BIN_01`/`_02` and `PICK_FROM_BIN_01`/`_02` stay split,
deliberately. There, **both** endpoints vary together: port 1's
`WC_PORT_01` only ever hands off to port 1's `RP_PORT_01`, never to
`RP_PORT_02`. Turning both sides into lists (`from: [WC_PORT_01,
WC_PORT_02]`, `to: [RP_PORT_01, RP_PORT_02]`) wouldn't just be shorter -
it would also silently permit `WC_PORT_01 → RP_PORT_02`, a cross-port
connection that doesn't exist physically. The rule of thumb: a list is
safe exactly when the *other* endpoint is a single, shared id - once
both sides vary in lockstep, keep them as separate rules.

## 5. `execution`: making manual vs. automated explicit - and checked

Every rule above carries `execution: "manual"` or `execution:
"automated"`. This field is deliberately **redundant** with what the
endpoints' controllers already imply (section 3) - and that redundancy
is checked, not just documented:

```yaml
# schemas/movement-rule.schema.json
"execution": {
  "enum": ["manual", "automated"],
  "description": "... 'automated' requires both endpoints under the
    same controller ..."
}
```

`tools/validate.py` derives the *implied* execution from the endpoints
(`automated` iff `from` and `to` sit under the same `controller`, else
`manual`) and flags a contradiction. Try it: flip `PICK_FROM_BIN_01`'s
`execution` to `"automated"` and re-run
`python tools/validate.py customers/autostore_customer/company.yaml` -
you'll get:

```
❌ movement_rule 'PICK_FROM_BIN_01': execution 'automated' contradicts
   the endpoints (from controller='CTRL_ASC_01', to controller=None
   -> implies 'manual').
```

This exists because "who executes this" is operationally important
(dispatch logic, staffing, SLAs) but easy to get subtly wrong by hand
when endpoints change - the check catches that class of error the same
way schema validation catches a typo'd enum value.

## 6. Why `lanes.yaml` only contains manual connections

Other buildings in this repo (e.g. `example_customer/hall_3`) have a
`structure/lanes.yaml` describing physical conveyor connectivity ("can")
separately from `movement_rules.yaml` ("may"). This building uses explicit
`connections` for the manual ENTRY/port/EXIT legs.

Grid-to-port connectivity is intrinsic to what an AutoStore
installation *is* - there's no separate conveyor/lane technology choice
to make or document, unlike, say, a shuttle system where the lane layout
is a real design decision. Modeling a `lane`/`conveyor_segment` here
would describe infrastructure that doesn't exist as a distinct thing to
configure. The manual handovers and walking paths do exist physically and are
therefore explicit can-edges. `ENTRY` and `EXIT` remain plain work centers; no
door or conveyor infrastructure is invented.

## 7. Why there is no replenishment strategy

`strategies/replenishment.yaml` is present (every building imports one)
but its list is empty. Classic `replenishment_strategy` (see
`example_customer`'s `REPL_PICKFACE_A`) moves goods from bulk storage to
a *separate, fixed* pick face. AutoStore collapses that distinction: the
port **is** the pick face, a robot brings whichever bin is needed
directly to it. What AutoStore installations do have - continuous
induction of new stock into the grid - is already fully expressed by the
induction `movement_rule`s in section 4; there's no separate storage
area upstream of `ENTRY` in this minimal example to define a
`replenishment_strategy` against.

## Quick reference: which file answers which question

| Question | File |
|---|---|
| How big is the grid, what does it hold? | `structure/storage.yaml` → `AUTOSTORE_GRID` |
| What are the ports, physically? | `structure/storage.yaml` → `work_centers` (`WC_PORT_*`, `ENTRY`, `EXIT`) |
| Which controller runs the grid, and what does it look like on the wire? | `structure/wcs.yaml` → `controller_definitions` |
| What are the ports, from the controller's point of view? | `structure/wcs.yaml` → `reporting_points` (`RP_PORT_*`) |
| Who executes movements, and how is that equipment bound to the controller? | `structure/wcs.yaml` → `equipment` (`ROBOT_FLEET_01`) |
| What is allowed to move where, and who carries it out? | `strategies/movement_rules.yaml` |
| How does stock get replenished? | `strategies/replenishment.yaml` (deliberately empty - see section 7) |
