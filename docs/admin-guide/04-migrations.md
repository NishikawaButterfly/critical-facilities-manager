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

The database is now stamped `0001` while its schema is not `0001`, and the
upgrade has failed part-way. **Verify before you stamp**, as the error message
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
