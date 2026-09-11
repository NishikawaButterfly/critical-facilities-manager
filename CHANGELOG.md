# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - TBD

### Changed

- Reads are scoped by site grants. Every list and detail read on locations,
  assets, maintenance orders, work permits, incidents, commissioning tests,
  punch items and the evidence attached to them now returns only what the
  token's grants cover, and the audit trail shows only entries recorded under
  one of the token's grants. An entity outside the caller's grants answers
  exactly the 404 a missing one does, with the same error code and wording,
  and list totals count the filtered set. An installation-wide grant reads
  every site; the role does not widen reads, so an admin whose grants cover
  one site reads that site. Compatibility: a token holding no grants now reads
  no site-owned record, and a site-scoped token receives 404 for another
  site's records. (#28)
- A refusal no longer prints what a read would hide. When starting a
  maintenance order is blocked by a `mutual_exclusive_maintenance` constraint
  whose conflicting order lies outside the caller's read scope, the 409 names
  the constraint and states that another member asset already has an order in
  progress, without the conflicting order's id, title or asset. The status and
  error code are the same in both cases, and when the conflicting work is
  readable the refusal still names the order's id, title and asset. (#50)

### Security

- The authentication lockout can no longer be evaded by sending a
  forwarded-for header. Requests carrying an `X-Forwarded-For` value that no
  trusted proxy vouches for are counted into one shared bucket instead of
  partitioning the limiter by attacker-chosen addresses. (#38)

### Added

- A user manual (`docs/user-guide/`) and an administrator and deployment guide
  (`docs/admin-guide/`), both written by operating the software; the few
  procedures that were reviewed rather than demonstrated are marked as such.
- The three web views run in a headless browser in CI.

## [0.2.0] - 2026-08-13

Seven reviewed pull requests turned an API-first prototype into a platform that
can be deployed, audited and operated by more than one team; every change
arrived with a failing test written first and stated limitations.

### Added

- The schema is under Alembic migrations. The baseline is proven equal to the
  models and autogenerate emptiness is asserted; the application refuses to
  start against an out-of-date database by default, with self-upgrade one flag
  away, because several processes over one PostgreSQL database would otherwise
  race each other. Existing deployments stamp once. (#5)
- Write authority is scoped to sites. A token's role applies where a grant
  covers the object's site, resolved by walking the location tree; grants are
  managed through an admin API and recorded in the audit trail alongside every
  action. Existing users were carried to installation-wide grants by
  migration, so no deployed token changed behaviour. (#6)
- Evidence is verifiable. Files attach to commissioning tests, punch items,
  orders and permits through a content-addressed, write-once store: the path
  is the SHA-256, identical content deduplicates, and downloads verify the
  whole object before serving a byte. The attach is the auditable act,
  recorded with hash, size and the client's declared filename and type,
  labelled as declarations. (#9)
- CI runs against PostgreSQL 16. A service container runs the migration chain
  and the entire suite against a real server, which is where the append-only
  triggers are proven to fire; coverage is gated at 96%. (#13)
- A web interface: the asset tree, the order board with the state actions the
  token can actually perform, and the audit trail as a timeline, in plain
  HTML, CSS and ES modules served by the application itself with no build
  step and no runtime dependency. (#15)

### Changed

- `create_all` at startup is gone from all three places it lived; the schema
  now comes from migrations only. (#5)

### Security

- Authentication resists brute force. Failed bearer lookups are counted per
  source, with a lockout after ten failures in a minute; the source is the
  socket peer, with `X-Forwarded-For` meant to count only behind an
  explicitly trusted proxy, rightmost entry only; a default deployment did not
  enforce this, which 0.3.0 corrects (#38). Refusals are indistinguishable
  whether the token exists or not, successful authentication is never counted,
  and failures never write audit rows. (#7)
- The audit trail cannot be rewritten. Triggers on both SQLite and PostgreSQL
  refuse `UPDATE`, `DELETE` and `TRUNCATE` on the audit and evidence tables for
  whichever role is connected, including the application's own bugs; deletion
  for retention requires a deliberate migration. (#11)

## [0.1.0] - 2026-08-02

### Added

- First public release of a project developed and tested privately: a
  location hierarchy with assets and validated status transitions;
  maintenance orders whose start is gated by mutual-exclusion constraints and
  whose completion is gated by open work permits; versioned MOP/SOP/EOP
  procedures with four-eyes approval; incidents that open corrective orders;
  commissioning tests with witnessed evidence that open punch items on
  failure; bearer-token authentication with viewer, engineer and admin roles;
  and an append-only audit trail behind every write. 237 tests; all sample
  data synthetic. Rate limiting, per-site role scoping and database
  migrations were documented as future work.

[Unreleased]: https://github.com/NishikawaButterfly/critical-facilities-manager/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/NishikawaButterfly/critical-facilities-manager/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/NishikawaButterfly/critical-facilities-manager/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/NishikawaButterfly/critical-facilities-manager/releases/tag/v0.1.0
