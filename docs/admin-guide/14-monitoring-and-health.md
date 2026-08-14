# 14. Monitoring and health

[← Upgrades](13-upgrades.md) · [Guide index](README.md) · [Next: Security hardening →](15-security-hardening.md)

There is one endpoint and one log line. This chapter says exactly what each of
them means, because the gap between "health is green" and "the system works" is
wide enough to matter.

## The health endpoint

```
$ curl -s http://localhost:8000/api/v1/health
{"status":"ok","version":"0.2.0","database":"ok"}
```

It lives **under the API prefix** — `/api/v1/health`, not `/health` — so a
non-default `CFM_API_PREFIX` moves it. Verified: with
`CFM_API_PREFIX=/api/v2`, `/api/v2/health` answered `200` and `/api/v1/health`
answered `404`. Point your monitor at the prefix you configured.

It takes no token, and it keeps answering when everything else refuses.
Verified from a source in the middle of an authentication lockout: every
authenticated route returned `429`, and `/api/v1/health` returned `200`.

## What it proves

The handler runs `SELECT 1` on a pooled connection and reports the settings'
version string. So a `200` proves:

- **The process is alive** and accepting connections.
- **The database is reachable** and answering queries — a real round trip, not a
  cached flag.
- **The schema was at head when the process started.** The startup check
  refuses to serve otherwise ([Migrations](04-migrations.md)), so a process
  that is answering at all got past it.

That last one is worth understanding precisely: it is a statement about
*startup*, not about now. If someone migrates the database under a running
process, health stays green.

## What it does not prove

Each of these was verified against a running instance that reported
`{"status":"ok",…}` at the time:

| Green health, and yet | Evidence |
|-----------------------|----------|
| **Evidence uploads fail** | With `CFM_EVIDENCE_DIR` pointing at an unwritable directory, health answered `ok` and an upload answered `500` with `PermissionError` in the log |
| **Evidence directory does not exist** | The store is created at first write, not at startup; health was `ok` with no directory on disk |
| **Stored evidence is corrupt** | Health knows nothing about object integrity; corruption surfaces only on download, as `500 evidence.corrupted` |
| **The version is a fiction** | `CFM_APP_VERSION` overrides the string with no cross-check ([Configuration](02-configuration.md#settings-that-can-lie-to-you)) |
| **The disk is nearly full** | Nothing measures it |
| **The frontend is broken** | `/ui` is not touched by this check |
| **A source is locked out** | The limiter is invisible here |

The honest summary: **health is a liveness check for the process and the
database connection.** It is not a readiness check for the storage half of the
system, and the storage half is where an operator's evidence lives.

## When the database is not reachable

Verified by holding an exclusive lock on the SQLite file from another process:

```
$ curl -s -w "\nHTTP %{http_code} in %{time_total}s\n" http://localhost:8000/api/v1/health
Internal Server Error
HTTP 500 in 7.532835s
```

with this in the service log:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
```

Two things to configure around that:

- **The response is `500` with the plain body `Internal Server Error`**, not
  problem-details JSON. A monitor that parses the body will not find an
  `error_code`; check the status.
- **It can take seconds to fail.** 7.5 s here. Set the monitor's timeout above
  that, or a slow database will look like a dead process.

After the lock was released, the same request answered `200` in 5 ms.

## What to alert on

There is no metrics endpoint — verified: nothing in the source references
Prometheus, OpenTelemetry, statsd, or any instrumentation library, and the only
system route is `/health`. Everything below is built from that endpoint, the
process, the logs, and the filesystem.

| Alert | Signal | Why it matters |
|-------|--------|----------------|
| Service down | `/api/v1/health` not `200` for two consecutive checks | The only liveness signal there is |
| Health slow | Response time above ~2 s | Database contention; on SQLite, a second writer ([Database setup](03-database-setup.md)) |
| Process restarting | Supervisor restart count | A migration mismatch makes startup fail in a loop, with `SchemaOutOfDateError` in the log |
| **Evidence volume free space** | Filesystem, below a threshold you set | Nothing in the application watches it, and a full disk fails uploads with an unstructured `500` |
| **Evidence store writability** | A synthetic upload, periodically | The only way to detect the failures health cannot see |
| `500 evidence.corrupted` in access logs | Any occurrence | Storage fault, wrong directory, or a partial restore — never routine |
| Authentication lockouts | `Locked authentication source …` in the log | Someone is guessing, or a dead token is being retried ([Rate limiting in production](10-rate-limiting.md)) |
| Unhandled tracebacks | `Traceback` in the log | Every one of the storage failures in this guide arrives this way |
| Database size and audit growth | Row count or file size trend | The audit trail never shrinks; capacity planning is yours |

The two in bold are the ones that distinguish a monitored installation from one
that only looks monitored. A synthetic upload — attach a one-line file to a
scratch record, download it, compare — exercises the database, the store, the
hashing, and the verification path in a single check, and it is the closest
thing to a real readiness probe this release has.

## Logs

The application configures no logging of its own beyond one named logger.

- **`cfm.ratelimit`** emits a `WARNING` per lockout, with the source, duration,
  failure count, and window.
- **Everything else** comes from uvicorn: access lines, startup and shutdown,
  and tracebacks for unhandled exceptions.
- **Nothing is structured.** There is no JSON log format, no request id, and no
  correlation between an access line and a traceback beyond timing.

Two log-related cautions:

- **Tokens do not appear in the application's own logs**, and they must not
  reach anyone else's. A proxy configured to log request headers will capture
  `Authorization` on every request, which turns the log into a credential
  store.
- **Startup failures name the database, but did not leak its password** in any
  of the failures produced for this guide: the messages named the dialect
  (`Can't load plugin: sqlalchemy.dialects:postgres`) or the host
  (`failed to resolve host 'db.invalid'`) and no more. Treat that as an
  observation rather than a guarantee across every failure path, and keep
  service logs restricted ([Security hardening](15-security-hardening.md)).

## A minimal monitoring setup

1. HTTP check on `/api/v1/health` every 30 s, timeout 10 s, alert after two
   failures.
2. Free-space check on the evidence volume and on the database volume.
3. Log matches for `Locked authentication source`, `evidence.corrupted`,
   `SchemaOutOfDateError`, and `Traceback`.
4. A daily synthetic upload-and-download against a scratch record, alerting on
   anything but `201` then `200`.
5. A record of the expected `version` string, compared against what `/health`
   reports after each deployment.

---

[← Upgrades](13-upgrades.md) · [Guide index](README.md) · [Next: Security hardening →](15-security-hardening.md)
