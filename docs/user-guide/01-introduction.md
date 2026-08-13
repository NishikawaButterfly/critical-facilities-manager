# 1. Introduction and terminology

[← Manual index](README.md) · [Next: Getting started →](02-getting-started.md)

## What this system is for

A critical facility — a data hall, a hospital plant room, a substation — is
governed less by its equipment than by its paperwork. Who authorized this work?
Was the procedure the current one? Who witnessed the transfer test? Can we still
prove it a year from now?

Critical Facilities Manager holds that record. It tracks equipment against a
location hierarchy, plans and authorizes work on it, records witnessed test
results, files defects, stores evidence files under their content hashes, and
writes every change to an audit trail that cannot afterwards be edited.

It is deliberately narrow. It does not schedule technicians, hold spare-part
inventory, cost work, or connect to any building management system. Every fact
it holds was typed in by a person and is attributed to that person.

## The objects you will work with

The system holds ten kinds of record. They connect as follows:

```
site ─ building ─ floor ─ room            (locations)
   └── asset                              (equipment, at any level)
         ├── maintenance order ── work permit ── procedure
         ├── incident ──────────── corrective maintenance order
         ├── commissioning test ── punch item
         └── constraint (spanning several assets)

evidence files attach to: maintenance orders, work permits,
                          commissioning tests, punch items
audit entries record: every write, on every object above
```

| Term | What it means here |
|------|--------------------|
| **Location** | A node in the physical hierarchy: a site, a building, a floor, or a room. Nothing else. |
| **Site** | The top of the hierarchy. Every asset ultimately belongs to exactly one site, and site membership is what write permissions are scoped by. |
| **Asset** | One piece of equipment, identified by a tag that is unique within its site. |
| **Maintenance order** | A unit of work against one asset — either planned upkeep (*preventive*) or a repair (*corrective*). |
| **Procedure** | A written, versioned, numbered set of steps: a MOP, SOP, or EOP. Approved procedures are frozen. |
| **Work permit** | Authorization to carry out one maintenance order, optionally under one approved procedure. Requested by one person, issued by another. |
| **Constraint** | A rule over a group of assets. The enforced kind stops two of them being under maintenance at the same time. |
| **Incident** | An unplanned event observed on an asset. May raise one corrective maintenance order. |
| **Commissioning test** | A witnessed acceptance test on an asset. Passes or fails, once. A failure may open a punch item. |
| **Punch item** | A defect, omission, documentation gap, or safety finding that blocks acceptance until closed by someone other than whoever fixed it. |
| **Evidence** | A file attached to an order, permit, test, or punch item, stored under the SHA-256 of its content and verified again on the way out. |
| **Audit entry** | One immutable line recording who changed what, when, and under which authorization scope. |

Full definitions are in the [glossary](20-glossary.md).

## Three ideas that run through everything

### 1. Separation of duties ("four eyes")

Four operations in this system refuse to let one person do both halves:

| Operation | Who is blocked |
|-----------|----------------|
| Approving a procedure | Whoever last edited the draft |
| Issuing a work permit | Whoever requested it |
| Witnessing a commissioning test | Whoever executed it |
| Closing a punch item | Whoever started the work on it |

These are hard refusals, not warnings. The system compares usernames, so a
single person holding two accounts defeats it; the rule is a control against
mistakes and casual shortcuts, not against a determined fraud.

### 2. States, not fields

Nothing important is changed by editing a field. A maintenance order does not
have its status set to `done` — it is *completed*, through an operation that
checks the current state, checks the rules around it, requires completion
notes, and writes an audit entry. Every domain object has a small state machine
and a fixed table of legal transitions; anything not in the table is refused
with a message naming what *is* allowed.

The state machines are given in full in each object's chapter.

### 3. The record is append-only

The audit trail, evidence objects, evidence attachments, and commissioning
evidence notes are append-only at the database level, not merely by convention.
Attempting to update or delete a row in those tables fails even from a direct
database connection:

```
sqlite> update audit_entries set actor='someone-else' where id=86;
Error: audit_entries is append-only: UPDATE is not allowed
```

Corrections are made by adding records, never by rewriting them. See
[The audit trail](14-audit-trail.md).

## The interface, honestly

Version 0.2.0 ships two ways in:

- **A web page** at `/ui/`, with three read-oriented views and exactly one kind
  of write: moving a maintenance order through its states. It is described in
  [The web interface](15-web-interface.md).
- **An HTTP API** at `/api/v1`, which is where everything else lives —
  creating assets, approving procedures, issuing permits, recording test
  results, uploading evidence, managing users.

This manual documents both, and says which is which every time. Where an
operation has no button, the chapter says so and gives the request.
[Known limitations](19-known-limitations.md) collects all of them in one list.

## Time and identity

- **All timestamps are UTC**, in ISO 8601 with a `Z` suffix
  (`2026-08-13T18:26:26.025488Z`). The web interface labels them `UTC`
  explicitly. There is no local-time display and no time zone setting.
- **You are your username.** The audit trail records the username behind the
  token that made the request — `priya-eng`, not "Priya Sharma" and not a
  session. Usernames are lowercase slugs and are never reused, because users
  are deactivated rather than deleted.

---

[← Manual index](README.md) · [Next: Getting started →](02-getting-started.md)
