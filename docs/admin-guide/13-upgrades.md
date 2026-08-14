# 13. Upgrades

[← Restore and disaster recovery](12-restore-and-disaster-recovery.md) · [Guide index](README.md) · [Next: Monitoring and health →](14-monitoring-and-health.md)

An upgrade is four steps in one order: **back up, migrate, restart, verify.**
The application enforces part of that order for you — it refuses to serve a
database that is not at the revision it expects — but nothing enforces the
backup, and that is the step you will want.

## The procedure

### 1. Back up

Database and evidence directory, together, with the service stopped
([Backups](11-backups.md)). Write down two things with the backup:

- the **version you are upgrading from**, because rolling back means running
  that version again;
- the **revision the database is at**:

  ```
  $ alembic current
  0004 (head)
  ```

### 2. Install the new code

```
$ git fetch && git checkout <the new tag>
$ python -m pip install -e .
```

Do not start the service yet. If the new release contains migrations, the old
schema is not one it will serve — and if you start it first, it will simply
refuse with the message in [Migrations](04-migrations.md).

### 3. Migrate

With the service still stopped:

```
$ alembic upgrade head
```

Run this **once**, from one place. It is a deploy step, not a startup step; that
is the whole reason `CFM_DB_AUTO_UPGRADE` defaults to off
([Migrations](04-migrations.md#why-this-is-the-default)). Then confirm:

```
$ alembic current
0004 (head)
$ alembic check
No new upgrade operations detected.
```

`check` compares the live schema against the models. It cannot see triggers, so
on any release that touches the append-only tables, also confirm they still
exist ([Restore and disaster recovery](12-restore-and-disaster-recovery.md)) —
SQLite's batch mode rebuilds a table by copying it, which drops its triggers,
and a migration that does that without recreating them would leave the audit
trail writable.

### 4. Restart and verify

```
$ curl -s $API/health
{"status":"ok","version":"0.2.0","database":"ok"}
```

Then four checks that between them touch every subsystem:

| Check | Command | Proves |
|-------|---------|--------|
| Version | `GET /api/v1/health` | The new code is what is running — provided nobody set `CFM_APP_VERSION` ([Configuration](02-configuration.md#settings-that-can-lie-to-you)) |
| Authentication | `GET /api/v1/me` with a real token | Tokens survived; the database is readable |
| Evidence | `GET /api/v1/evidence-objects/<known id>/content` | The store is reachable and verifying — this is the one that catches a lost `CFM_EVIDENCE_DIR` |
| Frontend | `GET /ui/config.json` | The page can still configure itself |

The evidence check is not optional. An upgrade that moves the working directory
or the deployment path silently changes where a relative `CFM_EVIDENCE_DIR`
points ([Installation](01-installation.md)), and the only symptom is a `500` the
next time somebody opens a file.

## Downtime

There is downtime, and the design assumes it. The service must be stopped while
the database migrates, because the alternative is a running process writing
against a schema that is changing under it. For the single-process deployments
this release targets, that window is the length of `alembic upgrade head` plus a
restart.

Zero-downtime upgrades are not supported: there is no support for running two
versions against one database, and no expand-and-contract contract for
migrations to follow. Do not try to keep the old process alive through the
migration — it will keep serving with a schema that no longer matches its
models, and the refuse-to-start check only runs at startup.

## Rolling back

**Restore the backup. Do not downgrade.**

Alembic's downgrades exist and they run cleanly, but they are schema
operations, and removing a schema removes the data in it. Executed on a copy of
a database holding evidence:

| Step | `evidence_objects` | `evidence_attachments` |
|------|--------------------|------------------------|
| At head | 3 rows | 4 rows |
| After `alembic downgrade 0002` | table does not exist | table does not exist |
| After `alembic upgrade head` again | 0 rows | 0 rows |

The object files were still on disk — all three — but every row that said what
they were is gone, permanently. `alembic downgrade base` is worse: it leaves
`alembic_version` and nothing else.

So the rollback procedure is:

1. Stop the service.
2. Restore the database and the evidence directory from the pre-upgrade backup.
3. Reinstall the previous version of the code.
4. Start, and run the four verification checks above.

Everything written between the upgrade and the rollback is lost — that is what
"restore the backup" means. Keep the upgrade window short and quiet for exactly
this reason, and export or write down anything created in it before rolling
back.

The one case where a downgrade is the right tool is a migration you have just
run on a database that has no data yet, and even then the cost of restoring
instead is a file copy.

## Upgrading the Python interpreter or the dependencies

The package requires Python 3.12 or newer and is tested on 3.12 and 3.13.
Moving between them is a rebuild of the virtual environment, not a data
operation:

```
$ rm -rf .venv && python3.13 -m venv .venv
# Activate .venv with the command for your shell, then:
$ python -m pip install -e .
```

Neither the database nor the evidence store is affected — the store holds bytes
and hash-named paths, and neither depends on the interpreter. Verify with the
same four checks.

## A checklist you can copy

```
[ ] Note the current version and `alembic current`
[ ] Stop the service
[ ] Back up the database AND the evidence directory
[ ] Install the new code
[ ] alembic upgrade head        (once, from one host)
[ ] alembic current             -> expect head
[ ] alembic check               -> "No new upgrade operations detected."
[ ] Confirm the append-only triggers still exist
[ ] Start the service
[ ] /health, /me, a known evidence download, /ui/config.json
[ ] Keep the backup until the new version has run a full working day
```

---

[← Restore and disaster recovery](12-restore-and-disaster-recovery.md) · [Guide index](README.md) · [Next: Monitoring and health →](14-monitoring-and-health.md)
