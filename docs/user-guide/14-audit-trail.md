# 14. The audit trail

[← Evidence](13-evidence.md) · [Manual index](README.md) · [Next: The web interface →](15-web-interface.md)

Every write in this system appends one or more lines to the audit trail. The
trail is the only complete history the product keeps, and it is the reason the
rest of the record can be trusted: nothing in it can be changed afterwards, by
anyone, through any route.

## What one entry contains

```
{
  "id": 86,
  "occurred_at": "2026-08-13T18:26:26.025488Z",
  "actor": "priya-eng",
  "entity_type": "maintenance_order",
  "entity_id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "action": "status_changed",
  "scope": "installation",
  "before": {
    "status": "in_progress",
    "completion_notes": null
  },
  "after": {
    "status": "done",
    "completion_notes": "Battery string A replaced. Cell voltages recorded in the attached evidence file. UPS returned to normal operation."
  }
}
```

| Field | Meaning |
|-------|---------|
| `id` | A number that only ever increases. Entries are written in this order, so it is also the true sequence of events. |
| `occurred_at` | UTC timestamp of the change. |
| `actor` | The username behind the token that made the request. Not a display name, not a session. |
| `entity_type` | Which kind of object: `maintenance_order`, `asset`, `work_permit`, `procedure`, `incident`, `commissioning_test`, `punch_item`, `constraint`, `location`, `user`, `api_token`, `site_grant`. |
| `entity_id` | Which one. |
| `action` | What happened (below). |
| `scope` | The authorization that covered the write: `installation`, `site:<id>`, or null. |
| `before` | The state that changed, as it was. Null for creations. |
| `after` | The state that changed, as it became. Null for deletions. |

`before` and `after` are **not** whole snapshots except on creation and
deletion. For an update they carry only the fields that actually changed —
which is what makes the trail readable, and means an entry with a small payload
is a small change, not a lost one.

## The actions

| Action | Written when |
|--------|--------------|
| `created` | An object is created |
| `updated` | Editable fields change |
| `status_changed` | Any workflow transition, on any object |
| `deleted` | A location is deleted, or a site grant removed |
| `deactivated` | A user is deactivated |
| `revoked` | An API token is revoked |
| `evidence_added` | A text note is appended to a commissioning test |
| `evidence_attached` | A file is attached to any record |
| `created_from_incident` | A corrective order is raised from an incident |
| `corrective_order_created` | The matching entry on the incident |
| `created_from_commissioning_test` | A punch item is opened from a failed test |
| `punch_item_created` | The matching entry on the test |

The paired actions are how relationships survive. Raising a corrective order
writes three entries — the order's own `created`, a `created_from_incident` on
the order, and a `corrective_order_created` on the incident — so the connection
is readable from either end even though it lives in a single field.

## The scope field

`scope` records the authority the write went through:

- `installation` — covered by an installation-wide grant.
- `site:6eddaddd-…` — covered by a grant on that site.
- `null` — no site check applied. User management, token management, and
  anything done by the seed or bootstrap scripts.

A site grant is recorded in preference to an installation-wide one when both
would cover the write, so the entry names the narrowest authority that
justified it. When a write touches two sites — moving an asset — the scope
recorded is the destination.

## What you see of the trail

The trail is scoped by the same grants as everything else, but it is scoped on
what each entry *recorded at the time*, not on where the object sits now:

- An **installation-wide grant** shows the whole trail, as before.
- A **site grant** shows the entries whose `scope` is `site:<that site>`. Every
  other entry — `installation`, `null`, or another site — is not in your view,
  and the `total` counts only what you can see.

That rule is stricter than it may look, and deliberately so. An entry marked
`installation` says which *authority* covered the write, not which site the
record belonged to: an installation-wide engineer working on your site records
`installation`, and so does one working on somebody else's. Reading those to
you would mean deciding, today, which site a past event "really" concerned —
and an asset that moved between sites last month would drag its old history
into its new site the moment it arrived. The trail is evidence; it is filtered
on the evidence it already carries.

The practical consequence: on an installation where most work is done under
installation-wide grants, a site-scoped account sees little of the trail. If
site-scoped people need site-scoped history, give the people doing the work
site grants rather than installation-wide ones — the scope label then records
the site, and it records it permanently.

## Reading the trail

### In the web interface

The [Audit timeline](15-web-interface.md#audit-timeline) view shows the trail
newest first, fifty entries at a time, with **Load older entries** at the
bottom. Each entry shows the timestamp, actor, action, entity type and id, the
scope, and collapsible **Before** and **After** payloads. It is the only view in
the product that shows procedures, permits, incidents, tests, punch items, and
evidence at all — as audit lines.

Its counter states the position plainly:

> Showing the 50 most recent of 115 entries. The trail is append-only: the API
> never writes to it from these pages and the database refuses updates and
> deletes on it outright, so every line above is permanent.

### Over the API

```
$ curl -H "Authorization: Bearer $TOKEN" \
    "$API/audit-entries?entity_type=maintenance_order&entity_id=af881c0d-…"
```

| Parameter | Effect |
|-----------|--------|
| `entity_type` | One kind of object |
| `entity_id` | One object |
| `actor` | Everything one username did |
| `limit` / `offset` | Paging, newest first, 200 maximum |

There is **no date range filter**. You cannot ask for "everything last Tuesday";
you page backwards through the trail until you reach it. On a busy installation
that is the difference between an easy question and a tedious one. See
[Known limitations](19-known-limitations.md).

Three queries worth knowing:

```
The complete history of one record:
  ?entity_type=maintenance_order&entity_id=<id>

Everything one person did:
  ?actor=priya-eng

Everything that happened to one entity type:
  ?entity_type=work_permit
```

Note that `entity_id` alone works too, without `entity_type`, and is the way to
see both sides of a linked pair.

### Reading one record's life

The complete history of the UPS battery replacement from
[Workflow 1](16-common-workflows.md#workflow-1-planned-ups-maintenance-end-to-end):

```
#86  2026-08-13T18:26:26.025488Z  priya-eng  status_changed     scope=installation
#84  2026-08-13T18:25:37.652833Z  priya-eng  evidence_attached  scope=installation
#80  2026-08-13T18:24:26.859354Z  priya-eng  status_changed     scope=installation
#77  2026-08-13T18:24:13.002323Z  priya-eng  status_changed     scope=installation
#76  2026-08-13T18:24:04.498213Z  priya-eng  created            scope=installation
```

Five lines: created as a draft, scheduled, started, evidenced, completed. The
gaps in the id sequence — 78, 79, 81 to 83, 85 — are other objects' entries
interleaved in the same trail, including the permit's own creation and issue.
Query the permit separately to see those; the trail is one sequence for the
whole installation, not one per record.

## Why it cannot be changed

Three independent facts, in increasing order of strength:

1. **The API offers no write.** There is one audit endpoint and it is a `GET`.
2. **The application never updates or deletes these rows.** Recording is an
   insert and nothing else.
3. **The database refuses.** Triggers on the audit table — and on the evidence
   objects, evidence attachments, and commissioning evidence tables — reject
   `UPDATE` and `DELETE` outright, whatever connects and with whatever
   privileges:

```
sqlite> update audit_entries set actor='someone-else' where id=86;
Error: audit_entries is append-only: UPDATE is not allowed

sqlite> delete from audit_entries where id=86;
Error: audit_entries is append-only: DELETE is not allowed

sqlite> update evidence_objects set sha256='0000…';
Error: evidence_objects is append-only: UPDATE is not allowed

sqlite> delete from evidence_attachments where id=1;
Error: evidence_attachments is append-only: DELETE is not allowed

sqlite> update commissioning_evidence set note='x';
Error: commissioning_evidence is append-only: UPDATE is not allowed
```

The third is the one that matters. An application bug, a careless migration, or
somebody with a database client and good intentions all hit the same wall.
Removing history requires deliberately dropping the trigger first — which leaves
its own trace in the migration history.

## What the trail does not tell you

Being honest about the limits is part of relying on it:

- **No reason field.** The trail records what changed, not why. Reasons live
  only where the workflow provides a field for them — completion notes, closing
  notes, resolution notes, permit scope. If why matters, write it there.
- **No IP address, client, or session.** Only the username. Two people sharing a
  token are indistinguishable.
- **No failed attempts.** A refused write — a 403, a 409, a rejected transition
  — leaves nothing behind. The trail is a record of what happened, not of what
  was tried.
- **No reads.** Downloading an evidence file, listing a punch list, or reading
  anything at all is not recorded. There is no access log in the product.
- **No export.** No CSV, no signed report, no bulk download. An auditor who
  wants the trail gets it by paging the API.

## Practical advice

- **Write notes as though they will be read by a stranger in two years**,
  because the notes are the only "why" the record will ever have.
- **Do not share tokens.** The trail's whole value is that `actor` means one
  person.
- **Query by `entity_id` when investigating**, and follow the linked entries —
  `created_from_incident`, `punch_item_created` — to the other objects involved.
- **Correct by adding.** A mistake is fixed by a new record with an explanatory
  note, never by an edit that would not be possible anyway.

---

[← Evidence](13-evidence.md) · [Manual index](README.md) · [Next: The web interface →](15-web-interface.md)
