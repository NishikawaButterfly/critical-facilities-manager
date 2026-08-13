# Critical Facilities Manager — User Manual

**Documents version 0.2.0.**

This manual explains what you can do with Critical Facilities Manager and how
to perform each operation correctly. It assumes you have never seen the source
code and never need to.

The repository [README](../../README.md) answers a different question — what
the system is and how it is built. If you want to run the tests, read the
architecture, or deploy the service, read that instead.

## Who this is for

Facilities engineers, commissioning managers, and the administrators who set up
their access. The manual assumes you know what a UPS, a CRAC unit, a method of
procedure, and a punch list are. It does not assume you know HTTP, though every
operation is shown as an HTTP request because — as
[Known limitations](19-known-limitations.md) explains without apology — most of
this system is reachable only that way today.

## What the system does

Critical Facilities Manager keeps the operational record of a critical
facility: what equipment exists and where it sits, what work is planned and
authorized against it, who witnessed what, and what evidence backs each claim.
It enforces the rules that make that record trustworthy — an approver cannot be
the author, a witness cannot be the executor, two redundant units cannot be
worked on at the same time — and it writes an append-only audit trail that
neither the application nor a direct database connection can rewrite.

It is a system of record, not a monitoring system. Nothing here talks to a BMS,
reads a sensor, or raises an alarm. Every fact in it was put there by a person.

## How to read this manual

Chapters 1 to 3 are prerequisites; read them in order. Chapters 4 to 14 are
reference — one domain object each, readable on their own. Chapter 16 is the
one to read if you only read one: it walks complete jobs from beginning to end.

| # | Chapter | What it covers |
|---|---------|----------------|
| 1 | [Introduction and terminology](01-introduction.md) | The vocabulary the rest of the manual uses |
| 2 | [Getting started](02-getting-started.md) | Opening the interface, entering a token, moving around |
| 3 | [Roles and permissions](03-roles-and-permissions.md) | Viewer, engineer, admin — and where each may write |
| 4 | [Sites and locations](04-sites-and-locations.md) | The site → building → floor → room hierarchy |
| 5 | [Assets](05-assets.md) | Equipment records and their lifecycle |
| 6 | [Maintenance orders](06-maintenance-orders.md) | Every state, every legal transition, and what each means |
| 7 | [Procedures](07-procedures.md) | MOP, SOP, EOP; versions; four-eyes approval |
| 8 | [Work permits](08-work-permits.md) | Request, issue, close, revoke — and what a permit blocks |
| 9 | [Operational constraints](09-operational-constraints.md) | Stopping two redundant units from being worked on at once |
| 10 | [Incidents](10-incidents.md) | Unplanned events and the corrective work they raise |
| 11 | [Commissioning tests](11-commissioning-tests.md) | Witnessed acceptance tests, evidence, pass and fail |
| 12 | [Punch items](12-punch-items.md) | Defects and gaps, from open to verified closure |
| 13 | [Evidence](13-evidence.md) | Uploading files, hashes, downloading, verifying |
| 14 | [The audit trail](14-audit-trail.md) | What is recorded, how to read it, why it cannot be changed |
| 15 | [The web interface](15-web-interface.md) | The three views, one section each |
| 16 | [Common workflows](16-common-workflows.md) | Complete end-to-end runs |
| 17 | [Error messages explained](17-error-messages.md) | 401, 403, 404, 409, 413, 422, 429 in this system's terms |
| 18 | [Troubleshooting](18-troubleshooting.md) | When something does not behave as this manual says |
| 19 | [Known limitations](19-known-limitations.md) | What version 0.2.0 cannot do |
| 20 | [Glossary](20-glossary.md) | Every term, defined once |

## About the examples

Every example in this manual was executed against a running instance of version
0.2.0 seeded with the demo dataset, and every request and response shown is the
real one. The demo campus — Camellia Demo Campus, Building C, the UPS, CRAC,
chiller, PDU, and generator assets, and the four-person crew — is entirely
fictional and ships with the software.

Two things will differ on your system:

- **Identifiers.** Every `id` is a UUID generated when the record is created.
  The demo dataset generates fresh ones on every seed, so the identifiers in
  these examples will not match yours. Read them as placeholders and substitute
  your own.
- **Dates.** The demo dataset builds its due dates relative to the day you seed
  it. The examples were captured on 2026-08-13.

Commands are shown for a shell using `curl`, with the API token held in a
variable:

```
export TOKEN=<the token you were given>
export API=http://localhost:8000/api/v1
```

Adjust the host and port to match your installation.
