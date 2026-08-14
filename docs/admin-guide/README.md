# Critical Facilities Manager — Administrator and Deployment Guide

**Documents version 0.2.0.**

This guide is for whoever installs the software, configures it, holds the
database credentials, and gets called when it will not start. It covers
installation, every setting, the migration policy, tokens, evidence storage,
reverse proxies, backups, restore, upgrades, monitoring, hardening, and the
failures each of those can produce.

Three documents, three questions:

| Document | Question it answers |
|----------|---------------------|
| [README](../../README.md) | What is this platform and how is it built? |
| [User Manual](../user-guide/README.md) | How do I operate it correctly? |
| This guide | How do I install, configure, secure, back up, upgrade, and run it? |

An operator needs none of what follows. If you are here to schedule maintenance
or record a commissioning test, read the manual instead.

## What this guide assumes

That you know your way around a shell, a process supervisor, and a reverse
proxy, and that you have never read this codebase. It does not assume Python
experience beyond installing a package into a virtual environment, and it never
asks you to read source to find a default.

## Chapters

| # | Chapter | What it covers |
|---|---------|----------------|
| 1 | [Installation](01-installation.md) | Supported Python, installing from a checkout, what the package ships |
| 2 | [Configuration](02-configuration.md) | Every `CFM_` setting, its default, and what a wrong value produces |
| 3 | [Database setup](03-database-setup.md) | SQLite and PostgreSQL, connection strings, which suits what |
| 4 | [Migrations](04-migrations.md) | The chain, the refuse-to-start policy, auto-upgrade, adopting an old database |
| 5 | [First run and bootstrap](05-first-run-and-bootstrap.md) | Creating the first admin, the token shown once, seeding a demo |
| 6 | [Token lifecycle](06-token-lifecycle.md) | Minting, expiry, revocation, deactivation, and the audit record |
| 7 | [Evidence storage](07-evidence-storage.md) | Layout, write-once addressing, permissions, sizing, corruption |
| 8 | [The web interface](08-web-interface.md) | Serving it, turning it off, what it needs (and does not need) |
| 9 | [Running behind a reverse proxy](09-reverse-proxy.md) | TLS, `X-Forwarded-For`, and the address the limiter counts |
| 10 | [Rate limiting in production](10-rate-limiting.md) | Per-process counters, worker counts, tuning |
| 11 | [Backups](11-backups.md) | Why the database and the evidence directory are one backup |
| 12 | [Restore and disaster recovery](12-restore-and-disaster-recovery.md) | The verified restore, and what comes back with it |
| 13 | [Upgrades](13-upgrades.md) | Back up, migrate, restart, verify — and how to go back |
| 14 | [Monitoring and health](14-monitoring-and-health.md) | What `/health` proves, what it does not, what to alert on |
| 15 | [Security hardening](15-security-hardening.md) | The checklist, in the order it matters |
| 16 | [Retention and legal hold](16-retention-and-legal-hold.md) | The open decision, stated plainly, and today's route |
| 17 | [Troubleshooting](17-troubleshooting.md) | Symptom, cause, fix — every message quoted from a real run |

Chapters 1 to 5 are the installation path, in order. The rest are reference.

## About the examples

Every command, message, and configuration value in this guide was executed
against version 0.2.0 and copied from the output. Two caveats:

- **Platform.** The runs were done on Windows with CPython 3.13.3 and SQLite
  3.49.1. Paths in captured output therefore look like Windows paths; the
  behaviour they demonstrate is not platform-specific unless a chapter says so.
- **PostgreSQL.** No PostgreSQL server was available on the machine used to
  write this guide, so the PostgreSQL procedures in
  [Database setup](03-database-setup.md) and [Migrations](04-migrations.md) are
  derived from the migration scripts and from what the project's CI proves on a
  real PostgreSQL 16 server — they are marked where they appear. Everything
  else was run.

Identifiers (UUIDs, hashes) come from a synthetic demo dataset and will differ
on your system. Token secrets are shown as placeholders; the real ones are
printed once and never appear in a document.

Shell examples assume the API token and base URL are in variables:

```
export TOKEN=<an admin token>
export API=http://localhost:8000/api/v1
```
