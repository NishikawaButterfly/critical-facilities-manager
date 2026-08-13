# 9. Operational constraints

[← Work permits](08-work-permits.md) · [Manual index](README.md) · [Next: Incidents →](10-incidents.md)

A constraint is a rule over a group of assets. It exists to stop the thing that
takes critical facilities down: two redundant units out of service at the same
time because two people planned two jobs that each looked safe on its own.

## The two kinds

| Kind | Behaviour |
|------|-----------|
| `mutual_exclusive_maintenance` | **Enforced.** Only one member asset may have a maintenance order in progress at any moment. |
| `advisory` | **Never enforced.** Free text carried in `description`, visible when you look for it and ignored otherwise. |

There are only these two. There is no constraint on time windows, on
simultaneous work by the same crew, on N+1 capacity counting, or on anything
else. An `advisory` constraint is a note attached to a group of assets — useful,
but it will not stop anybody doing anything.

## What the enforced kind actually does

Exactly one thing: it refuses to let a maintenance order move to `in_progress`
if another member asset already has an order in progress.

From the demo campus, where `UPS-C-01` and `UPS-C-02` are members of the
constraint named "UPS redundancy pair":

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/maintenance-orders/2d05a32f-…/start
{
  "title": "Request conflicts with current state",
  "status": 409,
  "detail": "Constraint 'UPS redundancy pair' (586b0568-36a5-4f5b-9379-831ae84df916) allows only one of its member assets under maintenance at a time; order af881c0d-29e8-404c-9d1d-692b677dd31a ('UPS 1 battery string replacement') is already in progress on asset 4cd1ad3e-1436-410b-a7ee-f2c86143fd8d.",
  "error_code": "maintenance_order.mutual_exclusion_conflict"
}
```

The message gives you everything needed to act: which constraint fired, which
order is holding the lock, what that order is called, and which asset it is on.
Go and find out whether it can be completed or cancelled; the moment it leaves
`in_progress`, your start succeeds.

The same refusal appears in the [order board](15-web-interface.md#order-board)
when you press **Start**, quoted from the API word for word, with the error code
underneath.

### The limits of the check

Read these carefully, because each one is a way to defeat the protection while
believing you are protected.

- **It fires only on `start`.** You may create and schedule any number of
  orders on both members. Nothing warns you at planning time that two scheduled
  jobs conflict. The collision is discovered by the second person to press
  Start, possibly with a crew already on site.
- **It counts only orders in progress.** An asset that is `under_maintenance`,
  or has an issued permit, or is decommissioned, does not hold the lock. Only
  an order at `in_progress` does.
- **It knows nothing about permits, tests, or incidents.** A commissioning test
  on the redundant unit, or a punch item being worked, is invisible to it.
- **It is pairwise across the group, not a capacity count.** A constraint over
  three chillers allows exactly one in progress, not two of three. There is no
  way to express "any two of these four".
- **A retired constraint stops enforcing immediately.** Retiring is the way to
  lift the rule, and it takes effect at once.

## Creating a constraint

**API only.** Constraints do not appear in the web interface.

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"kind":"mutual_exclusive_maintenance",
         "name":"UPS redundancy pair",
         "description":"UPS 1 and UPS 2 back each other; only one may be worked on at a time.",
         "asset_ids":["4cd1ad3e-1436-410b-a7ee-f2c86143fd8d",
                      "8338e91b-3bd7-4978-b894-4e885263009c"]}' \
    $API/constraints
```

| Field | Rules |
|-------|-------|
| `kind` | Required: `mutual_exclusive_maintenance` or `advisory` |
| `name` | Required, 1–255 characters. This is what appears in the refusal message — make it recognisable at 3 a.m. |
| `description` | Optional for the enforced kind; **required** for `advisory` |
| `asset_ids` | Required; at least one, at least **two** for the enforced kind, all distinct, all must exist |

The refusals:

```
{"status":422,"error_code":"constraint.too_few_members",
 "detail":"A mutual_exclusive_maintenance constraint needs at least two member assets."}

{"status":422,"error_code":"constraint.description_required",
 "detail":"An advisory constraint carries its guidance as free text; a description is required."}

{"status":422,"error_code":"constraint.duplicate_member",
 "detail":"Constraint member assets must be distinct."}

{"status":422,"error_code":"constraint.asset_not_found",
 "detail":"Asset nope was not found."}
```

**Permission:** an **installation-wide** grant, always. A constraint may span
sites, so it belongs to none of them. A site-scoped engineer cannot create or
retire constraints.

**Members cannot be added or removed afterwards.** A constraint is created and
retired, never edited — not its name, not its description, not its membership.
To change a group you retire the old constraint and create a new one, which
leaves both on the record with the period each was in force. That is deliberate:
a constraint that silently changed membership would make the audit trail
unreadable.

## Retiring a constraint

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/constraints/{id}/retire
{ ... "status": "retired" ... }
```

Terminal, and once only:

```
{"status":409,"error_code":"constraint.invalid_transition",
 "detail":"Constraint status cannot change from 'retired' to 'retired'; allowed: none."}
```

There is no reason field on retirement. If why it was lifted matters — and for a
redundancy constraint it does — record it somewhere the audit trail can carry
it, such as the completion notes of the order that made it obsolete.

## Finding constraints

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/constraints?asset_id=4cd1ad3e-…"
```

| Parameter | Effect |
|-----------|--------|
| `asset_id` | Every constraint this asset is a member of |
| `kind` | `mutual_exclusive_maintenance` or `advisory` |
| `status` | `active` or `retired` |

**The `asset_id` filter is the query to know.** Before planning work on
anything redundant, ask what constrains it:

```
active  mutual_exclusive_maintenance  UPS redundancy pair
        UPS 1 and UPS 2 back each other; only one may be worked on at a time.
```

Nothing does this for you. Neither the asset record nor the order board
mentions that an asset is constrained; the asset tree shows no marker; the
[Start] button gives no warning until it is pressed. Asking this question is
part of planning the job, and no part of the software will remind you. See
[Known limitations](19-known-limitations.md).

## The demo campus constraints

| Kind | Name | Members | Purpose |
|------|------|---------|---------|
| `mutual_exclusive_maintenance` | UPS redundancy pair | `UPS-C-01`, `UPS-C-02` | Enforced: one at a time |
| `advisory` | CRAC maintenance window | `CRAC-C-01`, `CRAC-C-02` | "Prefer maintaining the CRAC units outside the summer peak window." Never enforced. |

Working through the constraint in a real job is
[Workflow 1](16-common-workflows.md#workflow-1-planned-ups-maintenance-end-to-end).

---

[← Work permits](08-work-permits.md) · [Manual index](README.md) · [Next: Incidents →](10-incidents.md)
