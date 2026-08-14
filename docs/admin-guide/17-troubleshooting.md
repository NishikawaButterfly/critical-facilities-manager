# 17. Troubleshooting

[← Retention and legal hold](16-retention-and-legal-hold.md) · [Guide index](README.md)

Every message quoted here came out of a real run. For refusals an operator
sees during normal work — `403`, `409`, a workflow that will not advance — send
them to the [user manual's error chapter](../user-guide/17-error-messages.md)
instead; this chapter is about the installation.

## Start here

```
$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/health
$ curl -s -H "Authorization: Bearer $TOKEN" $API/me
$ curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
    $API/evidence-objects/<known id>/content
```

| Health | `/me` | Evidence | Where the problem is |
|--------|-------|----------|----------------------|
| no answer | — | — | Process down or wrong address — [service will not start](#the-service-will-not-start) |
| `500` | — | — | Database unreachable — [database problems](#database-problems) |
| `200` | `401` | — | The token — [authentication](#authentication-problems) |
| `200` | `429` | — | Lockout — [authentication](#authentication-problems) |
| `200` | `200` | `500` | The evidence store — [evidence problems](#evidence-problems) |
| `200` | `200` | `200` | The installation is healthy; the problem is a specific operation |

## The service will not start

### "The configured database is empty."

```
cfm.migrate.SchemaOutOfDateError: The configured database is empty. Run 'alembic upgrade head' before starting the application, or set CFM_DB_AUTO_UPGRADE=true to let the application migrate at startup.
ERROR:    Application startup failed. Exiting.
```

**Cause.** The database has no tables at all. Usually a fresh install, or
`CFM_DATABASE_URL` pointing somewhere new — including a *relative* SQLite path
resolving against a different working directory.

**Fix.** `alembic upgrade head`, or run `scripts/bootstrap_admin.py`, which
migrates an empty database itself. If you did not expect an empty database,
check the URL before you migrate: creating a second empty database is how the
first one goes missing.

### "The database schema is at revision X but the application expects Y."

```
cfm.migrate.SchemaOutOfDateError: The database schema is at revision 0003 but the application expects 0004. Run 'alembic upgrade head' before starting the application, or set CFM_DB_AUTO_UPGRADE=true to let the application migrate at startup.
```

**Cause.** New code, un-migrated database. The normal state between step 2 and
step 3 of an upgrade.

**Fix.** `alembic upgrade head`, once, from one host
([Upgrades](13-upgrades.md)). If the message is the other way round — the
database ahead of the application — you are running old code against a migrated
database; deploy the newer release rather than downgrading the schema.

### "…has tables but no Alembic revision stamp."

```
cfm.migrate.SchemaOutOfDateError: The configured database has tables but no Alembic revision stamp. If this database was created by a release that predates migrations (tables built at startup), verify that its schema matches the current models, adopt it with 'alembic stamp 0001', and then run 'alembic upgrade head'.
```

**Cause.** A database built before migrations existed, or one whose
`alembic_version` table was dropped.

**Fix.** Follow the message, and read
[Migrations](04-migrations.md#adopting-a-database-that-predates-migrations)
first — stamping the wrong revision is worse than the original problem.

### A ValidationError naming a setting

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
api_prefix
  Value error, api_prefix must be a non-root path such as '/api/v1' without a trailing slash [type=value_error, input_value='/api/v1/', input_type=str]
```

**Cause.** A `CFM_` value the settings reject. The process stops before binding
a port.

**Fix.** The message names the setting and the value it got. Full table of
accepted and rejected values in [Configuration](02-configuration.md).

### "error parsing value for field ..."

```
SettingsError: error parsing value for field "cors_origins" from source "EnvSettingsSource"
```

**Cause.** `CFM_CORS_ORIGINS` is not JSON. A bare URL is the usual mistake.

**Fix.** Use a JSON array: `CFM_CORS_ORIGINS=["https://ops.example.com"]`, or
`[]` for none.

### The database URL is rejected

| Message | Cause | Fix |
|---------|-------|-----|
| `sqlalchemy.exc.NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:postgres` | Scheme is `postgres://` | Use `postgresql+psycopg://` |
| `sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL from given URL string` | Not a URL — a bare path, or a truncated value | Check quoting in the environment file |
| `sqlite3.OperationalError: unable to open database file` | The directory in the SQLite path does not exist, or is not writable | Create the directory; use an absolute path |
| `(psycopg.OperationalError) failed to resolve host 'db.invalid'` | DNS | Fix the host name |
| `(psycopg.errors.ConnectionTimeout) connection timeout expired` | Nothing listening, or a firewall dropping the connection | Check the server and the path to it; add `?connect_timeout=5` to fail fast |

### It starts, then exits, then starts again

A supervisor restarting a process that fails its schema check. The loop is
intended; read the log for one of the `SchemaOutOfDateError` messages above and
fix the cause rather than the loop.

## Authentication problems

### Everything answers 401

```
{"status":401,"error_code":"auth.invalid_token",
 "detail":"The API token is unknown, revoked, expired, or belongs to an inactive user."}
```

**Cause.** One of exactly four things, and the message deliberately will not say
which: unknown, revoked, expired, or the user is deactivated.

**Fix.** As an administrator you can tell them apart:

```
$ curl -H "Authorization: Bearer $ADMIN" $API/users/<user-id>          # is_active?
$ curl -H "Authorization: Bearer $ADMIN" $API/users/<user-id>/tokens   # revoked? expires_at?
```

If the token is not in that list at all, it was never minted for that user, or
it belongs to someone else. Mint a replacement
([Token lifecycle](06-token-lifecycle.md)); there is no repair.

### Everything answers 429

```
{"status":429,"error_code":"auth.rate_limited",
 "detail":"Too many failed authentication attempts from this source; retry after the
 number of seconds given in the Retry-After header."}
```

with `Retry-After` in seconds. **Cause.** The source crossed
`CFM_AUTH_MAX_FAILURES`. Valid tokens from that source are refused too — that
is the design. The service logged it once:

```
Locked authentication source 127.0.0.1 for 10 seconds after 3 failures within 60 seconds.
```

**Fix.** Find what is retrying a dead token — a script, a scheduled job, a
browser tab with a revoked token — and stop it. Wait out `Retry-After`. If the
logged source is `127.0.0.1` for everybody, the application is behind a proxy
and attributing every request to it; if it is an address that keeps changing,
`X-Forwarded-For` is being trusted when it should not be. Both are
[Running behind a reverse proxy](09-reverse-proxy.md).

`/api/v1/health` keeps answering `200` throughout — verified — which is how you
confirm the service itself is fine.

### Administrative calls refused

| Message | Meaning |
|---------|---------|
| `409 api_token.already_revoked` — "API token … is already revoked." | Somebody already revoked it. Nothing to do. |
| `422 api_token.user_inactive` — "User 'priya-eng' is inactive; inactive users cannot receive tokens." | The user was deactivated; deactivation is not reversible in this release. Create a new user. |
| `422 api_token.expiry_not_future` — "The token expiry must lie in the future." | Backdated `expires_at`, often a timezone mistake. Send an explicit UTC timestamp. |
| `409 user.last_active_admin` — "User 'rowan-admin' is the last active admin and cannot be deactivated." | Create another admin first. |

## Evidence problems

### Uploads answer 500 with no error code

```
HTTP 500
Internal Server Error
```

**Cause.** `CFM_EVIDENCE_DIR` is wrong or unusable. Storage failures escape the
problem-details handling, so the response has no `error_code` — the diagnosis is
in the service log. Both of these were produced deliberately:

```
PermissionError: [WinError 5] Access is denied: '…\readonly-evidence\incoming'
FileNotFoundError: [WinError 3] The system cannot find the path specified: '…\evidence-is-a-file.txt\incoming'
```

**Fix.** Check that the path is a directory, exists or can be created, and is
writable by the service account. Nothing validates it at startup, and
`/health` stays green throughout — verified. A full disk fails on the same path.

### A download answers 500 evidence.corrupted

```
{"status":500,"error_code":"evidence.corrupted",
 "detail":"Evidence object 826d748a-… cannot be served: Evidence object da27eacd… failed
 verification: its content hashes to 92e78d0b…. The object is corrupted and will not be served."}
```

or

```
"detail":"Evidence object 131254a0-… cannot be served: Evidence object dd210c25… is missing from the store."
```

**Cause,** in the order worth checking: `CFM_EVIDENCE_DIR` points somewhere
else; a restore brought back the database but not the evidence
([Backups](11-backups.md)); the file was edited or replaced; disk fault.

**Fix.** Restore that object's file to
`objects/<first 2>/<next 2>/<full hash>` from a backup. Verified: putting the
original bytes back turned the `500` into a `200` with no restart. The metadata
endpoint keeps answering `200` throughout, so the record of what was attached
survives even when the bytes do not.

### Uploads answer 413

| Message | Cause | Fix |
|---------|-------|-----|
| `evidence.request_too_large` — "The request body exceeds the evidence upload limit of 2097152 bytes (the configured file cap plus multipart framing)." | The whole request body is over the cap, rejected before parsing | Raise `CFM_EVIDENCE_MAX_MB`, or send a smaller file |
| `evidence.file_too_large` — "Evidence file 'between.bin' exceeds the upload limit of 1048576 bytes." | The body fitted; the file itself did not | Same |

If a `413` arrives that is *not* in this shape — plain HTML, or an nginx error
page — it came from the proxy, not the application. Its body limit must be at
least `CFM_EVIDENCE_MAX_MB` plus framing
([Running behind a reverse proxy](09-reverse-proxy.md)).

### Other evidence refusals

| Message | Meaning |
|---------|---------|
| `404 evidence.not_found` — "Evidence object … was not found." | No row with that id. A wrong id, or the wrong database. |
| `409 evidence.already_attached` — "Evidence object … (sha256 …) is already attached to this maintenance order." | The same content is already on that target. Deduplication working as intended. |
| `409 evidence.target_not_active` — "Evidence can only be attached while the commissioning test is active; this one is 'passed'." | The target reached a terminal state. Operator territory — [user manual, chapter 13](../user-guide/13-evidence.md). |

## Database problems

### Everything answers 500, including health

```
$ curl -s -w "\nHTTP %{http_code} in %{time_total}s\n" http://localhost:8000/api/v1/health
Internal Server Error
HTTP 500 in 7.532835s
```

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
```

**Cause.** Another process holds the SQLite write lock: a `sqlite3` session left
open in a transaction, a maintenance script, a backup tool that locks.

**Fix.** Find and close it. Then keep it from happening: copy the database file
with the service stopped ([Backups](11-backups.md)), and do not run a second
writer against a SQLite deployment.

### A direct UPDATE or DELETE is refused

```
IntegrityError: audit_entries is append-only: UPDATE is not allowed
IntegrityError: evidence_attachments is append-only: DELETE is not allowed
```

**Cause.** Working as designed — the triggers from migration `0004`.

**Fix.** If you genuinely need to delete, there is one supported route and it is
a migration; read [Retention and legal hold](16-retention-and-legal-hold.md)
before touching anything.

## Migration and tooling problems

### "No 'script_location' key found in configuration."

```
$ alembic upgrade head
FAILED: No 'script_location' key found in configuration.
```

**Cause.** Run outside a checkout. `alembic.ini` is not part of the installed
package.

**Fix.** Run from the repository, or use the packaged helpers
([Installation](01-installation.md#what-the-package-ships)).

### "Target database is not up to date."

```
$ alembic check
ERROR [alembic.util.messaging] Target database is not up to date.
FAILED: Target database is not up to date.
```

**Cause.** `check` compares models against a database that is behind head; it
refuses before comparing.

**Fix.** `alembic upgrade head`, then `check` again. A clean check reads
`No new upgrade operations detected.` — and says nothing about triggers.

### "table site_grants already exists"

```
$ alembic upgrade head
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) table site_grants already exists
```

**Cause.** The database was stamped at a revision it is not actually at —
almost always `alembic stamp 0001` on a database that already had the later
tables. The upgrade replays migrations whose work is already done.

**Fix.** Restore from backup and stamp the revision that matches the real
schema. This is why the stamp procedure says to verify first
([Migrations](04-migrations.md#adopting-a-database-that-predates-migrations)).

## Script problems

| Message | Cause | Fix |
|---------|-------|-----|
| `error: The database already contains users; the bootstrap only creates the first admin. Manage further users through the API with an admin token.` | The bootstrap has already run | Use an existing admin token. If none survives, see [First run and bootstrap](05-first-run-and-bootstrap.md#if-the-bootstrap-token-is-lost) |
| `error: The database already contains locations or users; refusing to seed on top of existing data.` | The demo seed will not run on a populated database | Point it at an empty one. Never at production |

## Frontend problems

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/ui/` answers `404` | `CFM_WEB_ENABLED=false` | Verified behaviour, not a fault |
| `/docs` answers `404` | `CFM_DOCS_ENABLED=false` | Same |
| The page loads but every API call fails | Served under a path prefix; the page resolves the API against the origin root | [The web interface](08-web-interface.md#serving-it-under-a-path-prefix) |
| The browser refuses a module script | Something between the browser and the application rewrote the media type | The application serves `/ui/app.js` as `text/javascript; charset=utf-8` with `nosniff` — verified. Check the proxy |
| Stale page after an upgrade | A cache holding `/ui/*` across releases | Do not cache the frontend across deployments |

## Collecting a useful report

1. The status code and the whole body — especially `error_code`.
2. The service log around the same moment, including any traceback.
3. `curl -s $API/health` output.
4. `alembic current` and `alembic check`.
5. The values of `CFM_DATABASE_URL` (**with the password removed**) and
   `CFM_EVIDENCE_DIR`.
6. Whether a proxy is in front, and the uvicorn command line.

Never include a token. If one has appeared in a log, a screenshot, or a ticket,
revoke it first and report second.

---

[← Retention and legal hold](16-retention-and-legal-hold.md) · [Guide index](README.md)
