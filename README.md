# Critical Facilities Manager

This repository contains the first public release of a project developed
and tested privately. The private history stays private; from here on,
changes land in the open.


Operations platform for critical facilities (data centers and similar), built
as the operational counterpart to the validation and simulation projects in
this account. This repository currently contains the **domain core and the
operational workflows around it** (procedures, work permits, incidents,
commissioning tests, punch items, operational constraints) behind a
token-authenticated, role-guarded, versioned REST API; it is under active
development and interfaces may change without notice.

## What exists today

- **Users, API tokens, and roles** — every request except `/health` carries
  an `Authorization: Bearer <token>` header. Tokens belong to users, are
  minted by an admin (or the bootstrap script), can expire, and can be
  revoked; the secret is generated server-side, shown exactly once at
  creation, and stored only as a SHA-256 hash that is compared in constant
  time on lookup. Failed authentications are throttled per network source
  and repeat offenders are locked out (see
  [Authentication rate limiting](#authentication-rate-limiting)).
  Users have no passwords and cannot self-register — this
  is an API-first operations tool, so authentication is token-only until a
  future frontend brings interactive login. Three roles: `viewer` reads
  everything, `engineer` additionally runs every domain workflow, and
  `admin` additionally manages users and tokens. The authenticated
  username is the actor recorded in the audit trail, which means the
  separation-of-duties rules (approver ≠ author, issuer ≠ requester,
  witness ≠ executor, verifier ≠ starter) compare authenticated
  identities, not header values.
- **Site-scoped write authority** — a user's role says *what* they may do;
  their site grants say *where*. A grant covers one site's whole subtree, or
  the whole installation (`site_id` null) — the default for new users, and
  what every user existing before migration `0002` was carried to, so
  behavior did not change for existing tokens. Domain writes resolve the
  target's owning site (an order follows its asset, a permit its order, a
  punch item its asset or location) and require a covering grant. Objects
  that belong to no single site — procedures, constraints, creating or
  deleting a site itself — require an installation-wide grant, and moving an
  asset across sites requires authority over both. Every audit entry records
  the scope that covered the write (`installation` or `site:<id>`).
- **Location hierarchy** — site → building → floor → room. The adjacency rule
  is enforced: each kind must hang from exactly the kind above it (a room
  cannot be attached directly to a building). Codes are unique among siblings.
- **Asset inventory** — free-text asset type, criticality
  (`low | medium | high | critical`), a location reference at any level of the
  hierarchy, and a tag that is unique within a site (the owning site is derived
  from the location and stored with the asset). Status follows an explicit
  state machine: `planned → installed → operational ⇄ under_maintenance`, with
  `decommissioned` reachable from `operational` and `under_maintenance`.
  Status changes only happen through the transition endpoint.
- **Maintenance orders** — preventive or corrective, always against one asset,
  with a due date. Workflow: `draft → scheduled → in_progress → done`, with
  `cancelled` reachable from any non-terminal state. Completing an order
  requires completion notes. Orders are editable only while draft or scheduled.
- **Procedures (MOP/SOP/EOP)** — a titled, ordered list of non-empty steps
  with a version lifecycle: `draft → approved → retired`. Steps and title are
  editable only while draft, and approval must come from a different actor
  than the author of the last draft edit. A new version of an approved
  procedure starts as a fresh draft linked to its predecessor; the chain stays
  linear (one successor per procedure) and is queryable from any member.
- **Work permits** — authorization to carry out a maintenance order that is
  scheduled or in progress, optionally under an approved procedure (draft and
  retired procedures are rejected). Workflow:
  `requested → issued → closed | revoked`. The issuer must differ from the
  requester, closing requires a completion note, and a maintenance order
  cannot be completed while one of its permits is still issued.
- **Incidents** — an unplanned event on an asset with a severity
  (`low | medium | high | critical`) and workflow
  `open → investigating → resolved | dismissed`; resolution notes are
  required to resolve. While the incident is active, a POST action creates a
  corrective maintenance order pre-filled from it (once per incident) and
  audit-links the two records in both directions.
- **Commissioning tests** — a planned acceptance test on an asset, optionally
  under an approved procedure, with workflow `pending → passed | failed`
  (both terminal). Recording a result stores the executing user and a
  witness that must be a different, active user. Evidence is a list of structured
  text notes (note, actor, UTC timestamp) appended while the test is pending
  or together with the result, plus file evidence (see
  [Evidence storage](#evidence-storage)). A failed test offers a POST action
  that opens a pre-filled punch item (once per test), audit-linked in both
  directions the same way incidents link their corrective orders.
- **Punch items** — a defect, missing element, documentation gap, or safety
  finding (`defect | missing | documentation | safety`) scoped to exactly
  one of an asset or a location, with a severity
  (`minor | major | blocking`) and an optional due date. Workflow:
  `open → in_progress → closed`, where closing requires a note and a
  verifier different from whoever started the work. The list filters by
  scope, category, severity, state, and overdue (due date past and not
  closed).
- **Operational constraints** — a named set of member assets, created and
  retired but never edited. The `mutual_exclusive_maintenance` kind (two or
  more members) is enforced when a maintenance order starts: if another
  member asset already has an order in progress, the start is rejected with
  a 409 problem naming the constraint and the conflicting order. The
  `advisory` kind is free text attached to assets and is only surfaced,
  never enforced.
- **File evidence with content hashes** — files (photos, scans, signed
  sheets, reports) upload directly onto the commissioning test, punch item,
  maintenance order, or work permit they evidence. The content is hashed
  server-side while it streams in, stored write-once under its SHA-256, and
  re-verified on every download, so an audit can prove what was attached
  and that it has not changed (see [Evidence storage](#evidence-storage)).
- **Audit trail** — an append-only entry is written automatically for every
  create, update, delete, and state transition: who (the authenticated
  username), what (entity and action), when (UTC), and a before/after
  summary of the changed fields. Token secrets and hashes never appear in
  it. The API only ever reads this table, and the database itself refuses
  to change it: append-only is enforced by triggers on the audit and
  evidence tables, not just by application convention (see
  [Database migrations](#database-migrations)).
- **Problem-details errors** — every error body follows the RFC 9457 shape
  (see [Problem details](#problem-details)).
- **OpenAPI docs** — interactive documentation at `/docs` when enabled.
- **Migrated schema** — Alembic owns the database schema; the application
  refuses to start when the database is not at the head revision (see
  [Database migrations](#database-migrations)).

## What does not exist yet

- Sessions and password login. Authentication is API-token-only; interactive
  login (and any password or SSO story) arrives together with a future
  frontend.
- Site-scoped reads. Site grants scope writes only; every authenticated
  user still reads everything, installation-wide. A commissioning witness
  is also only a validated reference — the scope check applies to the
  executor recording the result, not to the named witness.
- Site-scoped administration. User, token, and grant management is gated by
  the admin role alone; an admin's own site grants scope their domain
  writes, not whom they can manage.
- Cross-process rate limiting. The authentication throttle counts in
  process memory; with several workers each process enforces its own
  limit (see [Authentication rate limiting](#authentication-rate-limiting)).
  A shared store — or limits at the reverse proxy — is future work for
  multi-worker deployments.
- Remote object storage. Evidence lives in a local directory behind a
  small store interface (write / verified read) so an S3-compatible
  backend can replace it without touching callers; that backend is future
  work, deliberately not built yet.
- Evidence retention and deletion. There is no delete endpoint — evidence
  is append-only like the audit trail, and the database enforces that with
  triggers — so how a real deployment handles retention periods, legal
  holds, or data-protection erasure requests is an open decision
  documented in [Evidence storage](#evidence-storage), not silently
  ignored. Until that design exists, any operational deletion takes a
  deliberate migration, on purpose.
- A frontend.

## Local development

Python 3.12 or newer is required.

```bash
python -m venv .venv
# Activate .venv with the command for your shell, then:
python -m pip install -e ".[dev]"
python -m pytest
python scripts/bootstrap_admin.py your-admin-name  # first run only; prints the admin token once
uvicorn cfm.main:app --reload
```

The bootstrap script refuses to run once any user exists; from then on,
users and tokens are managed through the API with an admin token.

Quality gates, run the same way as CI:

```bash
ruff format --check .
ruff check .
mypy
python -m pytest
```

CI additionally gates test coverage and runs the migration chain and the
full suite against a real PostgreSQL server (set `CFM_TEST_DATABASE_URL`
to a PostgreSQL URL to do the same locally); day-to-day local runs stay
on SQLite for speed.

## Configuration

Settings use the `CFM_` prefix (see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `CFM_DATABASE_URL` | `sqlite:///./cfm.db` | SQLAlchemy database URL |
| `CFM_DB_AUTO_UPGRADE` | `false` | Run pending migrations at startup instead of refusing to start |
| `CFM_DOCS_ENABLED` | `true` | Enable OpenAPI browser pages |
| `CFM_CORS_ORIGINS` | localhost origins | JSON array of allowed web origins |
| `CFM_AUTH_MAX_FAILURES` | `10` | Failed authentications per source before lockout; `0` disables the limiter |
| `CFM_AUTH_FAILURE_WINDOW_SECONDS` | `60` | Sliding window within which failures accumulate |
| `CFM_AUTH_LOCKOUT_SECONDS` | `300` | How long a locked source keeps receiving 429 |
| `CFM_TRUSTED_PROXY` | `false` | Attribute failures to the final `X-Forwarded-For` entry appended by one trusted reverse proxy |
| `CFM_EVIDENCE_DIR` | `./evidence` | Root directory of the content-addressed evidence store |
| `CFM_EVIDENCE_MAX_MB` | `25` | Upper bound, in mebibytes, on one uploaded evidence file |

For PostgreSQL, use a URL such as `postgresql+psycopg://cfm:cfm@db:5432/cfm`.

## Authentication rate limiting

Failed token authentications are throttled per network source: after
`CFM_AUTH_MAX_FAILURES` failures within a sliding window of
`CFM_AUTH_FAILURE_WINDOW_SECONDS`, the source is locked for
`CFM_AUTH_LOCKOUT_SECONDS`, and every authenticated route answers it with
`429` plus a `Retry-After` header — for valid and invalid tokens alike, and
with one constant body, so a locked-out caller learns nothing about any
token by probing further. Successful authentications are never counted and
cost only a dictionary lookup; requests without credentials are rejected
with `401` but do not count either, so unauthenticated scanning cannot lock
out a NAT gateway shared with legitimate clients. Each lockout is reported
once through the standard `logging` module. Failed attempts deliberately
write nothing to the audit trail: that table is append-only domain history,
and per-failure entries would let an unauthenticated attacker grow it
without bound.

The source is the peer address of the connection. `X-Forwarded-For` is
ignored by default because any client can write that header. Set
`CFM_TRUSTED_PROXY=true` only when exactly one reverse proxy you control
sits directly in front of the app and appends the real client address as
the final `X-Forwarded-For` entry (what nginx, traefik, and caddy do);
then that final entry becomes the source, and earlier entries remain
untrusted.

Honest scope: the counters live in process memory. With a single process —
the SQLite deployment story — the limits are exact. With several workers
or replicas each process counts on its own, so the effective per-source
limit is the configured one multiplied by the process count; that is still
a hard cap, just not a shared one, and a shared-store limiter was
deliberately not bought at the price of a database write per failure on
the authentication path. What this defends against: online token guessing
and noisy scanning from single sources — a locked source gets at most
`CFM_AUTH_MAX_FAILURES` tries per lockout period instead of thousands per
second. What it cannot defend against: offline attacks (only SHA-256
hashes of the 256-bit secrets are stored, so there is nothing usable to
steal in the first place) and attacks spread across many sources, which
per-source accounting inherently only limits per source.

## Evidence storage

Evidence files live in a content-addressed object store under
`CFM_EVIDENCE_DIR`: an object's path is the SHA-256 of its content beneath
two levels of prefix directories (`objects/ab/cd/abcd…`). The hash is
computed server-side while the upload streams in — the filename and content
type a client declares are recorded as declarations, but the hash and size
are measured, and they are what an audit can rely on. Content addressing
makes the store write-once and deduplicating: identical bytes land on the
same path exactly once, however many objects they evidence, and a stored
hash is never rewritten. Uploads are flushed and fsynced before the
database row referencing them commits, so a committed attachment never
points at bytes that could still be lost; a crash between the two steps
leaves at most an orphaned object that a retried upload reuses.

Uploading is always an attach: the file arrives as `multipart/form-data`
(field `file`, optional `note`) on the commissioning test, punch item,
maintenance order, or work permit it evidences, is allowed only while that
target can still change state, and is guarded by the same site-scope rules
as any other write on the target. The attach is the auditable act — its
audit entry records the actor, the scope, the SHA-256, the measured size,
and the declaration made in that request. One evidence object may be
attached to many targets (the bytes are stored once) but only once to any
single target. Uploads are capped at `CFM_EVIDENCE_MAX_MB`; oversized
files are rejected with `413` (`evidence.file_too_large`), and request
bodies beyond the cap plus multipart framing are rejected before parsing
can spool them to disk (`evidence.request_too_large`).

Downloads re-hash the stored bytes and compare against the recorded
SHA-256 before serving — verification happens up front, at the price of
buffering at most one capped file in memory, because a stream can only
detect corruption after wrong bytes have already been sent with a success
status. A corrupted or missing object answers `500`
(`evidence.corrupted`) instead of serving silently wrong evidence. Content
is always served as an attachment with `X-Content-Type-Options: nosniff`,
since the content type is a client declaration. Downloads are reads and
stay installation-wide, like every other read in the API.

There is no delete endpoint: evidence rows and objects are append-only,
like the audit trail whose entries reference them — and since migration
`0004` the database enforces that itself, with triggers that abort any
UPDATE or DELETE against the audit and evidence tables whatever
connection attempts it. Honest scope, twice over. First, storage is a
local directory — right for the single-process deployment story, and the
store's small interface (write / verified read) is the seam where an
S3-compatible remote backend arrives later; the triggers guard the
database rows, not the object files, whose write-once discipline the
store enforces on its own. Second, retention is an open decision for a
real deployment: retention periods, legal holds, and data-protection
erasure requests will eventually require a deliberate deletion mechanism
with its own audit story, and that design is documented future work
rather than something this codebase pretends cannot happen. The triggers
raise the cost of that future mechanism on purpose: operational deletion
now requires a deliberate migration that drops the trigger, deletes, and
recreates it — a visible, reviewable act in the migration history instead
of a quiet `DELETE` someone runs against the database. That friction is
the point.

## Database migrations

The schema is owned by [Alembic](https://alembic.sqlalchemy.org/); the
application does not create tables at startup. At boot it compares the
database's stamped revision against the newest migration and refuses to
start when they differ, printing exactly what to run — so a deployment
never serves a schema it does not understand. Setting
`CFM_DB_AUTO_UPGRADE=true` makes the application run pending migrations
itself at startup instead; that is convenient for a single process over
SQLite, but with several processes or a shared PostgreSQL database prefer
running the upgrade once as a deploy step.

The migration environment reads the database URL from the same settings as
the API (`CFM_DATABASE_URL`, `.env`, or the SQLite default), so `alembic`
commands target whatever database the application would serve.

- Fresh database: `alembic upgrade head`. The bootstrap and demo seed
  scripts do this themselves when the database is empty, which is why the
  quickstart above needs no separate migration step.
- After deploying new code: `alembic upgrade head`.
- A database created by a release that predates migrations (tables built by
  `create_all` at startup, no `alembic_version` table): verify it matches
  the current models, adopt it once with `alembic stamp 0001`, and use
  `alembic upgrade head` from then on.
- New migration during development: change the models, run
  `alembic revision --autogenerate -m "describe the change"`, and review
  the generated script — autogenerate is a starting point, not a guarantee.

The test suite enforces that a freshly migrated database matches the
models exactly and that autogenerate detects no drift at head, so a model
change without a matching migration fails CI.

One deliberate exception lives outside that comparison: migration `0004`
installs `BEFORE UPDATE` / `BEFORE DELETE` triggers (plus `TRUNCATE` on
PostgreSQL) on the append-only tables — `audit_entries`,
`commissioning_evidence`, `evidence_attachments`, `evidence_objects` — so
history cannot be rewritten by an application bug or a direct connection,
whatever role it uses. A PostgreSQL superuser, or a later migration, can
still drop a trigger; the protection is against silent mutation, not
against deliberate schema changes, which stay visible in the migration
history. Triggers are not part of the SQLAlchemy metadata, so the drift
checks above are blind to them by construction; dedicated tests assert
instead that the triggers exist at head and abort direct UPDATE and
DELETE statements on every protected table, which also catches a future
migration rebuilding one of these tables (SQLite batch mode discards
triggers with the old table) without recreating them.

## Demo dataset

Seed a small fictional campus (one site, one building, floors, rooms, seven
assets, maintenance orders in different workflow states, procedures with a
version chain, an issued work permit, incidents — one linked to a corrective
order — commissioning tests with witnessed evidence including a failed one
linked to its punch item, an overdue blocking punch item, and constraints
over the redundant UPS pair and the CRAC units) so the API is explorable
immediately:

```bash
python scripts/seed_demo.py
uvicorn cfm.main:app --reload
# then browse http://localhost:8000/docs
```

The seed also creates a synthetic crew — one admin, two engineers, one
viewer — and runs every workflow action as one of them, so the audit trail
shows real actors. One API token per crew member is minted during seeding
and printed exactly once (only hashes are stored); `cfm.demo.seed_demo`
returns the same secrets programmatically, which is how the test suite
reaches the seeded API deterministically.

All demo data is synthetic. The script refuses to run against a database that
already contains locations or users.

## API

All routes live under `/api/v1`. Except `/health`, every request must send
`Authorization: Bearer <token>`. Any authenticated user may issue GET
requests; everything else requires the engineer or admin role, and the
`/users` rows below require admin outright. Domain writes additionally
require a site grant covering the target's site — an installation-wide
grant for procedures, constraints, and sites themselves (see the
site-scoped write authority bullet above). List endpoints return
`{items, total, limit, offset}` and accept `limit` and `offset` query
parameters.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness and database check (no auth) |
| GET | `/me` | The authenticated caller's own user |
| POST | `/users` | Create a user, optionally scoped to sites via `site_ids` (admin) |
| GET | `/users` | List users (`role`, `is_active` filters; admin) |
| GET | `/users/{id}` | Fetch one user (admin) |
| POST | `/users/{id}/deactivate` | Deactivate a user; the last active admin is protected (admin) |
| POST | `/users/{id}/tokens` | Mint an API token; the secret appears once (admin) |
| GET | `/users/{id}/tokens` | List token metadata — never secrets (admin) |
| POST | `/users/{id}/tokens/{token_id}/revoke` | Revoke a token (admin) |
| GET | `/users/{id}/site-grants` | List a user's site grants (admin) |
| POST | `/users/{id}/site-grants` | Grant write authority on a site, or installation-wide with a null `site_id` (admin) |
| DELETE | `/users/{id}/site-grants/{grant_id}` | Delete a grant; authority narrows immediately (admin) |
| POST | `/locations` | Create a location |
| GET | `/locations` | List locations (`kind`, `parent_id` filters) |
| GET | `/locations/{id}` | Fetch one location |
| PATCH | `/locations/{id}` | Rename or recode a location |
| DELETE | `/locations/{id}` | Delete a childless, asset-free location no grant references |
| POST | `/assets` | Create an asset |
| GET | `/assets` | List assets (`location_id`, `site_id`, `status`, `criticality` filters) |
| GET | `/assets/{id}` | Fetch one asset |
| PATCH | `/assets/{id}` | Update asset fields (not status) |
| POST | `/assets/{id}/transition` | Change asset status (validated) |
| POST | `/maintenance-orders` | Create an order (starts as draft) |
| GET | `/maintenance-orders` | List orders (`asset_id`, `status`, `order_type` filters) |
| GET | `/maintenance-orders/{id}` | Fetch one order |
| PATCH | `/maintenance-orders/{id}` | Edit a draft or scheduled order |
| POST | `/maintenance-orders/{id}/schedule` | draft → scheduled |
| POST | `/maintenance-orders/{id}/start` | scheduled → in_progress |
| POST | `/maintenance-orders/{id}/complete` | in_progress → done (notes required) |
| POST | `/maintenance-orders/{id}/cancel` | any active state → cancelled |
| POST | `/maintenance-orders/{id}/evidence-files` | Upload and attach a file as evidence (multipart) |
| GET | `/maintenance-orders/{id}/evidence-files` | List the order's file evidence |
| POST | `/procedures` | Create a procedure (starts as draft, version 1) |
| GET | `/procedures` | List procedures (`kind`, `status` filters) |
| GET | `/procedures/{id}` | Fetch one procedure |
| GET | `/procedures/{id}/versions` | Full version chain, oldest first |
| PATCH | `/procedures/{id}` | Edit title or steps of a draft |
| POST | `/procedures/{id}/approve` | draft → approved (distinct approver) |
| POST | `/procedures/{id}/retire` | approved → retired |
| POST | `/procedures/{id}/new-version` | New linked draft of an approved procedure |
| POST | `/work-permits` | Request a permit for an active order |
| GET | `/work-permits` | List permits (`order_id`, `status` filters) |
| GET | `/work-permits/{id}` | Fetch one permit |
| POST | `/work-permits/{id}/issue` | requested → issued (distinct issuer) |
| POST | `/work-permits/{id}/close` | issued → closed (note required) |
| POST | `/work-permits/{id}/revoke` | issued → revoked |
| POST | `/work-permits/{id}/evidence-files` | Upload and attach a file as evidence (multipart) |
| GET | `/work-permits/{id}/evidence-files` | List the permit's file evidence |
| POST | `/incidents` | Report an incident (starts as open) |
| GET | `/incidents` | List incidents (`asset_id`, `severity`, `status` filters) |
| GET | `/incidents/{id}` | Fetch one incident |
| POST | `/incidents/{id}/start-investigation` | open → investigating |
| POST | `/incidents/{id}/resolve` | investigating → resolved (notes required) |
| POST | `/incidents/{id}/dismiss` | investigating → dismissed |
| POST | `/incidents/{id}/corrective-order` | Create a linked corrective order |
| POST | `/commissioning-tests` | Plan a test for an asset (starts pending) |
| GET | `/commissioning-tests` | List tests (`asset_id`, `status` filters) |
| GET | `/commissioning-tests/{id}` | Fetch one test with its evidence |
| POST | `/commissioning-tests/{id}/evidence` | Append a text evidence note (pending only) |
| POST | `/commissioning-tests/{id}/pass` | pending → passed (distinct witness) |
| POST | `/commissioning-tests/{id}/fail` | pending → failed (distinct witness) |
| POST | `/commissioning-tests/{id}/punch-item` | Open a linked punch item from a failed test |
| POST | `/commissioning-tests/{id}/evidence-files` | Upload and attach a file as evidence (multipart) |
| GET | `/commissioning-tests/{id}/evidence-files` | List the test's file evidence |
| POST | `/punch-items` | Record a punch item (asset or location scope) |
| GET | `/punch-items` | List punch items (`asset_id`, `location_id`, `category`, `severity`, `status`, `overdue` filters) |
| GET | `/punch-items/{id}` | Fetch one punch item |
| POST | `/punch-items/{id}/start` | open → in_progress |
| POST | `/punch-items/{id}/close` | in_progress → closed (distinct verifier, note required) |
| POST | `/punch-items/{id}/evidence-files` | Upload and attach a file as evidence (multipart) |
| GET | `/punch-items/{id}/evidence-files` | List the punch item's file evidence |
| POST | `/constraints` | Create a constraint over member assets |
| GET | `/constraints` | List constraints (`kind`, `status`, `asset_id` filters) |
| GET | `/constraints/{id}` | Fetch one constraint |
| POST | `/constraints/{id}/retire` | active → retired |
| GET | `/evidence-objects/{id}` | Fetch one evidence object's metadata (hash, size, declaration) |
| GET | `/evidence-objects/{id}/content` | Download the bytes, re-verified against the recorded hash |
| GET | `/audit-entries` | Read the audit trail (`entity_type`, `entity_id`, `actor` filters) |

## Problem details

Errors use `application/problem+json` with this shape:

```json
{
  "type": "https://github.com/NishikawaButterfly/critical-facilities-manager#problem-details",
  "title": "Request conflicts with current state",
  "status": 409,
  "detail": "Asset status cannot change from 'planned' to 'operational'; allowed: installed.",
  "instance": "/api/v1/assets/…/transition",
  "error_code": "asset.invalid_transition"
}
```

Validation failures (`422`, `error_code: api.validation_failed`) additionally
carry `invalid_params`, a list of `{name, reason}` objects.

Authentication failures return `401` with a `WWW-Authenticate: Bearer`
header and either `auth.credentials_required` (no usable header) or
`auth.invalid_token` — the latter is deliberately vague so responses do not
reveal whether a token exists, is revoked, or has expired. A source that
keeps failing authentication receives `429` with `auth.rate_limited`, a
`Retry-After` header, and a body that is identical whatever token was
presented (see
[Authentication rate limiting](#authentication-rate-limiting)). Role
failures return `403` with `auth.forbidden`; a write whose target site no
grant covers returns `403` with `auth.scope_forbidden`.

## License

[MIT](LICENSE)
