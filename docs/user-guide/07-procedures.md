# 7. Procedures

[← Maintenance orders](06-maintenance-orders.md) · [Manual index](README.md) · [Next: Work permits →](08-work-permits.md)

A procedure is a written, numbered set of steps under configuration control:
drafted, reviewed by someone else, approved, frozen, and superseded by a new
version rather than edited.

## The three kinds

| Kind | Stands for | Used for |
|------|------------|----------|
| `mop` | Method of Procedure | A specific piece of planned work — how to replace this battery string, how to transfer this UPS |
| `sop` | Standard Operating Procedure | A routine that recurs — how to escort a visitor, how to log a delivery |
| `eop` | Emergency Operating Procedure | What to do when something has gone wrong — loss of cooling, loss of utility |

The kind is chosen at creation, is carried unchanged into every later version,
and cannot be edited. The system treats all three identically; the distinction
is for the people reading them.

## The lifecycle

```
draft ──▶ approved ──▶ retired
```

| Status | What it means | May move to |
|--------|---------------|-------------|
| `draft` | Being written. Editable. Cannot be referenced by a permit or a test. | `approved` |
| `approved` | Frozen and usable. Permits and commissioning tests may reference it. | `retired` |
| `retired` | Withdrawn from use. **Terminal.** | — |

There is no route from `approved` back to `draft`. Changing an approved
procedure means starting a new version.

## Four-eyes approval

**A procedure must be approved by someone other than whoever last edited the
draft.**

```
$ curl -X POST -H "Authorization: Bearer $PRIYA_TOKEN" $API/procedures/{id}/approve
{"status":409,"error_code":"procedure.approver_must_differ",
 "detail":"A procedure must be approved by someone other than the author of its last draft edit ('priya-eng')."}

$ curl -X POST -H "Authorization: Bearer $MARCO_TOKEN" $API/procedures/{id}/approve
{ ... "status": "approved", "last_edited_by": "priya-eng", "approved_by": "marco-eng", ... }
```

The comparison is against `last_edited_by`, which is updated on every edit that
actually changes something. So if the intended approver makes even a small edit
to the draft first, they disqualify themselves and a third person must approve
it. Review, then hand back for the author to change, then approve.

Both names stay on the record: `last_edited_by` and `approved_by` are fields on
the procedure, not just entries in the audit trail.

## Versions

An approved procedure is frozen. Changing it means creating a successor:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/procedures/{id}/new-version
```

The new version:

- starts at `draft`, with `version` one higher,
- copies the title, kind, and steps of its predecessor,
- records `predecessor_id` pointing back,
- records you as `last_edited_by`, with `approved_by` empty.

The predecessor is **not** retired automatically. Until you retire it, both
versions are approved and both can be referenced by new permits — which is a
real trap. Retire the old one deliberately:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/procedures/{id}/retire
```

The chain is strictly linear — one successor per procedure, no branching:

```
{"status":409,"error_code":"procedure.successor_exists",
 "detail":"Procedure cc425dfd-b3ed-4d77-bfb4-444f15d87d38 already has a successor (version 2)."}
```

and only an approved procedure may start one:

```
{"status":409,"error_code":"procedure.not_approved",
 "detail":"Only an approved procedure can start a new version; this one is 'draft'."}
```

### Reading the whole chain

Ask any version for the chain and you get all of them, oldest first:

```
$ curl -H "Authorization: Bearer $TOKEN" $API/procedures/4fc00e75-.../versions
```

```
v1  approved  cc425dfd-b3ed-4d77-bfb4-444f15d87d38  pred=None       3 steps
v2  draft     4fc00e75-491d-4544-a55e-5ae71429719a  pred=cc425dfd…  4 steps
```

That is the demo campus's data hall access SOP: version 1 approved and in use,
version 2 drafted with an extra step about collecting the badge on the way out
and not yet reviewed.

## Creating and editing a draft

**API only.** Procedures do not appear in the web interface at all.

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"kind":"mop","title":"UPS battery string replacement",
         "steps":["Confirm the redundant UPS is carrying the load and is healthy.",
                  "Transfer UPS 1 to maintenance bypass and confirm the bypass is closed.",
                  "Isolate the battery string and verify zero volts at the terminals.",
                  "Replace the string, torque the links, and record the cell voltages.",
                  "Return to normal operation and confirm the transfer with operations."]}' \
    $API/procedures
{
  "id": "a9bfa826-957c-4449-93b4-63996ac0c511",
  "kind": "mop",
  "status": "draft",
  "title": "UPS battery string replacement",
  "steps": [ ... the five steps ... ],
  "version": 1,
  "predecessor_id": null,
  "last_edited_by": "priya-eng",
  "approved_by": null,
  "created_at": "2026-08-13T18:23:54.339860Z",
  "updated_at": "2026-08-13T18:23:54.339866Z"
}
```

| Field | Rules |
|-------|-------|
| `kind` | Required: `mop`, `sop`, or `eop` |
| `title` | Required, 1–255 characters |
| `steps` | Required; at least one step, and no step may be blank |

Steps are a plain ordered list of strings. Each is trimmed of surrounding
whitespace, and a blank one is refused:

```
{"status":422,"error_code":"procedure.step_text_required",
 "detail":"Every procedure step needs non-empty text."}
```

There is no rich text, no numbering scheme of your own, no sub-steps, no
attachments, no sign-off boxes, and no place to record a review date or an
owner. A procedure is a title and a list of sentences.

Editing works only on a draft, and replaces the whole `steps` list — there is no
per-step edit:

```
$ curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"steps":["…","…","…"]}' $API/procedures/{id}

{"status":409,"error_code":"procedure.not_editable",
 "detail":"A procedure can only be edited while draft; this one is 'approved'. Start a new version to change an approved procedure."}
```

**Permission:** every procedure operation — create, edit, approve, retire, new
version — requires an **installation-wide** grant, because a procedure belongs
to no single site. A site-scoped engineer cannot touch procedures at all. See
[Roles and permissions](03-roles-and-permissions.md#site-grants-where-you-may-write).

## Where procedures are used

Two places, and in both the procedure must be `approved`:

- **[Work permits](08-work-permits.md)** — a permit may name the procedure the
  work will follow.
- **[Commissioning tests](11-commissioning-tests.md)** — a test may name the
  procedure it was run to.

Both refuse a draft or retired one:

```
{"status":422,"error_code":"work_permit.procedure_not_approved",
 "detail":"A work permit can only reference an approved procedure; procedure 4e4172f9-… is 'draft'."}

{"status":422,"error_code":"commissioning_test.procedure_not_approved", …}
```

Both links are optional. An order can be permitted with no procedure at all, and
the system will not ask why — the seeded UPS inspection permit has none.

Retiring a procedure does **not** invalidate permits or tests already referring
to it. The historical record keeps pointing at the version that was actually
used, which is the correct behaviour: it is what was followed on the day.

## Finding procedures

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/procedures?kind=mop&status=approved"
```

| Parameter | Effect |
|-----------|--------|
| `kind` | `mop`, `sop`, or `eop` |
| `status` | `draft`, `approved`, or `retired` |

No title search. The demo campus's four procedures:

| Kind | Version | Status | Title |
|------|---------|--------|-------|
| mop | 1 | approved | CRAC fan module replacement |
| sop | 1 | approved | Data hall access and escort |
| sop | 2 | draft | Data hall access and escort |
| eop | 1 | draft | Loss of cooling emergency response |

---

[← Maintenance orders](06-maintenance-orders.md) · [Manual index](README.md) · [Next: Work permits →](08-work-permits.md)
