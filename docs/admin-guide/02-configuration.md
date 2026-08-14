# 2. Configuration

[← Installation](01-installation.md) · [Guide index](README.md) · [Next: Database setup →](03-database-setup.md)

Every setting is an environment variable prefixed `CFM_`. There is no
configuration file format of its own, no reload, and no admin endpoint that
changes a setting: the process reads its environment once at start and holds it
for its lifetime.

## Where settings come from

In order of precedence, highest first:

1. **The process environment.**
2. **A `.env` file in the process's working directory**, UTF-8 encoded.
3. **The built-in default.**

Verified: with a `.env` containing `CFM_EVIDENCE_MAX_MB=7`, the setting read 7;
starting the same process with `CFM_EVIDENCE_MAX_MB=99` in the environment,
it read 99. `.env.example` in the repository lists the same defaults as this
chapter and can be copied to `.env` as a starting point.

**Unknown `CFM_` variables are ignored silently.** Verified: a process started
with `CFM_NOT_A_SETTING=1` starts normally and says nothing. A typo in a
setting name is therefore invisible — the application runs with the default and
never warns. Check the effect, not the spelling.

## The settings

| Variable | Default | What it does |
|----------|---------|--------------|
| `CFM_DATABASE_URL` | `sqlite:///./cfm.db` | SQLAlchemy URL of the database. See [Database setup](03-database-setup.md). |
| `CFM_DB_AUTO_UPGRADE` | `false` | Run pending migrations at startup instead of refusing to start. See [Migrations](04-migrations.md). |
| `CFM_EVIDENCE_DIR` | `./evidence` | Root of the content-addressed evidence store. See [Evidence storage](07-evidence-storage.md). |
| `CFM_EVIDENCE_MAX_MB` | `25` | Largest single evidence upload, in mebibytes. Minimum 1. |
| `CFM_DOCS_ENABLED` | `true` | Serve `/docs`, `/redoc`, and `/openapi.json`. |
| `CFM_WEB_ENABLED` | `true` | Serve the frontend at `/ui` and redirect `/` to it. See [The web interface](08-web-interface.md). |
| `CFM_CORS_ORIGINS` | `["http://localhost:3000","http://localhost:5173"]` | JSON array of browser origins allowed to call the API. |
| `CFM_AUTH_MAX_FAILURES` | `10` | Failed authentications per source before lockout; `0` disables the limiter. |
| `CFM_AUTH_FAILURE_WINDOW_SECONDS` | `60` | Sliding window in which those failures must fall. Minimum 1. |
| `CFM_AUTH_LOCKOUT_SECONDS` | `300` | How long a locked source keeps getting `429`. Minimum 1. |
| `CFM_TRUSTED_PROXY` | `false` | Attribute failures to the rightmost `X-Forwarded-For` entry. While it is `false`, requests carrying that header share one lockout bucket. Read [Running behind a reverse proxy](09-reverse-proxy.md) before leaving it or setting it. |
| `CFM_API_PREFIX` | `/api/v1` | Path every API route hangs under. |
| `CFM_APP_NAME` | `Critical Facilities Manager API` | Title in the OpenAPI document, and the heading and browser title of the frontend. |
| `CFM_APP_VERSION` | `0.2.0` | Version string reported by `/health`, `/docs`, and `/ui/config.json`. |

The last two are cosmetic with one sharp edge, covered under
[Settings that can lie to you](#settings-that-can-lie-to-you).

## What a wrong value produces

Values that fail validation stop the process **before it binds a port**, with a
`pydantic_core._pydantic_core.ValidationError` naming the setting. This is the
tail of a real start with `CFM_API_PREFIX=/api/v1/`:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
api_prefix
  Value error, api_prefix must be a non-root path such as '/api/v1' without a trailing slash [type=value_error, input_value='/api/v1/', input_type=str]
```

Every case below was executed:

| Setting and value | Result |
|-------------------|--------|
| `CFM_API_PREFIX=/` | `Value error, api_prefix must be a non-root path such as '/api/v1' without a trailing slash` |
| `CFM_API_PREFIX=api/v1` (no leading slash) | same message |
| `CFM_API_PREFIX=/api/v1/` (trailing slash) | same message |
| `CFM_API_PREFIX=//api/v1` (doubled slash) | same message |
| `CFM_API_PREFIX=/api/v2` | Accepted. `/api/v1/health` → `404`, `/api/v2/health` → `200`, and `/ui/config.json` reports `"api_prefix":"/api/v2"` |
| `CFM_AUTH_MAX_FAILURES=-1` | `Input should be greater than or equal to 0` |
| `CFM_AUTH_MAX_FAILURES=abc` | `Input should be a valid integer, unable to parse string as an integer` |
| `CFM_AUTH_MAX_FAILURES=0` | Accepted, and the limiter is off: 20 consecutive bad tokens all answered `401`, none `429`, nothing logged |
| `CFM_AUTH_FAILURE_WINDOW_SECONDS=0` | `Input should be greater than or equal to 1` |
| `CFM_AUTH_LOCKOUT_SECONDS=0` | `Input should be greater than or equal to 1` |
| `CFM_EVIDENCE_MAX_MB=0` | `Input should be greater than or equal to 1` |
| `CFM_EVIDENCE_MAX_MB=25.5` | `Input should be a valid integer, unable to parse string as an integer` |
| `CFM_DB_AUTO_UPGRADE=maybe` | `Input should be a valid boolean, unable to interpret input` |
| `CFM_DB_AUTO_UPGRADE=yes`, `CFM_TRUSTED_PROXY=1`, `CFM_DOCS_ENABLED=off`, `CFM_WEB_ENABLED=0` | All accepted — the usual boolean spellings work |
| `CFM_CORS_ORIGINS=https://ops.example.com` | `SettingsError: error parsing value for field "cors_origins" from source "EnvSettingsSource"`. It must be a **JSON array**: `["https://ops.example.com"]` |
| `CFM_CORS_ORIGINS=[]` | Accepted; no browser origin is allowed |

Two settings are **not** validated at startup, and both fail later instead:

| Setting and value | Result |
|-------------------|--------|
| `CFM_DATABASE_URL` wrong | Fails at startup, but as a database error, not a validation error — see below |
| `CFM_EVIDENCE_DIR` wrong | Accepted at startup, even for a value that cannot be a directory (`???` was accepted). The failure arrives at the first upload, as `500` — see [Evidence storage](07-evidence-storage.md) |

### Database URLs that do not work

The error depends on how it is wrong. All four were produced by starting the
application:

```
CFM_DATABASE_URL=postgres://cfm:cfm@localhost:5432/cfm
sqlalchemy.exc.NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:postgres

CFM_DATABASE_URL=not-a-url
sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL from given URL string

CFM_DATABASE_URL=sqlite:///C:/no-such-dir/cfm.db
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unable to open database file

CFM_DATABASE_URL=postgresql+psycopg://cfm:cfm@db.invalid:5432/cfm
sqlalchemy.exc.OperationalError: (psycopg.OperationalError) failed to resolve host 'db.invalid': [Errno 11001] getaddrinfo failed
```

The first is the common one: the scheme is `postgresql+psycopg`, not
`postgres`. An unreachable but well-formed PostgreSQL host produces
`(psycopg.errors.ConnectionTimeout) connection timeout expired` after the
driver's connect timeout, which by default is long — add `?connect_timeout=5`
to the URL if you would rather a misconfigured deployment fail fast.

## Settings that can lie to you

`CFM_APP_VERSION` overrides the version string the application reports about
itself. Verified: with `CFM_APP_VERSION=99.9.9`, the settings object reports
`99.9.9`, and that value is what `/health`, the OpenAPI document, and
`/ui/config.json` publish. Nothing cross-checks it against the installed
package. Since `/health` is the natural place to confirm which version is
deployed ([Monitoring and health](14-monitoring-and-health.md)), **do not set
this variable.** The same applies to `CFM_APP_NAME`, with less at stake.

## A configuration for a real deployment

```
CFM_DATABASE_URL=postgresql+psycopg://cfm:<password>@db.internal:5432/cfm
CFM_EVIDENCE_DIR=/var/lib/cfm/evidence
CFM_EVIDENCE_MAX_MB=25
CFM_DOCS_ENABLED=false
CFM_WEB_ENABLED=true
CFM_CORS_ORIGINS=[]
CFM_AUTH_MAX_FAILURES=10
CFM_AUTH_FAILURE_WINDOW_SECONDS=60
CFM_AUTH_LOCKOUT_SECONDS=300
CFM_TRUSTED_PROXY=true
```

Both paths are absolute. `CFM_DB_AUTO_UPGRADE` is absent, so the application
refuses to start on an unmigrated database instead of migrating it — the
default, and the right one for anything with more than one process
([Migrations](04-migrations.md)). `CFM_CORS_ORIGINS` is empty because the
bundled frontend is served from the same origin and needs no CORS allowance;
add origins only for a separate browser application you actually run. The
reasoning for `CFM_TRUSTED_PROXY` is in
[Running behind a reverse proxy](09-reverse-proxy.md) — including what a
deployment behind a proxy gives up by leaving it `false`.

## Checking what a running process actually loaded

There is no endpoint that dumps the configuration, by design: it would publish
the database URL. Three things are observable from outside:

```
$ curl -s $API/health
{"status":"ok","version":"0.2.0","database":"ok"}

$ curl -s http://localhost:8000/ui/config.json
{"app_name":"Critical Facilities Manager API","app_version":"0.2.0","api_prefix":"/api/v1"}

$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
200
```

For the rest — evidence directory, rate limits, trusted proxy — read the
process's environment on the host. The single most useful check after a
configuration change is that the service came up at all: a bad value stops it
loudly, and the two that do not are the two listed above.

---

[← Installation](01-installation.md) · [Guide index](README.md) · [Next: Database setup →](03-database-setup.md)
