# 8. Work permits

[← Procedures](07-procedures.md) · [Manual index](README.md) · [Next: Operational constraints →](09-operational-constraints.md)

A work permit is the authorization to carry out one maintenance order. One
person requests it; a different person issues it; it is closed with a note when
the work is done, or revoked if it should not have been given.

## What a permit records

| Field | Meaning |
|-------|---------|
| `order_id` | The maintenance order it authorizes. Required. |
| `procedure_id` | The approved procedure the work will follow. Optional. |
| `scope` | Free text: what exactly is authorized, and what is not. Required. |
| `status` | `requested`, `issued`, `closed`, or `revoked`. |
| `requested_by` | Username of whoever asked. Set automatically. |
| `issued_by` | Username of whoever authorized. Set automatically on issue. |
| `completion_note` | The closing note, or the revocation reason. |

The `scope` field is the permit. Everything else is metadata; the scope is
where you write what the permit actually covers, in the words a technician on
site will read. From the demo campus:

> Replace battery string A of UPS-C-01 in Electrical Room 1A; UPS on
> maintenance bypass.

## The state machine

```
requested ──▶ issued ──┬──▶ closed
                       └──▶ revoked
```

| Status | What it means | May move to |
|--------|---------------|-------------|
| `requested` | Asked for. Not authorization to do anything. | `issued` |
| `issued` | Authorized. **Blocks completion of its order** until it leaves this state. | `closed`, `revoked` |
| `closed` | Work finished under it, with a note. **Terminal.** | — |
| `revoked` | Withdrawn. **Terminal.** | — |

### A requested permit cannot be withdrawn

There is no route out of `requested` except issuing. A permit asked for by
mistake, or superseded before anyone got to it, cannot be cancelled, deleted, or
declined:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/work-permits/{id}/revoke
{"status":409,"error_code":"work_permit.invalid_transition",
 "detail":"Work permit status cannot change from 'requested' to 'revoked'; allowed: issued."}

$ curl -X POST … /close
{"status":409,"error_code":"work_permit.invalid_transition",
 "detail":"Work permit status cannot change from 'requested' to 'closed'; allowed: issued."}
```

The only way to clear an unwanted request is to **issue it and then revoke it**
— which puts a real authorization on the record for the moment in between, and
names a second person as its issuer. That is the practical route today; the
audit trail will show both steps seconds apart. See
[Known limitations](19-known-limitations.md).

## Requesting a permit

**API only.**

```
$ curl -X POST -H "Authorization: Bearer $PRIYA_TOKEN" -H "Content-Type: application/json" \
    -d '{"order_id":"af881c0d-29e8-404c-9d1d-692b677dd31a",
         "procedure_id":"a9bfa826-957c-4449-93b4-63996ac0c511",
         "scope":"Replace battery string A of UPS-C-01 in Electrical Room 1A; UPS on maintenance bypass."}' \
    $API/work-permits
{
  "id": "58722f9c-3b33-462a-a807-8b3bdf362c5c",
  "order_id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "procedure_id": "a9bfa826-957c-4449-93b4-63996ac0c511",
  "scope": "Replace battery string A of UPS-C-01 in Electrical Room 1A; UPS on maintenance bypass.",
  "status": "requested",
  "requested_by": "priya-eng",
  "issued_by": null,
  "completion_note": null,
  "created_at": "2026-08-13T18:24:13.196732Z",
  "updated_at": "2026-08-13T18:24:13.196738Z"
}
```

Two conditions must hold:

**The order must be `scheduled` or `in_progress`.**

```
{"status":422,"error_code":"work_permit.order_not_active",
 "detail":"A work permit requires a scheduled or in_progress maintenance order; order 139e8212-… is 'draft'."}
```

Schedule the order first. A permit cannot be prepared against a draft, and
cannot be raised against work that is already done or cancelled.

**A named procedure must be approved.**

```
{"status":422,"error_code":"work_permit.procedure_not_approved",
 "detail":"A work permit can only reference an approved procedure; procedure 4e4172f9-… is 'draft'."}
```

Omit `procedure_id` entirely if the work needs no written procedure; the field
is optional and the system never asks why it is empty.

**Permission:** the site of the order's asset. `requested_by` is taken from your
token — you cannot request a permit on someone else's behalf.

## Issuing a permit

```
$ curl -X POST -H "Authorization: Bearer $MARCO_TOKEN" $API/work-permits/{id}/issue
{ ... "status": "issued", "requested_by": "priya-eng", "issued_by": "marco-eng", ... }
```

No body. **The issuer must not be the requester:**

```
$ curl -X POST -H "Authorization: Bearer $PRIYA_TOKEN" $API/work-permits/{id}/issue
{"status":409,"error_code":"work_permit.issuer_must_differ",
 "detail":"A work permit must be issued by someone other than its requester ('priya-eng')."}
```

Any engineer or admin with authority over the site may issue — there is no
separate "authorized person" register, no competency record, and no way to
restrict issuing to a named group. The control is only that it is a second
person. If your site requires a specific authorizing engineer, that is a
procedural control outside this system.

## Closing a permit

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"completion_note":"Work finished, bypass removed, UPS back on inverter; area left clear."}' \
    $API/work-permits/{id}/close
```

**The completion note is mandatory:**

```
{"status":422,"error_code":"api.validation_failed",
 "invalid_params":[{"name":"body.completion_note","reason":"Field required"}]}
```

Closing is not four-eyes: the same person who requested it may close it. Only
issuing needs a second pair of eyes.

## Revoking a permit

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"reason":"Scope superseded; the inspection was rescheduled."}' \
    $API/work-permits/{id}/revoke
{ ... "status": "revoked",
      "completion_note": "Scope superseded; the inspection was rescheduled.", ... }
```

The reason is **optional** and the body may be omitted. It is stored in
`completion_note` — the same field a closed permit uses — so read the status
alongside it. Supply a reason: a revoked permit with no explanation is a
question nobody will be able to answer later.

Use revoke when the authorization should never have been given or must be
withdrawn mid-job; use close when the work finished under it.

## What a permit actually blocks

This matters, and it is narrower than most people expect.

| Question | Answer |
|----------|--------|
| Can an order start without a permit? | **Yes.** Nothing checks. |
| Can an order start with the permit still at `requested`? | **Yes.** Nothing checks. |
| Can an order be completed with an `issued` permit outstanding? | **No.** This is the one enforced gate. |
| Can an order be cancelled with an `issued` permit outstanding? | **Yes.** Cancellation is not blocked, and the permit is left open. |
| Does a permit stop anyone else working on the asset? | **No.** That is what [constraints](09-operational-constraints.md) are for. |

The single enforced rule:

```
{"status":409,"error_code":"maintenance_order.open_permit",
 "detail":"The maintenance order still has an issued work permit open; close or revoke the permit before completing the order."}
```

So a permit is a record of authorization and a check on tidy closeout, not a
lock on the work. If your organisation's rule is that work never starts without
an issued permit, it must be enforced by your people, not by this software.

An order may have several permits; only permits in the `issued` state block
completion.

## Finding permits

**API only** — permits do not appear in the web interface.

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/work-permits?order_id=920abd7f-…"
$ curl -H "Authorization: Bearer $TOKEN" "$API/work-permits?status=issued"
```

| Parameter | Effect |
|-----------|--------|
| `order_id` | Permits for one order |
| `status` | `requested`, `issued`, `closed`, `revoked` |

`?status=requested` is the queue of permits waiting for someone to issue them,
and `?status=issued` is the list of currently live authorizations across the
whole installation. Those two queries are the closest thing to a permit board
the product has.

There is no filter by asset, by site, by requester, or by issuer.

## Evidence on permits

Files attach to a permit while it is still active — that is, `requested` or
`issued`. Once closed or revoked, it takes no more:

```
{"status":409,"error_code":"evidence.target_not_active",
 "detail":"Evidence can only be attached while the work permit is active; this one is 'closed'."}
```

Attach the signed permit scan, the isolation certificate, or the lock-out
register photograph before you close it. See [Evidence](13-evidence.md).

---

[← Procedures](07-procedures.md) · [Manual index](README.md) · [Next: Operational constraints →](09-operational-constraints.md)
