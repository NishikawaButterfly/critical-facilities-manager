# 20. Glossary

[← Known limitations](19-known-limitations.md) · [Manual index](README.md)

Terms as this system uses them. Where a word has a wider industry meaning, the
entry says what the software actually does with it.

---

**Actor** — The username behind the token that made a request, recorded on every
audit entry. Not a display name and not a session. Two people sharing a token
are indistinguishable. See [The audit trail](14-audit-trail.md).

**Advisory constraint** — A [constraint](#constraint) that carries guidance as
free text and is never enforced. Its description is required precisely because
the text is the whole of it.

**API token** — The bearer credential that authenticates every request. Minted
by an admin, shown exactly once, stored only as a hash. Carries the identity and
role of one user. See [Roles and permissions](03-roles-and-permissions.md).

**Append-only** — A table whose rows can only be inserted. The audit trail,
evidence objects, evidence attachments, and commissioning evidence notes are
append-only at the database level: `UPDATE` and `DELETE` are refused even from a
direct connection.

**Asset** — One piece of equipment, identified by a `tag` unique within its
[site](#site) and located at any level of the hierarchy. See
[Assets](05-assets.md).

**Asset type** — Free-text category (`ups`, `chiller`, `crac`). Not a controlled
list; whatever you type becomes the type.

**Audit entry** — One immutable line recording who changed what, when, and under
which authorization [scope](#scope). See [The audit trail](14-audit-trail.md).

**Blocking** — The highest [punch item](#punch-item) severity. Conventionally
means acceptance cannot proceed; blocks nothing in software.

**Bootstrap** — The command-line script that creates the first admin on an empty
installation, because no API call is possible without a token.

**Commissioning test** — A witnessed acceptance test on one asset. Recorded once
as passed or failed, naming an executor and a different witness. See
[Commissioning tests](11-commissioning-tests.md).

**Constraint** — A rule over a group of assets. Either
`mutual_exclusive_maintenance` (enforced when an order starts) or `advisory`
(never enforced). Created and retired, never edited. See
[Operational constraints](09-operational-constraints.md).

**Corrective** — A [maintenance order](#maintenance-order) that repairs
something. The type raised automatically from an [incident](#incident).

**Criticality** — `low`, `medium`, `high`, or `critical` on an asset. Purely
descriptive; the system behaves identically whatever it says.

**Draft** — The starting state of a maintenance order (not yet committed to) and
of a procedure (still editable). The two are unrelated.

**EOP** — Emergency Operating Procedure. One of the three
[procedure](#procedure) kinds; treated identically to the others.

**Evidence attachment** — The record of one file being attached to one record:
which file, which record, by whom, when, with what note. The auditable act. See
[Evidence](13-evidence.md).

**Evidence note** — A text note on a [commissioning test](#commissioning-test),
carrying its author and timestamp. Append-only, and only while the test is
pending. Distinct from an evidence file.

**Evidence object** — One stored file, addressed by the SHA-256 of its contents.
Identical uploads deduplicate onto the same object. Write-once; never deleted.

**Four eyes** — Shorthand for the four separation-of-duties rules: a procedure's
approver, a permit's issuer, a test's witness, and a punch item's verifier must
each differ from the person who did the other half. Compares usernames.

**Grant** — See [Site grant](#site-grant).

**Incident** — An unplanned event observed on an asset. May raise one corrective
maintenance order while still open or investigating. See
[Incidents](10-incidents.md).

**Installation-wide grant** — A [site grant](#site-grant) with no site, covering
every site plus the objects that belong to no site — procedures, constraints,
and sites themselves.

**In progress** — For an order: work is happening, and the redundancy constraint
has been checked. For a punch item: somebody has taken it on, and is now barred
from closing it.

**Location** — A node in the site → building → floor → room hierarchy. Strict
adjacency; a location cannot be moved once created. See
[Sites and locations](04-sites-and-locations.md).

**Maintenance order** — One unit of work against one asset, preventive or
corrective, moving through draft → scheduled → in progress → done, or cancelled
from any of those. See [Maintenance orders](06-maintenance-orders.md).

**MOP** — Method of Procedure. One of the three [procedure](#procedure) kinds.

**Mutual exclusive maintenance** — The enforced [constraint](#constraint) kind:
only one member asset may have an order in progress at a time. Checked when an
order starts, and nowhere else.

**Overdue** — A punch item past its due date and not yet closed. The only
date-aware filter in the product; available on punch items only.

**Preventive** — A [maintenance order](#maintenance-order) for planned upkeep.

**Procedure** — A versioned, numbered set of steps: a MOP, SOP, or EOP. Drafted,
approved by someone else, then frozen; changed only by a new version. See
[Procedures](07-procedures.md).

**Problem details** — The response shape every refusal uses, carrying `status`,
`detail`, and `error_code`. See
[Error messages explained](17-error-messages.md).

**Punch item** — One outstanding defect, omission, documentation gap, or safety
finding, against exactly one asset or one location. Started by the fixer, closed
by somebody else. Cannot be edited. See [Punch items](12-punch-items.md).

**Requested** — The starting state of a [work permit](#work-permit): asked for,
not yet authorization to do anything. The only route out is issuing.

**Retired** — Terminal state of a procedure (withdrawn from use) or a constraint
(no longer enforced). Existing references to a retired procedure are unaffected.

**Revoked** — Terminal state of a work permit (authorization withdrawn) or of an
API token (permanently rejected).

**Role** — `viewer`, `engineer`, or `admin`. Says what kind of operation a user
may perform; cannot be changed after the account is created. See
[Roles and permissions](03-roles-and-permissions.md).

**Scope** — Two unrelated meanings:
1. On a [work permit](#work-permit): the free text saying what the permit
   authorizes. The most important field on it.
2. On an [audit entry](#audit-entry): the authorization the write went through —
   `installation`, `site:<id>`, or null.

**SHA-256** — The content hash under which every evidence file is stored,
recorded on the object, in the audit entry, and in the store's path. Recomputed
before any download is served.

**Site** — The top of the location hierarchy, and the unit of write authority.
Every asset belongs to exactly one, derived from its location.

**Site grant** — A row saying where one user may write: one site and everything
under it, or the whole installation. Reads are never scoped. See
[Roles and permissions](03-roles-and-permissions.md#site-grants-where-you-may-write).

**SOP** — Standard Operating Procedure. One of the three
[procedure](#procedure) kinds.

**Terminal state** — A state with no legal transitions out. `done`, `cancelled`,
`closed`, `revoked`, `resolved`, `dismissed`, `passed`, `failed`, `retired`, and
`decommissioned` are all terminal. Nothing reopens.

**Transition** — A move between states, performed as a named operation with its
own address (`/schedule`, `/start`, `/complete`) rather than by setting a field.
Illegal moves are refused with a message naming what is allowed.

**Under maintenance** — An asset status. Never set automatically by any work;
somebody must transition the asset.

**Witness** — The user named as having observed a
[commissioning test](#commissioning-test). Must be an active user of this
installation and must not be the executor. Is never asked to confirm anything.

**Work permit** — Authorization to carry out one maintenance order, optionally
under one approved procedure. Requested by one person, issued by another. Its
only enforced effect is that an issued permit blocks completion of its order.
See [Work permits](08-work-permits.md).

---

[← Known limitations](19-known-limitations.md) · [Manual index](README.md)
