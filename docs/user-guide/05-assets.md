# 5. Assets

[← Sites and locations](04-sites-and-locations.md) · [Manual index](README.md) · [Next: Maintenance orders →](06-maintenance-orders.md)

An asset is one piece of equipment. Everything operational in this system —
orders, permits, incidents, tests, most punch items, constraints — points at an
asset.

## What an asset record holds

| Field | Meaning |
|-------|---------|
| `tag` | The identifier your facility uses: `UPS-C-01`. Unique within its site. |
| `name` | The readable name: `UPS System 1`. |
| `asset_type` | Free text category: `ups`, `chiller`, `crac`, `pdu`, `generator`. Not a fixed list — whatever you type becomes the type. |
| `criticality` | One of `low`, `medium`, `high`, `critical`. |
| `status` | Where the asset is in its lifecycle (below). |
| `location_id` | The location it sits at — any level of the hierarchy. |
| `site_id` | Derived from the location, never set directly. This is what permissions are scoped by. |

The demo campus:

| Tag | Name | Type | Criticality | Status | Location |
|-----|------|------|-------------|--------|----------|
| `UPS-C-01` | UPS System 1 | ups | critical | operational | L1-ELEC |
| `UPS-C-02` | UPS System 2 | ups | critical | operational | L1-ELEC |
| `GEN-C-01` | Standby Generator 1 | generator | critical | operational | BLDG-C (a building) |
| `CRAC-C-01` | CRAC Unit 1 | crac | high | operational | L1-DH1 |
| `CRAC-C-02` | CRAC Unit 2 | crac | high | installed | L1-DH1 |
| `CHW-C-01` | Chiller 1 | chiller | high | operational | L2-MECH |
| `PDU-C-01` | PDU 1A | pdu | medium | operational | L1-DH1 |

### About `asset_type` and `criticality`

`asset_type` is free text with no validation beyond length. Nothing enforces
that every UPS is typed `ups` rather than `UPS` or `ups-system`. Agree a
convention before you load an estate, because the type is a filter key and
inconsistent values fragment your lists silently.

`criticality` is descriptive only. Nothing in the system behaves differently for
a `critical` asset than for a `low` one — it does not change what work is
allowed, who may authorize it, or what constraints apply. If you want two
redundant units protected from simultaneous maintenance, that is a
[constraint](09-operational-constraints.md), not a criticality.

## The asset lifecycle

```
planned ──▶ installed ──▶ operational ──▶ under_maintenance
                               │                  │
                               │                  │
                               ▼                  ▼
                        decommissioned ◀──────────┘
```

| From | May move to | What it means |
|------|-------------|---------------|
| `planned` | `installed` | On the drawing, not yet physically present |
| `installed` | `operational` | Physically in place, not yet accepted into service |
| `operational` | `under_maintenance`, `decommissioned` | In service |
| `under_maintenance` | `operational`, `decommissioned` | Temporarily out of service for work |
| `decommissioned` | — terminal | Retired; kept for the record |

The path is one-way. There is no route back from `installed` to `planned`, and
nothing leaves `decommissioned`.

**Nothing moves an asset's status automatically.** Starting a maintenance order
on an asset does *not* set it to `under_maintenance` — the demo's UPS stayed
`operational` throughout an order that ran from `scheduled` to `done`. Status is
a statement you make about the equipment, separately, when it becomes true.
[Common workflows](16-common-workflows.md) shows where in a job to make it.

### Changing status

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"to_status":"under_maintenance"}' \
    $API/assets/4cd1ad3e-1436-410b-a7ee-f2c86143fd8d/transition
{ ... "tag": "UPS-C-01", "status": "under_maintenance", ... }
```

An illegal move is refused, and the message names what *is* allowed from here:

```
{"status":409,"error_code":"asset.invalid_transition",
 "detail":"Asset status cannot change from 'operational' to 'planned'; allowed: decommissioned, under_maintenance."}

{"status":409,"error_code":"asset.invalid_transition",
 "detail":"Asset status cannot change from 'installed' to 'under_maintenance'; allowed: operational."}
```

That second one catches people out during commissioning: a newly installed unit
must be accepted into service (`operational`) before it can be taken out of it.

**Web interface:** none of this is available. The asset tree shows the status as
a badge and offers no way to change it.

## Finding an asset

**In the web interface:** the Asset tree view lists every asset under its
location; clicking one opens a detail panel with its status, criticality, type,
site, location, id, and timestamps. There is no search box and no filter — on a
real estate you scroll.

**Over the API:**

```
$ curl -H "Authorization: Bearer $TOKEN" \
    "$API/assets?site_id=6eddaddd-...&criticality=critical"
{
  "items": [ UPS-C-01, UPS-C-02, GEN-C-01 ],
  "total": 3, "limit": 50, "offset": 0
}
```

Filters:

| Parameter | Effect |
|-----------|--------|
| `location_id` | Assets at exactly that location — not the ones below it |
| `site_id` | Every asset anywhere in that site |
| `status` | One lifecycle status |
| `criticality` | One criticality |

**There is no filter on `tag` and no text search anywhere in the product.** To
find `UPS-C-01` when you know only its tag, you list the site's assets and look
— which is what the examples in this manual do. It is the single most keenly
felt gap in version 0.2.0; see
[Known limitations](19-known-limitations.md).

Fetch one directly when you have its id:

```
$ curl -H "Authorization: Bearer $TOKEN" $API/assets/4cd1ad3e-1436-410b-a7ee-f2c86143fd8d
```

## Creating an asset

**API only.**

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"tag":"UPS-C-03","name":"UPS System 3","asset_type":"ups",
         "criticality":"critical","location_id":"4727f00f-...","status":"planned"}' \
    $API/assets
```

| Field | Rules |
|-------|-------|
| `tag` | Required, 1–64 characters, unique within the site |
| `name` | Required, 1–255 characters |
| `asset_type` | Required, 1–128 characters, free text |
| `criticality` | Required: `low`, `medium`, `high`, `critical` |
| `location_id` | Required; any location, any level |
| `status` | Optional; defaults to `planned` |

`status` may be set to anything at creation — the transition table governs
changes afterwards, not the starting point. Recording an estate that is already
running is therefore a matter of creating each asset directly as `operational`.

The site is derived from the location and cannot be supplied. That is what makes
tag uniqueness meaningful: `UPS-C-01` may exist once per site.

```
{"status":409,"error_code":"asset.duplicate_tag",
 "detail":"Asset tag 'UPS-C-01' is already used within the same site."}
```

An unknown location is refused with `asset.location_not_found`.

## Editing an asset

`PATCH` changes `tag`, `name`, `asset_type`, `criticality`, and `location_id`.
It does not change `status` — that is the transition endpoint — and it cannot
change `site_id` directly.

```
$ curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"name":"UPS System 1 (string A renewed 2026)"}' $API/assets/4cd1ad3e-...
```

There is no restriction on when an asset may be edited. A decommissioned asset
can still be renamed.

### Moving an asset

Changing `location_id` moves the asset. If the new location is under a
different site, the asset's `site_id` follows it, and:

- **You need write authority over both sites** — the one it leaves and the one
  it arrives at. The audit entry records the destination site's scope.
- **The tag is re-checked against the destination site.** A move that would
  collide with an existing tag there is refused with `asset.duplicate_tag`.

Moving an asset does not move its history. Orders, incidents, and tests keep
pointing at the asset, so they follow it to the new site, and the permissions
needed to work on them change accordingly.

## What an asset does not have

No manufacturer, model, serial number, installation date, warranty, or
commissioning date field. No parent-child relationship between assets — a PDU
cannot be recorded as fed from a particular UPS, and a UPS cannot own its
battery strings. No attachments on the asset itself: evidence files attach to
work, not to equipment. See
[Known limitations](19-known-limitations.md).

---

[← Sites and locations](04-sites-and-locations.md) · [Manual index](README.md) · [Next: Maintenance orders →](06-maintenance-orders.md)
