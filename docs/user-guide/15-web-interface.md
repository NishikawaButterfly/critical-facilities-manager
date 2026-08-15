# 15. The web interface

[← The audit trail](14-audit-trail.md) · [Manual index](README.md) · [Next: Common workflows →](16-common-workflows.md)

The web interface is served at `/ui/`; the service root redirects there. It has
three views and one header, and it is deliberately small: it shows what the API
reports, offers only the actions the API would accept, and prints the API's
refusals word for word rather than paraphrasing them.

Before reading the three sections below, know what the page as a whole does
**not** offer: no search, no filters, no forms for creating anything, no
procedures, permits, incidents, commissioning tests, punch items, evidence, or
user administration. The only write in the entire interface is moving a
maintenance order through its states.

## The header

Present on every view.

| Element | What it does |
|---------|--------------|
| Title and version | Read from the running service — `version 0.2.0 - API at /api/v1` |
| **API token** field | Where you paste your token. Masked as a password field. |
| **Use token** | Applies it: confirms the identity and reloads the current view |
| **Forget token** | Removes it from the browser and clears the view |
| Identity line | Who the token belongs to, and whether it may write |
| Storage note | Where the token is being kept, and the risk |
| Banner | Errors that concern the whole page rather than one view |

The identity line is worth reading every time you switch tokens:

> Token belongs to Priya Sharma (priya-eng), role engineer - may run workflow
> actions, subject to its site grants.

and for a viewer:

> Token belongs to Vera Lindqvist (vera-viewer), role viewer - may read only.

with no token at all:

> No token entered. The API answers nothing but /health without one.

"subject to its site grants" is doing real work. The page can see your **role**
— it asks `/me` — but it cannot see your **grants**, because only an admin token
may read those. So it offers every action your role permits and lets the API
refuse the ones your grants do not reach. A 403 on an action the page offered is
not a bug in the page; it is the page being honest about what it cannot know.

## Asset tree

`/ui/#/tree`

![The asset tree view](../../assets/screenshots/tree.png)

The location hierarchy as a set of expandable nodes, with the assets recorded
against each one filed underneath it and a count on every branch. Each node
shows its kind as a badge, its code in bold, and its name; each asset shows its
tag, name, status, and criticality.

Everything starts expanded. Nodes are ordinary disclosure widgets, so they work
from the keyboard and read correctly to a screen reader.

Above the tree, the totals as the API counted them:

> 7 locations and 7 assets, as counted by the API.

### The asset detail panel

Clicking an asset fills the panel on the right:

```
UPS-C-01 - UPS System 1
Status          operational
Criticality     critical
Type            ups
Site            6eddaddd-e6cf-4ca8-bdf3-c9f1921587cf
Location        L1-ELEC - Electrical Room 1A
Location kind   room
Asset id        4cd1ad3e-1436-410b-a7ee-f2c86143fd8d
Created         2026-08-13 18:17:49 UTC
Updated         2026-08-13 18:26:26 UTC
```

This is the fastest way to get an asset's id for an API call: click it and copy.

The panel shows the asset record and nothing else — no orders, no incidents, no
tests, no constraints, no evidence. To find out whether this UPS is under a
redundancy constraint you must ask the API
([Operational constraints](09-operational-constraints.md#finding-constraints)).

### What it will not do

Everything except looking. No creating, editing, moving, or transitioning
assets; no adding locations; no search box; no filter. On an estate of a few
hundred assets, you scroll.

If a location's parent or an asset's location is missing from what was loaded,
the view says so in a warning rather than guessing a position for it — it will
never invent a place in the tree.

## Order board

`/ui/#/board`

![The order board view](../../assets/screenshots/board.png)

Every maintenance order as a card, in five columns by status: Draft, Scheduled,
In progress, Done, Cancelled. Each column shows its count. Each card shows the
title, type and status badges, the asset tag and name, the due date, the
description, and the completion notes when there are any.

**This is the only place in the product where you can change anything without
using the API.**

### Running an action

The buttons on a card are exactly the transitions its current status allows:

| Column | Buttons |
|--------|---------|
| Draft | Schedule, Cancel |
| Scheduled | Start, Cancel |
| In progress | Complete, Cancel |
| Done | none — *"The API allows no further transitions from done."* |
| Cancelled | none — *"The API allows no further transitions from cancelled."* |

**Schedule** and **Start** run immediately. **Complete** and **Cancel** open a
small form first, because they carry a note:

- **Complete** requires completion notes — *"The API requires this note and
  rejects the action without it."*
- **Cancel** takes an optional reason — *"Optional; the API accepts this action
  with or without a note."*

Confirm or discard. While the request is in flight the card says *"Sending
complete…"*; on success it repaints with the new status and the API's own words:

> The API moved this order to in_progress.

The board never reloads the page and never re-fetches the whole list. Only the
card that changed is updated, from the order the API returned.

### When the API refuses

The refusal is shown on the card, verbatim, with its error code. Pressing
**Start** on an order whose redundant partner is already being worked on:

> **Refused (409): conflicts with the current state**
>
> Constraint 'UPS redundancy pair' (586b0568-36a5-4f5b-9379-831ae84df916) allows
> only one of its member assets under maintenance at a time; order
> 920abd7f-320f-4d93-9a2a-e41a28d7c89e ('Quarterly UPS inspection') is already in
> progress on asset 4cd1ad3e-1436-410b-a7ee-f2c86143fd8d.
>
> `maintenance_order.mutual_exclusion_conflict`

The board quotes whatever the API sent. If your grants do not cover the site
the blocking order is on, what the API sent names the constraint and stops
there, and that is what the card shows — the board adds nothing and hides
nothing of its own.

The order stays where it was and the button stays available. Fix the cause and
press it again. [Error messages explained](17-error-messages.md) covers what
each status means.

### With a viewer token

No action buttons appear anywhere, and the board says why:

> **This token may read but not write.** Its role is not engineer or admin, so
> the API would refuse every workflow action with 403. No actions are offered
> rather than offering ones that cannot succeed.

If `/me` did not answer, the board cannot tell whether you may write and offers
nothing, saying so.

### What it will not do

No creating orders, no editing them, no filtering by asset or status, no
sorting, no search, and no view of an order's permits or evidence. Cards are
ordered by creation time within their column, oldest first. Every order in the
installation is loaded, in pages of 200 — fine for a demo campus, and something
to be aware of on a real estate.

## Audit timeline

`/ui/#/audit`

![The audit timeline view](../../assets/screenshots/audit.png)

The audit trail, newest first, fifty entries at a time. Each entry shows:

- the timestamp, in UTC
- the actor, the action as a badge, and the entity type and id
- the authorization scope — *"Authorized by scope installation."* or *"No site
  scope recorded for this action."*
- collapsible **Before** and **After** payloads, as formatted JSON

At the top, the position:

> Showing the 50 most recent of 115 entries. The trail is append-only: the API
> never writes to it from these pages and the database refuses updates and
> deletes on it outright, so every line above is permanent.

At the bottom, **Load older entries**, which appends the next fifty and becomes
**All entries loaded** at the end. Alongside it, an honest caveat:

> Paging is by offset, so an entry written while you page can shift the window
> by a line.

The order is exactly the order the API reports — the page never re-sorts.

### What it will not do

No filtering by entity, actor, action, or date; no search; no export; no link
from an entry to the record it concerns. Everything that happened is here, in
one stream, and finding one thing in it means paging or using the API's filters
([The audit trail](14-audit-trail.md#over-the-api)).

This is nonetheless the only place in the interface where procedures, permits,
incidents, commissioning tests, punch items, and evidence appear at all — as
audit lines.

## Practical notes

- **The token lives in this browser.** It survives reloads. Press **Forget
  token** when you finish on a shared machine.
- **Views do not refresh themselves.** Nothing polls; there are no live updates.
  Reload to see somebody else's changes.
- **Each view has its own address.** `#/tree`, `#/board`, `#/audit` are
  bookmarkable and shareable — the recipient still needs their own token.
- **Errors are the API's, not the page's.** When something is refused, the text
  came from the server. Quote it, including the error code, when asking for
  help.

---

[← The audit trail](14-audit-trail.md) · [Manual index](README.md) · [Next: Common workflows →](16-common-workflows.md)
