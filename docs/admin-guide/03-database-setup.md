# 3. Database setup

[← Configuration](02-configuration.md) · [Guide index](README.md) · [Next: Migrations →](04-migrations.md)

One setting chooses the database: `CFM_DATABASE_URL`. The application does not
create the schema itself — that belongs to [Migrations](04-migrations.md) — and
it does not create the PostgreSQL database or role for you either.

## SQLite

The default. The URL names a file:

```
CFM_DATABASE_URL=sqlite:///./cfm.db                 # relative to the working directory
CFM_DATABASE_URL=sqlite:///C:/var/cfm/cfm.db        # absolute, Windows
CFM_DATABASE_URL=sqlite:////var/lib/cfm/cfm.db      # absolute, POSIX (note four slashes)
```

The first two were executed; the POSIX form is SQLAlchemy's documented
convention for an absolute path and was not run on this machine. **Use an
absolute path.** A relative one silently follows whatever directory the service
was started from ([Installation](01-installation.md)).

The file is created on demand by the migration run, not by the application.
Nothing else in the deployment needs to exist first.

### What SQLite is suitable for

One process, one host, one facility's crew. That is the deployment story this
release is built and tested around, and within it SQLite is exact: the
authentication limiter's counters are precise
([Rate limiting in production](10-rate-limiting.md)), backups are two file
copies ([Backups](11-backups.md)), and there is no second service to keep
running.

### What SQLite costs

The database runs in **journal mode `delete`, not WAL** — verified by reading
`PRAGMA journal_mode` on a live database. One writer at a time, and any other
connection holding the write lock blocks the application.

Verified, with a second process holding `BEGIN EXCLUSIVE` on the database file:

| Request | Result |
|---------|--------|
| `POST /api/v1/maintenance-orders` | `HTTP 500` after 7.5 s |
| `GET /api/v1/health` | `HTTP 500` after 7.5 s |
| Either, after the lock was released | `200`, in 5 ms |

The server log names the cause:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
```

The response body is the plain string `Internal Server Error` — this failure
does not come back as problem-details JSON. What it means in practice:

- **Do not run a second writer against the database file.** A manual `sqlite3`
  session left in a transaction, a maintenance script, or a backup tool that
  takes a lock will make the service fail requests while it holds it.
- **Copy the file with the service stopped** ([Backups](11-backups.md)).
- **Several uvicorn workers over one SQLite file is not a supported shape.**
  Beyond the write lock, each worker keeps its own rate-limit counters
  ([Rate limiting in production](10-rate-limiting.md)).

## PostgreSQL

> The procedures in this section were **not executed** while writing this
> guide: no PostgreSQL server was available on the machine. They are derived
> from the migration scripts, the engine construction in `cfm.database`, and
> the project's CI, which runs the full migration chain and the whole test
> suite against a real `postgres:16-alpine` server on every change. Treat the
> commands as reviewed rather than demonstrated.

The URL scheme is `postgresql+psycopg` — the psycopg 3 driver, which
`pip install` brings in unconditionally:

```
CFM_DATABASE_URL=postgresql+psycopg://cfm:<password>@db.internal:5432/cfm
```

`postgres://` is **not** an accepted scheme. That mistake was executed, and it
stops the application at startup:

```
sqlalchemy.exc.NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:postgres
```

Before the first migration, create the database and a role that owns it:

```sql
CREATE ROLE cfm LOGIN PASSWORD '<password>';
CREATE DATABASE cfm OWNER cfm;
```

The role needs ordinary DDL rights on its own database, because migrations
create tables, indexes, a `plpgsql` function, and triggers. It does **not** need
superuser.

### Version requirement

Migration `0004` installs statement-level triggers through a `plpgsql`
function; the migration's own notes give PostgreSQL 11 as the floor for that
construct. CI pins PostgreSQL 16 deliberately — it is the version the sibling
deployment stack runs — so 16 is the version with evidence behind it. Anything
between is untested here.

### What PostgreSQL is suitable for

Anything that is not a single process on a single host: several uvicorn
workers, a standby, a real backup and point-in-time-recovery story, database
credentials managed separately from the application host. It is also the only
option if you want the append-only triggers to cover `TRUNCATE`, which the
PostgreSQL branch of migration `0004` adds and SQLite has no equivalent for.

### What PostgreSQL does not fix

Two limits survive the move, and both surprise people:

- **The authentication limiter stays per process.** It counts in memory, not in
  the database. Four workers means four independent counters
  ([Rate limiting in production](10-rate-limiting.md)).
- **Evidence stays on a local filesystem.** There is no object-storage backend
  in this release. If you run the application on two hosts against one
  PostgreSQL database, both hosts must see the *same* `CFM_EVIDENCE_DIR` on
  shared storage, or an evidence file uploaded through one host will answer
  `500` when a client downloads it through the other
  ([Evidence storage](07-evidence-storage.md)).

## Connections and pooling

`cfm.database.build_engine` creates the engine with `pool_pre_ping=True` for
every backend, so a connection that died while idle — a database restart, an
idle timeout on a firewall — is discovered and replaced instead of surfacing as
a failed request. Pool sizing is SQLAlchemy's default; nothing in this release
tunes it, and nothing exposes it as a setting. For SQLite,
`check_same_thread=False` is passed, because request handlers run on a thread
pool.

There is no read replica support, no statement timeout, and no connection
string sanitisation in logs — an exception raised at startup can include the
URL. See [Security hardening](15-security-hardening.md).

## Switching between the two

There is no migration path from SQLite to PostgreSQL in this release: no
export, no import, no dual-write. Alembic will build the schema on either, but
moving existing rows across is your own job. Choose before you have data worth
keeping.

---

[← Configuration](02-configuration.md) · [Guide index](README.md) · [Next: Migrations →](04-migrations.md)
