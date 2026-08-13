# 10. Incidents

[← Operational constraints](09-operational-constraints.md) · [Manual index](README.md) · [Next: Commissioning tests →](11-commissioning-tests.md)

An incident records an unplanned event on an asset — a trip, an alarm, a
failure, a discrepancy someone noticed. It is the entry point for everything
that was not planned.

Nothing raises incidents for you. There is no monitoring integration and no
alarm feed; every incident in this system was typed in by a person who saw
something.

## What an incident records

| Field | Meaning |
|-------|---------|
| `asset_id` | The asset it happened to. Required, and cannot be changed. |
| `severity` | `low`, `medium`, `high`, or `critical`. Descriptive only. |
| `status` | Where it is in the workflow. |
| `title` | What happened, in one line. |
| `description` | Optional detail. |
| `resolution_notes` | How it ended. Required to resolve. |
| `corrective_order_id` | The one corrective maintenance order raised from it, if any. |

`severity` changes nothing about how the system behaves — a `critical` incident
is not escalated, prioritised, or treated differently in any way. It is a label
for the people reading, and it cannot be changed after creation.

An incident belongs to exactly one asset. An event affecting three units is
three incidents, unconnected to each other.

## The state machine

```
open ──▶ investigating ──┬──▶ resolved
                         └──▶ dismissed
```

| Status | What it means | May move to |
|--------|---------------|-------------|
| `open` | Reported. Nobody has picked it up yet. | `investigating` |
| `investigating` | Somebody is looking into it. | `resolved`, `dismissed` |
| `resolved` | Understood and dealt with, with notes. **Terminal.** | — |
| `dismissed` | Not a real problem, or not worth pursuing. **Terminal.** | — |

**An incident cannot be resolved or dismissed straight from `open`** — it must
pass through `investigating` first, even when the cause is obvious. That is one
extra call, and it is what puts a named person against the investigation.

```
{"status":409,"error_code":"incident.invalid_transition",
 "detail":"Incident status cannot change from 'open' to 'resolved'; allowed: investigating."}
```

Both end states are terminal. A recurrence is a new incident.

## Reporting an incident

**API only.** Incidents do not appear in the web interface.

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"asset_id":"6ccedcd6-fa37-4273-b511-b903ebfee630",
         "severity":"high",
         "title":"Generator failed to start on weekly test",
         "description":"Crank attempt timed out; battery charger showing a fault light."}' \
    $API/incidents
{
  "id": "e1629c64-f721-4d59-b665-a3f35c2bd2de",
  "asset_id": "6ccedcd6-fa37-4273-b511-b903ebfee630",
  "severity": "high",
  "status": "open",
  "title": "Generator failed to start on weekly test",
  "description": "Crank attempt timed out; battery charger showing a fault light.",
  "resolution_notes": null,
  "corrective_order_id": null,
  "created_at": "2026-08-13T18:28:13.283047Z",
  "updated_at": "2026-08-13T18:28:13.283051Z"
}
```

| Field | Rules |
|-------|-------|
| `asset_id` | Required; must exist (`incident.asset_not_found` otherwise) |
| `severity` | Required: `low`, `medium`, `high`, `critical` |
| `title` | Required, 1–255 characters |
| `description` | Optional |

Every incident starts at `open`. There is no way to record one that is already
closed, so retrospective entries walk through all three states.

**Permission:** the site of the asset.

## Working an incident

### Start investigating

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/incidents/{id}/start-investigation
{ ... "status": "investigating" ... }
```

No body. Nothing records *who* is investigating on the incident record itself —
only the audit trail carries the actor. There is no assignee field.

### Resolve

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"resolution_notes":"Charger module replaced under the corrective order; generator started on retest."}' \
    $API/incidents/{id}/resolve
```

**Resolution notes are mandatory:**

```
{"status":422,"error_code":"api.validation_failed",
 "invalid_params":[{"name":"body.resolution_notes","reason":"Field required"}]}
```

An incident may be resolved while its corrective order is still open. Nothing
checks that the fix actually happened — the notes are your word for it.

### Dismiss

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"reason":"Sensor reading confirmed spurious; no fault present."}' \
    $API/incidents/{id}/dismiss
```

The reason is **optional** and the body may be omitted. It is stored in
`resolution_notes`. Write one: a dismissed incident with no explanation looks
identical to one somebody could not be bothered with.

## Raising corrective work

An incident may raise **one** corrective maintenance order, while it is `open`
or `investigating`:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"due_date":"2026-08-27",
         "title":"Replace generator start battery and charger",
         "description":"Renew the start battery, replace the faulty charger module, retest under load."}' \
    $API/incidents/{id}/corrective-order
{
  "id": "4f6ea665-63cf-4795-8574-8c34fe0ff5c4",
  "order_type": "corrective",
  "status": "draft",
  "asset_id": "6ccedcd6-fa37-4273-b511-b903ebfee630",
  "title": "Replace generator start battery and charger",
  "description": "Renew the start battery, replace the faulty charger module, retest under load.",
  "due_date": "2026-08-27",
  "completion_notes": null,
  "created_at": "2026-08-13T18:28:14.059423Z",
  "updated_at": "2026-08-13T18:28:14.059428Z"
}
```

| Field | Rules |
|-------|-------|
| `due_date` | Required, `YYYY-MM-DD` |
| `title` | Optional — defaults to the incident's title |
| `description` | Optional — defaults to the incident's description |

The order is created as `corrective`, at `draft`, against the incident's asset,
and from then on it is an ordinary [maintenance order](06-maintenance-orders.md):
schedule it, permit it, start it, complete it. The incident does not follow it —
resolving one does not resolve the other in either direction.

Two refusals:

```
A second corrective order:
{"status":409,"error_code":"incident.corrective_order_exists",
 "detail":"Incident e1629c64-… is already linked to corrective order 4f6ea665-…."}

After the incident is closed:
{"status":409,"error_code":"incident.not_active",
 "detail":"A corrective order can only be created while the incident is open or investigating; this one is 'resolved'."}
```

The one-order limit is worth planning around: if an incident needs work on
several assets, or several separate jobs, raise the first through the incident
and create the others directly, accepting that the link back is only in the
notes.

Raise the corrective order **before** you resolve the incident, or the link is
lost for good.

## The link in the audit trail

Creating a corrective order from an incident writes three audit entries, so the
connection survives even though it lives in one nullable field:

| Entity | Action | Payload |
|--------|--------|---------|
| `maintenance_order` | `created` | The full order |
| `maintenance_order` | `created_from_incident` | `{"incident_id": "e1629c64-…"}` |
| `incident` | `corrective_order_created` | `{"corrective_order_id": "4f6ea665-…"}` |

Reading one incident's history:

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/audit-entries?entity_id=e1629c64-…"
```

```
#104  priya-eng  incident  status_changed            {"status": "resolved", "resolution_notes": …}
#103  priya-eng  incident  corrective_order_created  {"corrective_order_id": "4f6ea665-…"}
#100  priya-eng  incident  status_changed            {"status": "investigating"}
#99   priya-eng  incident  created                   {"id": "e1629c64-…", "asset_id": …}
```

## Finding incidents

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/incidents?status=open&severity=critical"
```

| Parameter | Effect |
|-----------|--------|
| `asset_id` | Incidents on one asset |
| `severity` | `low`, `medium`, `high`, `critical` |
| `status` | `open`, `investigating`, `resolved`, `dismissed` |

`?status=open` is your unattended queue. Nothing else surfaces it: there is no
dashboard, no count anywhere in the web interface, and no notification of any
kind. Somebody has to run this query, and running it has to be somebody's job.

## Evidence on incidents

**None.** Evidence files attach to maintenance orders, work permits,
commissioning tests, and punch items — not to incidents. A photograph of the
fault panel goes on the corrective order instead. See
[Evidence](13-evidence.md) and
[Known limitations](19-known-limitations.md).

---

[← Operational constraints](09-operational-constraints.md) · [Manual index](README.md) · [Next: Commissioning tests →](11-commissioning-tests.md)
