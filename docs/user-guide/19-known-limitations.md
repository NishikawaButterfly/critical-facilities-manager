# 19. Known limitations

[← Troubleshooting](18-troubleshooting.md) · [Manual index](README.md) · [Next: Glossary →](20-glossary.md)

Everything version 0.2.0 does not do, collected in one place. Nothing here is a
defect — these are boundaries, and knowing them before you commit an estate to
the system is worth more than discovering them afterwards.

## What has no interface at all

The web interface has three views and exactly one kind of write: moving a
maintenance order through its states. **Every other operation in this manual
requires the API.**

| Area | Interface | API |
|------|-----------|-----|
| Browsing the location hierarchy and assets | Asset tree | yes |
| Maintenance order board | Order board | yes |
| Schedule / start / complete / cancel an order | Order board | yes |
| Reading the audit trail | Audit timeline | yes |
| Creating or editing **anything** | — | yes |
| Locations: create, rename, delete | — | yes |
| Assets: create, edit, move, change status | — | yes |
| Maintenance orders: create, edit | — | yes |
| Procedures: everything | — | yes |
| Work permits: everything | — | yes |
| Constraints: everything | — | yes |
| Incidents: everything | — | yes |
| Commissioning tests: everything | — | yes |
| Punch items: everything | — | yes |
| Evidence: upload, list, download | — | yes |
| Users, tokens, site grants | — | yes |

An operator with no way to make HTTP requests can look at three things and move
an order between states. Everyone else needs `curl`, a REST client, the
generated `/docs` page, or something built on the API.

## Finding things

- **No search.** Nothing in the product searches anything: not assets by tag,
  not orders by title, not procedures, not evidence by filename. Finding
  `UPS-C-01` means listing the site's assets and reading.
- **No filter on `tag`.** Assets filter by location, site, status, and
  criticality, but not by the identifier your facility actually uses.
- **No subtree queries.** `parent_id` returns direct children only; there is no
  "everything under this building". Walk it a level at a time.
- **No date filters** except one. Orders and tests cannot be filtered by due or
  planned date. Punch items have `overdue`, and that is the only date-aware
  query in the product.
- **No audit date range.** "Everything that happened last Tuesday" means paging
  backwards until you reach it.
- **No sorting.** Lists come back oldest first by creation time. There is no
  sort parameter anywhere.

## Nothing tells you anything

There are no notifications of any kind — no email, no webhook, no in-page
indicator, no counters, no dashboard, no queue.

Nobody is told that:

- a permit is waiting to be issued,
- a procedure is waiting for approval,
- a punch item is overdue,
- an order's due date has passed,
- an incident is open and unattended,
- a commissioning test is pending.

Every one of these is a query somebody has to remember to run. The ones worth
putting on a routine:

```
$API/work-permits?status=requested        permits waiting to be issued
$API/work-permits?status=issued           live authorizations
$API/procedures?status=draft              procedures waiting for approval
$API/punch-items?status=open&overdue=true late defects
$API/punch-items?severity=blocking&status=open   handover blockers
$API/incidents?status=open                unattended incidents
$API/commissioning-tests?status=pending   outstanding tests
```

## No export, no reporting

No CSV, no PDF, no printable permit, no handover pack, no signed audit report,
no bulk download of evidence. Everything leaves the system one API response at a
time. An auditor who wants the trail pages the API for it.

## Where the enforcement stops

The rules that exist are enforced firmly. These plausible-sounding ones do not
exist:

- **A permit does not gate the work.** An order starts with no permit, or with
  one still at `requested`. The only enforced point is that an `issued` permit
  blocks *completion*.
- **Cancelling an order leaves its permits open.** A cancelled job can leave a
  live authorization behind indefinitely, and nothing surfaces it.
- **A requested permit cannot be withdrawn.** The only route out is to issue it
  and then revoke it — which puts a real authorization on the record for the
  moment in between.
- **The redundancy constraint fires only on `start`.** Two conflicting jobs can
  both be created and scheduled with no warning; the collision is discovered by
  whoever presses Start second, possibly with a crew already on site.
- **Constraints cannot express capacity.** One member at a time, always. There
  is no "any two of these four".
- **Asset status is never inferred.** Work in progress does not make an asset
  `under_maintenance`, and completing an order does not put it back.
- **`criticality` and `blocking` change nothing.** Both are labels. A `critical`
  asset is treated exactly like a `low` one; a `blocking` punch item blocks
  nothing in software.
- **Due dates do nothing.** No escalation, no alert, no status change.
- **Nothing checks that a corrective order was done** before its incident is
  resolved.

## Four-eyes has one blind spot

The four separation-of-duties rules compare **usernames**. One person holding
two accounts satisfies every one of them. The control is against mistakes and
casual shortcuts, not against determined circumvention.

## The commissioning witness must have an account here

A witness is named by username and must be an active user of this installation.
A client's representative, a third-party commissioning agent, or a
manufacturer's engineer cannot be recorded as the witness unless somebody gives
them an account. The usual workaround — an internal witness, with the external
one named in an evidence note — is weaker than the record appears, and worth
raising before an acceptance meeting rather than during one.

The witness is also never asked. They do not confirm, sign, or receive
notification of anything; the executor types their username.

## What cannot be corrected

| Recorded wrongly | Route |
|------------------|-------|
| A commissioning test result | None. Terminal. Run a new test. |
| A punch item's severity, category, title, description, or due date | None — there is no edit. Close it with an explanatory note and open a correct one, which needs a second person. |
| An order after completion or cancellation | None. Terminal. Raise a new order. |
| An incident after resolution or dismissal | None. Terminal. |
| An asset's status past a terminal state | None. `decommissioned` is final. |
| A procedure after approval | New version. |
| A user's role | None. Deactivate and create a new account. |
| Evidence attached to the wrong record | None. There is no delete. Attach to the right one and explain in a note. |
| An evidence attachment note | None. Fixed at upload. |
| A location's parent | None. Create the right one, move the assets, delete the wrong one. |
| An audit entry | Impossible by design, at the database level. |

## Relationships the model does not hold

- **No links between assets.** A PDU cannot be recorded as fed from a UPS; a UPS
  cannot own its battery strings. There is no parent-child, no feeds-from, no
  system grouping other than a constraint's membership.
- **No asset details.** No manufacturer, model, serial number, installation
  date, warranty, or commissioning date.
- **One incident, one asset.** An event affecting three units is three
  unconnected incidents.
- **One incident, one corrective order.** Further work is unlinked.
- **One test, one punch item.** Further defects are unlinked.
- **No evidence on incidents, assets, locations, or procedures.**
- **No assignee anywhere.** Nothing records who *should* do a piece of work —
  only who did, after they did it.
- **No attachments or metadata on procedures.** A procedure is a title and a
  list of sentences: no diagrams, no sub-steps, no review date, no owner.

## Scale and shape

- **Strict four-level hierarchy.** Site → building → floor → room, adjacent
  levels only. No campus of campuses, no zone between floor and room, no
  outdoor yard that is not a building.
- **Lists cap at 200 per page.** The web interface loads whole inventories in
  200-record pages, which is fine for a demo campus and worth thinking about for
  an estate of thousands.
- **Rate limiting is per process.** With several server workers, each keeps its
  own counters, so the effective threshold is multiplied by the worker count.
- **Evidence lives on one local filesystem.** No object-store backend yet.
- **Evidence downloads buffer the whole file** in order to verify it before
  serving, which is what bounds the upload cap.
- **No time zone support.** Everything is UTC, everywhere, with no display
  preference.

## What site-scoped reads still do not cover

Site grants now scope reads as well as writes: a token reads the sites its
grants cover and nothing else, and an out-of-scope record answers the same 404
a missing one does. Five edges are worth knowing before you rely on it.

- **A write attempt still distinguishes.** An engineer or admin who tries to
  write an out-of-scope record gets `403 auth.scope_forbidden`, while an
  invented id gets 404 — so somebody who may write can confirm that an id
  exists somewhere. The refusal is explanatory on purpose; a `viewer` token
  never reaches it, because the role gate refuses their write first.
- **Installation-wide work is invisible to site-scoped readers in the audit
  trail.** An entry is shown when the scope recorded at the time of the write
  is one of your grants, so a change made on your site by somebody holding an
  installation-wide grant reads as `installation` and does not appear in your
  view of the trail. The alternative — deciding after the fact which site an
  old entry "really" belonged to — would rewrite history every time an asset
  moved. See [The audit trail](14-audit-trail.md#what-you-see-of-the-trail).
- **Procedures and constraints are readable by everyone.** They belong to no
  site by design, and the work refers to them; only a constraint's member
  assets are filtered to your grants.
- **Identical evidence bytes are one stored object.** Content addressing
  deduplicates, so if the same file is attached on two sites, the recorded
  filename and uploader are the ones from the first upload, whichever site that
  was. You still only reach the object through a record you may read.
- **A cross-site constraint names what blocks you.** Starting an order held up
  by a `mutual_exclusive_maintenance` constraint is refused with a 409 naming
  the conflicting order and its asset, even when they sit on a site you cannot
  read. The refusal has to say what to wait for to be worth anything; if that
  disclosure matters to you, do not bind assets on different sites into one
  constraint.

## Administration is not site-scoped

Any admin manages users, tokens, and grants anywhere in the installation. Their
own grants scope their domain reads and writes, not whom they may administer,
and there is no "administer only this site" role.

## What is not recorded

- **No failed attempts.** A refused write leaves nothing behind.
- **No reads.** Downloading an evidence file or listing a punch list is not
  logged. There is no access log.
- **No IP address, client, or session** — only the username.
- **No reason on a change**, except where the workflow provides a note field.

---

[← Troubleshooting](18-troubleshooting.md) · [Manual index](README.md) · [Next: Glossary →](20-glossary.md)
