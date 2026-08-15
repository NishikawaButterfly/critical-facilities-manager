# 6. Maintenance orders

[← Assets](05-assets.md) · [Manual index](README.md) · [Next: Procedures →](07-procedures.md)

A maintenance order is one unit of work against one asset. It is the object the
rest of the workflow hangs from: permits authorize an order, evidence attaches
to an order, constraints are checked when an order starts.

## The two types

| Type | Meaning |
|------|---------|
| `preventive` | Planned upkeep — an inspection, a service, a scheduled replacement |
| `corrective` | A repair — something is wrong and this fixes it |

The type is chosen at creation and cannot be changed afterwards. It affects
nothing except reporting and the filter; the workflow is identical for both.

Corrective orders raised from an incident are created as `corrective`
automatically — see [Incidents](10-incidents.md).

## The state machine

```
        ┌──────────────────────────────┐
        │                              │
     draft ──▶ scheduled ──▶ in_progress ──▶ done
        │           │              │
        └───────────┴──────────────┴──▶ cancelled
```

| Status | What it means operationally | May move to |
|--------|-----------------------------|-------------|
| `draft` | The work is written down but not committed to. Nobody is expected to do it. | `scheduled`, `cancelled` |
| `scheduled` | Committed and planned. Permits may now be requested against it. | `in_progress`, `cancelled` |
| `in_progress` | The work is happening. Constraints have been checked. Evidence may be attached. | `done`, `cancelled` |
| `done` | Finished, with completion notes on the record. **Terminal.** | — |
| `cancelled` | Abandoned at whatever stage it had reached. **Terminal.** | — |

Both terminal states are permanent. There is no reopening a completed order and
no un-cancelling a cancelled one; if work turns out to be needed after all, you
raise a new order.

Attempting an illegal move names what is allowed instead:

```
{"status":409,"error_code":"maintenance_order.invalid_transition",
 "detail":"Maintenance order status cannot change from 'scheduled' to 'done'; allowed: cancelled, in_progress."}

{"status":409,"error_code":"maintenance_order.invalid_transition",
 "detail":"Maintenance order status cannot change from 'done' to 'scheduled'; allowed: none."}
```

## The four transitions

Each is a separate operation with its own address. There is no "set status"
field anywhere.

### Schedule (`draft` → `scheduled`)

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/maintenance-orders/{id}/schedule
```

No body. This is the point at which the order becomes real: a
[work permit](08-work-permits.md) can only be requested against a `scheduled`
or `in_progress` order.

### Start (`scheduled` → `in_progress`)

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/maintenance-orders/{id}/start
```

No body. **This is where the redundancy check fires.** If an active
mutual-exclusion constraint contains this order's asset and another member
asset already has an order in progress, the start is refused:

```
{"status":409,"error_code":"maintenance_order.mutual_exclusion_conflict",
 "detail":"Constraint 'UPS redundancy pair' (586b0568-36a5-4f5b-9379-831ae84df916) allows only one of its member assets under maintenance at a time; order af881c0d-29e8-404c-9d1d-692b677dd31a ('UPS 1 battery string replacement') is already in progress on asset 4cd1ad3e-1436-410b-a7ee-f2c86143fd8d."}
```

The message names the constraint, the conflicting order, its title, and the
asset — everything you need to go and ask whether that work can wait.

**Unless you could not have read that order.** When the blocking work sits on a
site your grants do not cover, the same 409 with the same `error_code` names
the constraint and says only that a member asset outside your grants is under
maintenance: no order id, no title, no asset id. A refusal does not hand over
what a `GET` on that order would have refused you. See
[Operational constraints](09-operational-constraints.md#when-the-blocking-order-is-on-a-site-you-cannot-read).

**Starting does not require a work permit.** An order with no permit at all, or
with a permit still sitting at `requested`, starts perfectly happily. If your
site's rule is that work never begins without an issued permit, that rule is
yours to enforce; the software does not. See
[Known limitations](19-known-limitations.md).

**Starting does not change the asset's status** either. Move the asset to
`under_maintenance` yourself if that is true of the equipment.

### Complete (`in_progress` → `done`)

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"completion_notes":"Battery string A replaced. Cell voltages recorded in the attached evidence file. UPS returned to normal operation."}' \
    $API/maintenance-orders/{id}/complete
```

**Completion notes are mandatory**, and nothing but whitespace does not count:

```
Omitted entirely, or empty:
{"status":422,"error_code":"api.validation_failed",
 "invalid_params":[{"name":"body.completion_notes","reason":"Field required"}]}

Whitespace only:
{"status":422,"error_code":"maintenance_order.completion_notes_required",
 "detail":"Completion notes are required to complete a maintenance order."}
```

**An issued permit blocks completion.** This is the one place a permit is
actually enforced:

```
{"status":409,"error_code":"maintenance_order.open_permit",
 "detail":"The maintenance order still has an issued work permit open; close or revoke the permit before completing the order."}
```

Close the permit (with its own completion note) or revoke it, then complete the
order. Permits at `requested` do not block — only `issued` ones do.

### Cancel (from `draft`, `scheduled`, or `in_progress`)

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"reason":"Deferred to the next maintenance window at the client request."}' \
    $API/maintenance-orders/{id}/cancel
```

The reason is **optional**, and the body may be omitted entirely. Supply one
anyway: an order that simply stops with no explanation is exactly the record
someone will be trying to interpret in a year.

The reason is stored in the same `completion_notes` field a completed order
uses. A cancelled order therefore shows its reason under a field named for
completion; read the `status` alongside it.

Cancelling an in-progress order does *not* close its permits. Do that
separately.

## Creating an order

**API only.** The web interface can move an existing order through its states
but cannot create one.

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"order_type":"preventive","asset_id":"4cd1ad3e-1436-410b-a7ee-f2c86143fd8d",
         "title":"UPS 1 battery string replacement",
         "description":"Replace battery string A under the approved MOP; UPS 2 carries the load.",
         "due_date":"2026-09-30"}' \
    $API/maintenance-orders
{
  "id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "order_type": "preventive",
  "status": "draft",
  "asset_id": "4cd1ad3e-1436-410b-a7ee-f2c86143fd8d",
  "title": "UPS 1 battery string replacement",
  "description": "Replace battery string A under the approved MOP; UPS 2 carries the load.",
  "due_date": "2026-09-30",
  "completion_notes": null,
  "created_at": "2026-08-13T18:24:04.490959Z",
  "updated_at": "2026-08-13T18:24:04.490965Z"
}
```

| Field | Rules |
|-------|-------|
| `order_type` | Required: `preventive` or `corrective` |
| `asset_id` | Required; must exist |
| `title` | Required, 1–255 characters |
| `description` | Optional free text |
| `due_date` | Required, `YYYY-MM-DD` |

Every order starts at `draft`. There is no way to create one already scheduled.

`due_date` accepts dates in the past without complaint — useful when recording
work that was already overdue. Nothing in the system acts on a due date: no
alert, no escalation, no automatic status change. It is a date you can filter
and sort by, and nothing more. (Punch items are the one object with an
`overdue` filter; orders have none.)

An unknown asset is refused with `maintenance_order.asset_not_found` (422, not
404, because the asset was a value in your payload rather than the thing the
URL addressed).

## Editing an order

`PATCH` changes `title`, `description`, and `due_date` — **only while the order
is `draft` or `scheduled`**:

```
$ curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"due_date":"2026-10-15"}' $API/maintenance-orders/{id}
```

Once work has begun, the description is frozen:

```
{"status":409,"error_code":"maintenance_order.not_editable",
 "detail":"A maintenance order can only be edited while draft or scheduled; this one is 'done'."}
```

The asset and the type can never be changed. An order raised against the wrong
asset is cancelled and re-raised.

## Finding orders

**In the web interface:** the [Order board](15-web-interface.md#order-board)
shows every order in five columns by status, with the asset tag, due date,
description, and completion notes on each card. It is the best view of the
current workload in the product.

**Over the API:**

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/maintenance-orders?status=in_progress"
```

| Parameter | Effect |
|-----------|--------|
| `asset_id` | Orders against one asset |
| `status` | One workflow status |
| `order_type` | `preventive` or `corrective` |

Results come back oldest first, by creation time. There is no sort option, no
due-date filter, no "overdue" filter, and no way to list orders for a whole site
or location — you filter by one asset at a time or read the lot.

## Reading an order's history

Everything that ever happened to an order is in the audit trail:

```
$ curl -H "Authorization: Bearer $TOKEN" \
    "$API/audit-entries?entity_type=maintenance_order&entity_id=af881c0d-..."
```

```
#86  2026-08-13T18:26:26.025488Z  priya-eng  status_changed     scope=installation
#84  2026-08-13T18:25:37.652833Z  priya-eng  evidence_attached  scope=installation
#80  2026-08-13T18:24:26.859354Z  priya-eng  status_changed     scope=installation
#77  2026-08-13T18:24:13.002323Z  priya-eng  status_changed     scope=installation
#76  2026-08-13T18:24:04.498213Z  priya-eng  created            scope=installation
```

Newest first. Each `status_changed` entry carries the before and after states,
and the completion notes when they were set. See
[The audit trail](14-audit-trail.md).

---

[← Assets](05-assets.md) · [Manual index](README.md) · [Next: Procedures →](07-procedures.md)
