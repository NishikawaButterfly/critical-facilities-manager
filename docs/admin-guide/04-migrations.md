# 4. Migrations

[← Database setup](03-database-setup.md) · [Guide index](README.md) · [Next: First run and bootstrap →](05-first-run-and-bootstrap.md)

Alembic owns the schema. The application never creates tables; it checks the
database's stamped revision at startup and, by default, refuses to serve a
database that is not at the newest one.

## The chain

Four revisions, linear, no branches:

```
$ alembic history
0003 -> 0004 (head), Database-level append-only enforcement for audit and evidence tables.
0002 -> 0003, Verifiable file evidence.
0001 -> 0002, Site-scoped write authority.
<base> -> 0001, Schema baseline.
```

`alembic heads` prints `0004 (head)`. Every command below reads
`CFM_DATABASE_URL` (or `.env`, or the SQLite default) through the same settings
object the application uses, so `alembic` always targets the database the
application would serve. There is deliberately no `sqlalchemy.url` in
`alembic.ini`.

Run these from a checkout — the `alembic` CLI needs `alembic.ini`, which the
wheel does not contain ([Installation](01-installation.md)).

## Migrating a fresh database

```
$ alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, Schema baseline.
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, Site-scoped write authority.
INFO  [alembic.runtime.migration] Running upgrade 0002 -> 0003, Verifiable file evidence.
INFO  [alembic.runtime.migration] Running upgrade 0003 -> 0004, Database-level append-only enforcement for audit and evidence tables.

$ alembic current
0004 (head)
```

For SQLite the file is created by this command if it does not exist.

## A failed run changes nothing

Every run of the chain is one transaction, on SQLite as on PostgreSQL. If
anything in it fails, nothing it did is kept: the schema and the stamp are as
they were before the command, and `alembic current` still prints the revision
the run started from. There is no half-applied revision to repair first; fix
the cause and run the command again. `downgrade` and `stamp` run the same way.

PostgreSQL does this by itself. On SQLite it is the migration environment's
doing. Left to its defaults, Alembic treats SQLite DDL as non-transactional and
commits after each revision, and Python's `sqlite3` driver opens a transaction
only before a statement that changes data, so a `CREATE TABLE` issued earlier
would be permanent at once. The environment declares the run transactional, and
every command that opens the database says so in its second line of output:

```
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
```

It also opens its SQLite connection with the driver's own transaction handling
switched off, and starts each transaction with `BEGIN IMMEDIATE`. That
connection belongs to the migrations alone; the application's connections keep
the driver's default.

Verified on SQLite, with a revision added after `0004` that adds a column,
creates a table and then fails. Against a database at `0004`, one written by
the 0.3.0 release among them, the failed upgrade left the file byte-for-byte as
it was, still stamped `0004`. Against an empty database, it left no tables and
no stamp. Up to 0.3.0, the first run kept the column and the table, and the
second left the database stamped `0004`. The test suite asserts both cases,
plus a failure inside a batch rebuild and one while the stamp is being written,
on SQLite and, in CI, on PostgreSQL 16.

### The write lock

`BEGIN IMMEDIATE` takes the database's write lock before the run reads
anything, and the run holds it until it commits. Two upgrades of one file
started at the same moment therefore run one after the other. Executed three
times: each time the first applied all four revisions and the second found
nothing left to do. Up to 0.3.0, the second failed with
`table audit_entries already exists` all three times.

Every command that opens the database takes the lock, `current` and `check`
included. If another connection holds it, the command waits five seconds, the
`sqlite3` driver's default, and then stops, having changed nothing. Executed
with a second process holding `BEGIN IMMEDIATE` on a database at `0003`, which
was unchanged afterwards:

```
$ alembic upgrade head
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
```

With the service stopped, as the [upgrade procedure](13-upgrades.md) has it,
nothing else holds the lock. The lock does not make `CFM_DB_AUTO_UPGRADE` safe
for several processes: a process queued behind a migration that takes longer
than five seconds fails to start, and PostgreSQL runs take no such lock.

### Handing the migrations a connection

The environment also accepts an open SQLAlchemy connection in
`config.attributes["connection"]`, for a caller that already holds one. It
cannot reconfigure a connection it did not open, so it checks it before the
first revision, and refuses one on which the run could not be one
transaction: an SQLite connection left to the driver's own transaction
handling, which is what `cfm.database.build_engine` gives, or a connection in
`AUTOCOMMIT` on either database. Executed:

```
$ python -c "
from alembic import command
from cfm.database import build_engine
from cfm.migrate import build_alembic_config
url = 'sqlite:///./handed.db'
with build_engine(url).connect() as connection:
    config = build_alembic_config(url)
    config.attributes['connection'] = connection
    command.upgrade(config, 'head')
"
alembic.util.exc.CommandError: Refusing to migrate over a connection that is not inside a database transaction: each statement would be committed as it ran, and a run that failed part-way would leave the schema half-upgraded. Hand over a connection whose transactions are real (on SQLite, set isolation_level = None on the sqlite3 connection and emit BEGIN from the engine's 'begin' event, or connect with autocommit=False; on any database, do not use AUTOCOMMIT), or pass config.attributes['database_url'] instead and let the migrations open a connection of their own.
```

`handed.db` was left empty. An SQLite connection the run accepts comes from an
engine that sets `isolation_level = None` on each `sqlite3` connection and
emits `BEGIN` from its `begin` event, the recipe in SQLAlchemy's SQLite
documentation, or from one created with `connect_args={"autocommit": False}`
on Python 3.12 or later. The helpers in `cfm.migrate` pass a URL and never need
any of this.

## The refuse-to-start policy

With `CFM_DB_AUTO_UPGRADE` unset or `false` — the default — the application
compares the stamped revision against head during startup and stops if they
differ. All three refusals below are real startup output.

**An empty database:**

```
cfm.migrate.SchemaOutOfDateError: The configured database is empty. Run 'alembic upgrade head' before starting the application, or set CFM_DB_AUTO_UPGRADE=true to let the application migrate at startup.
ERROR:    Application startup failed. Exiting.
```

**A database behind head** (this one was stamped `0003`):

```
cfm.migrate.SchemaOutOfDateError: The database schema is at revision 0003 but the application expects 0004. Run 'alembic upgrade head' before starting the application, or set CFM_DB_AUTO_UPGRADE=true to let the application migrate at startup.
```

**A database with tables but no Alembic stamp:**

```
cfm.migrate.SchemaOutOfDateError: The configured database has tables but no Alembic revision stamp. If this database was created by a release that predates migrations (tables built at startup), verify that its schema matches the current models, adopt it with 'alembic stamp 0001', and then run 'alembic upgrade head'.
```

In every case the process exits non-zero without binding a port. A supervisor
that restarts on failure will loop; that is the intended shape of the failure,
because the alternative is a process serving a schema it does not understand.

### Why this is the default

With one process, migrating at startup is harmless. With several — uvicorn
workers, a rolling deploy, two hosts behind a load balancer — every process
starts at once and every one of them would try to migrate the same database
concurrently. Alembic has no coordination for that. Making the upgrade an
explicit deploy step, run once, before any process starts, removes the race
instead of hoping to win it.

The refusal also catches the other direction: an old binary pointed at a
database that a newer release already migrated. It stops rather than writing
rows a schema it does not know about.

### The `CFM_DB_AUTO_UPGRADE` opt-in

`CFM_DB_AUTO_UPGRADE=true` makes the application run `upgrade head` itself
during startup. Verified: pointed at a database stamped `0003`, the process
started and answered `{"status":"ok","version":"0.2.0","database":"ok"}`, with
the database at `0004` afterwards.

Safe when **all** of these hold:

- exactly one process starts against the database (one worker, no replicas,
  no rolling restart overlap);
- you have a current backup, because the migration runs unattended;
- a failed migration taking the service down with it is acceptable.

That is the SQLite single-process shape and little else. For anything larger,
leave it off and run the upgrade as a deploy step ([Upgrades](13-upgrades.md)).

## Adopting a database that predates migrations

For a database whose tables were built by an old release's `create_all`, with
no `alembic_version` table. The procedure — and the trap in it — were both
executed.

The procedure works when the schema really is the `0001` schema:

```
$ alembic stamp 0001
INFO  [alembic.runtime.migration] Running stamp_revision  -> 0001

$ alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, Site-scoped write authority.
INFO  [alembic.runtime.migration] Running upgrade 0002 -> 0003, Verifiable file evidence.
INFO  [alembic.runtime.migration] Running upgrade 0003 -> 0004, Database-level append-only enforcement for audit and evidence tables.

$ alembic current
0004 (head)
$ alembic check
No new upgrade operations detected.
```

The trap: `stamp` writes a revision number and inspects nothing. Stamping
`0001` on a database that already has the later tables makes Alembic replay
migrations that have nothing to do. Executed against a database built from the
current models:

```
$ alembic stamp 0001
$ alembic upgrade head
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) table site_grants already exists
```

The database is now stamped `0001` while its schema is not `0001`. The upgrade
stopped at the first statement of `0002`; being one transaction, it changed
nothing (the file was byte-for-byte what the stamp had left), but it fails the
same way every time it is run. **Verify before you stamp**, as the error message
itself says: compare the tables against the schema the target revision builds,
and stamp the revision that matches what is actually there — not always `0001`.
Take a backup first; recovering from a wrong stamp means restoring.

## What `alembic check` proves

`check` autogenerates a migration against the live database and reports whether
it would contain anything:

```
$ alembic check
No new upgrade operations detected.
```

That means: **the tables, columns, indexes, and constraints in the database
match the models.** Run against a database that is behind head it fails
differently, before comparing anything:

```
$ alembic check
ERROR [alembic.util.messaging] Target database is not up to date.
FAILED: Target database is not up to date.
```

What `check` does **not** prove is as important. Alembic's autogenerate cannot
see triggers, so the append-only triggers installed by `0004` are invisible to
it. A green `check` says nothing about whether they exist. The project's test
suite asserts them separately; on a live database, look for yourself
([Restore and disaster recovery](12-restore-and-disaster-recovery.md) shows the
query).

## Downgrades, and why they are not a rollback

Every migration has a `downgrade`, and the whole chain reverses cleanly:

```
$ alembic downgrade base
INFO  [alembic.runtime.migration] Running downgrade 0004 -> 0003, …
INFO  [alembic.runtime.migration] Running downgrade 0003 -> 0002, …
INFO  [alembic.runtime.migration] Running downgrade 0002 -> 0001, …
INFO  [alembic.runtime.migration] Running downgrade 0001 -> , Schema baseline.
```

After which the only table left is `alembic_version` — verified. `downgrade
base` is not a rollback, it is a delete of everything.

Partial downgrades are lossy in the same way. Executed on a copy of a database
holding evidence:

| Step | `evidence_objects` | `evidence_attachments` |
|------|--------------------|------------------------|
| At head | 3 rows | 4 rows |
| After `alembic downgrade 0002` | table does not exist | table does not exist |
| After `alembic upgrade head` again | 0 rows | 0 rows |

The object files stayed on disk — 3 of them, untouched — but every row
referencing them is gone, and nothing will ever link them again. Migration
`0003` drops the tables on the way down because that is what "remove this
feature from the schema" means.

**The rollback for this system is a restore, not a downgrade.** See
[Upgrades](13-upgrades.md).

## Adding a migration

For development, not deployment: change the models, then

```
$ alembic revision --autogenerate -m "describe the change"
```

and read the generated script before committing it. Autogenerate does not see
triggers, server defaults it cannot introspect, or data changes. The test suite
fails a model change that has no matching migration, which is what keeps the
chain honest.

---

[← Database setup](03-database-setup.md) · [Guide index](README.md) · [Next: First run and bootstrap →](05-first-run-and-bootstrap.md)
