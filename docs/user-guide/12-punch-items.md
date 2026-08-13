# 12. Punch items

[← Commissioning tests](11-commissioning-tests.md) · [Manual index](README.md) · [Next: Evidence →](13-evidence.md)

A punch item is one outstanding problem: a defect, a missing thing, a
documentation gap, or a safety finding. It is the snagging list, and its
defining rule is that whoever fixes a problem cannot be the one who declares it
fixed.

## What a punch item records

| Field | Meaning |
|-------|---------|
| `asset_id` **or** `location_id` | What it is against — exactly one of the two. |
| `category` | `defect`, `missing`, `documentation`, or `safety`. |
| `severity` | `minor`, `major`, or `blocking`. |
| `status` | `open`, `in_progress`, or `closed`. |
| `title` | The problem, in one line. |
| `description` | Optional detail. |
| `due_date` | Optional. The only date the product will filter on. |
| `started_by` | Who took the work on. Set when it moves to `in_progress`. |
| `verified_by` | Who closed it. Must differ from `started_by`. |
| `closing_note` | What was done and what was checked. Required to close. |

### Asset or location, never both

A punch item is scoped to exactly one thing:

```
{"status":422,"error_code":"punch_item.exactly_one_scope",
 "detail":"A punch item is scoped to exactly one of an asset or a location."}
```

The same error appears if you supply neither. Use an asset when the problem is
with a piece of equipment; use a location when it is with the space — the demo
campus's blocking item is against Data Hall 1, because a missing as-built cable
schedule belongs to the room, not to any one cabinet.

The site the item belongs to — and therefore who may work on it — follows
whichever of the two is set.

### Category and severity

| Category | Use it for |
|----------|------------|
| `defect` | Something is installed but wrong or broken |
| `missing` | Something that should be there is not |
| `documentation` | Drawings, schedules, certificates, or manuals absent or wrong |
| `safety` | A hazard, a guard, a label, a means of isolation |

| Severity | Conventional meaning |
|----------|----------------------|
| `minor` | Does not prevent acceptance |
| `major` | Should be fixed before acceptance |
| `blocking` | Acceptance cannot proceed |

Both are labels. **`blocking` blocks nothing in this software** — it does not
prevent a test passing, an order completing, or an asset going operational. It
is a signal to the people running the handover meeting, and the meeting is where
it has force.

## The state machine

```
open ──▶ in_progress ──▶ closed
```

| Status | What it means | May move to |
|--------|---------------|-------------|
| `open` | Recorded. Nobody has taken it on. | `in_progress` |
| `in_progress` | Somebody is fixing it. `started_by` is now set. | `closed` |
| `closed` | Fixed and verified by a second person. **Terminal.** | — |

**An item cannot be closed straight from `open`:**

```
{"status":409,"error_code":"punch_item.invalid_transition",
 "detail":"Punch item status cannot change from 'open' to 'closed'; allowed: in_progress."}
```

The intermediate state is not bureaucracy — it is what records who did the work,
so the system can insist that a different person verifies it. There is no way to
close a trivial item in one step, and no way to reopen a closed one; a defect
that comes back is a new punch item.

## Creating a punch item

**API only.**

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"location_id":"4814996c-436a-48a0-a37c-b4c1d5126c8c",
         "category":"documentation","severity":"blocking",
         "title":"As-built cable schedule missing for Data Hall 1",
         "description":"The handover pack lacks the final cable schedule; acceptance is blocked.",
         "due_date":"2026-08-08"}' \
    $API/punch-items
```

| Field | Rules |
|-------|-------|
| `asset_id` / `location_id` | Exactly one, and it must exist |
| `category` | Required: `defect`, `missing`, `documentation`, `safety` |
| `severity` | Required: `minor`, `major`, `blocking` |
| `title` | Required, 1–255 characters |
| `description` | Optional |
| `due_date` | Optional, `YYYY-MM-DD` |

Every item starts at `open`, with no owner.

Items can also be opened from a failed commissioning test, which links the two —
see [Commissioning tests](11-commissioning-tests.md#when-a-test-fails-opening-a-punch-item).

**A punch item cannot be edited.** There is no `PATCH`. A wrong severity,
category, title, description, or due date is permanent for the life of the item;
the only route is to close it with a note explaining the mistake and open a
correct one — which needs two people, because closing does. Get it right the
first time.

## Working an item

### Take it on

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/punch-items/{id}/start
{ ... "status": "in_progress", "started_by": "priya-eng" ... }
```

No body. This records **you** as `started_by` and, in doing so, disqualifies you
from closing it. If you press this by accident, the item now needs someone else
to close — there is no undo.

### Close it

```
$ curl -X POST -H "Authorization: Bearer $MARCO_TOKEN" -H "Content-Type: application/json" \
    -d '{"closing_note":"Head-end point remapped and alarm confirmed at the monitoring station."}' \
    $API/punch-items/{id}/close
{
  "status": "closed",
  "started_by": "priya-eng",
  "verified_by": "marco-eng",
  "closing_note": "Head-end point remapped and alarm confirmed at the monitoring station.",
  …
}
```

**The closer must not be whoever started it:**

```
{"status":409,"error_code":"punch_item.verifier_must_differ",
 "detail":"A punch item must be closed by someone other than whoever started the work ('priya-eng')."}
```

**The closing note is mandatory.** Write what was checked, not what was done —
the note is the verification record, and "fixed" verifies nothing.

Both names stay on the item permanently: `started_by` and `verified_by` are
fields, not just audit entries.

## Finding punch items

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/punch-items?status=open&overdue=true"
```

| Parameter | Effect |
|-----------|--------|
| `asset_id` | Items against one asset |
| `location_id` | Items against one location — exactly that node, not the ones under it |
| `category` | `defect`, `missing`, `documentation`, `safety` |
| `severity` | `minor`, `major`, `blocking` |
| `status` | `open`, `in_progress`, `closed` |
| `overdue` | `true` for items past their due date and not yet closed; `false` for everything else |

**`overdue` is the only date-aware filter in the product.** It compares the due
date against today in UTC and excludes closed items, so `?overdue=true` is a
genuine "what is late" list. Items with no due date are never overdue.

`?severity=blocking&status=open` — everything standing between you and
acceptance — is the query to run before a handover meeting. Nobody will run it
for you; punch items do not appear in the web interface, and nothing anywhere
displays a count.

## Evidence on punch items

Files attach while the item is `open` or `in_progress`, and stop the moment it
closes:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" \
    -F "file=@thermal-scan.jpg;type=image/jpeg" \
    -F "note=Post-repair thermal image of the terminations." \
    $API/punch-items/{id}/evidence-files
```

Attach the closeout photograph **before** closing. Afterwards:

```
{"status":409,"error_code":"evidence.target_not_active",
 "detail":"Evidence can only be attached while the punch item is active; this one is 'closed'."}
```

See [Evidence](13-evidence.md).

## The demo campus punch items

| Severity | Category | Against | Title |
|----------|----------|---------|-------|
| `major` | `defect` | `CRAC-C-02` | CRAC unit 2 condensate alarm test — opened from the failed test |
| `blocking` | `documentation` | Data Hall 1 | As-built cable schedule missing for Data Hall 1 — overdue |

---

[← Commissioning tests](11-commissioning-tests.md) · [Manual index](README.md) · [Next: Evidence →](13-evidence.md)
