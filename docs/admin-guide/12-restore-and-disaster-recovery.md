# 12. Restore and disaster recovery

[← Backups](11-backups.md) · [Guide index](README.md) · [Next: Upgrades →](13-upgrades.md)

The procedure below was executed end to end on SQLite, with the results quoted.
The PostgreSQL equivalent differs only in how the database half is moved.

## The procedure

1. **Stop the service.** Nothing should be writing to either half.
2. **Put the database back.** SQLite: copy the file into place. PostgreSQL:
   restore the dump into an empty database.
3. **Put the evidence directory back**, whole. The tree under `objects/` must
   keep its shape — object paths are derived from content hashes, so a
   flattened or re-sorted copy is unusable.
4. **Point the configuration at both.** `CFM_DATABASE_URL` and
   `CFM_EVIDENCE_DIR` are not in the backup ([Backups](11-backups.md)); if the
   restore landed somewhere new, they have to say so.
5. **Start the service and verify** — the four checks below.

## What a verified restore looks like

The source installation held 69 audit entries and 3 evidence objects, one of
them attached moments before the backup. Database and evidence directory were
copied with the service stopped, and the application was started against the
copies.

**The service starts:**

```
$ curl -s $API/health
{"status":"ok","version":"0.2.0","database":"ok"}
```

**The record is complete.** `GET /audit-entries` reported `69` on the restored
database, against `69` on the source. The audit trail is the right thing to
count: it grows with every write and never shrinks, so a matching total means
nothing was lost between the last write and the copy.

**The evidence is really there.** Downloading the object attached just before
the backup returned `200`, and the bytes were identical to what was uploaded.
That check is the one that distinguishes a real restore from a database-only
one, which reports healthy and fails only on download
([Backups](11-backups.md#why-a-database-backup-alone-is-incoherent)).

**The append-only triggers came back with the schema.** They are part of the
schema, created by migration `0004`, so a restore of the database restores
them:

```
$ sqlite3 cfm.db "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
trg_audit_entries_no_delete
trg_audit_entries_no_update
trg_commissioning_evidence_no_delete
trg_commissioning_evidence_no_update
trg_evidence_attachments_no_delete
trg_evidence_attachments_no_update
trg_evidence_objects_no_delete
trg_evidence_objects_no_update
```

Eight triggers, two per protected table. And they fire — every one of these was
attempted against the restored database on a direct connection, and every one
was refused:

```
UPDATE audit_entries SET actor='someone-else'
  -> IntegrityError: audit_entries is append-only: UPDATE is not allowed
DELETE FROM audit_entries
  -> IntegrityError: audit_entries is append-only: DELETE is not allowed
UPDATE evidence_objects SET sha256=…
  -> IntegrityError: evidence_objects is append-only: UPDATE is not allowed
DELETE FROM evidence_attachments
  -> IntegrityError: evidence_attachments is append-only: DELETE is not allowed
UPDATE commissioning_evidence SET note='rewritten'
  -> IntegrityError: commissioning_evidence is append-only: UPDATE is not allowed
```

On PostgreSQL the same protection is one statement-level trigger per table
plus a `plpgsql` function, and it also covers `TRUNCATE`; that branch is
exercised by CI rather than here.

**Run this check after every restore.** Autogenerate cannot see triggers, so
`alembic check` will report a clean schema whether or not they exist
([Migrations](04-migrations.md#what-alembic-check-proves)). The query above is
the only proof.

## The four post-restore checks, as commands

```
$ curl -s $API/health
$ curl -s -H "Authorization: Bearer $TOKEN" "$API/audit-entries?limit=1" | grep -o '"total":[0-9]*'
$ curl -s -o /tmp/probe -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
    $API/evidence-objects/<a known object id>/content
$ sqlite3 cfm.db "SELECT count(*) FROM sqlite_master WHERE type='trigger'"     # expect 8
```

Keep a known object id with the backup documentation. Hunting for one during a
recovery is time you will not have.

## Partial disasters

**The evidence directory is lost, the database survives.** Restore the
directory from the last backup. Objects written after that backup are gone for
good: the rows referencing them stay, the metadata still answers, and downloads
answer `500 evidence.corrupted` forever. There is no repair and no way to blank
the reference — the rows are append-only. Record which objects are affected
while you still know the window.

**The database is lost, the evidence survives.** Restore the database. Every
object written after that backup becomes an orphan — present on disk,
referenced by nothing, invisible to the API. Harmless, and reusable: if the same
file is uploaded again it lands on the same path.

**One evidence object is corrupt.** Copy that one file back from a backup, to
the same path. Nothing else needs to change, because the path is the hash, and
the download re-verifies. Verified: restoring the original bytes turned a `500
evidence.corrupted` back into a `200` with the correct content, with no restart.

**The database is intact but at the wrong revision.** That is not a restore
problem; see [Migrations](04-migrations.md).

## Recovery expectations, stated plainly

- **Recovery point** = the last backup. There is no write-ahead shipping, no
  replication, and no point-in-time recovery in the application. On PostgreSQL
  you can build one at the database layer; the evidence directory still limits
  you to whenever it was last copied.
- **Recovery time** = copy time plus a start. Minutes for SQLite installations
  of the size this release targets.
- **The audit trail is the source of truth about the gap.** Compare its total
  before and after; the difference is what the incident cost.
- **Nothing reconciles the two halves for you.** There is no command that lists
  rows whose objects are missing. To find them, walk `evidence_objects` and
  check for a file at `objects/<first 2>/<next 2>/<hash>` for each.

## After any restore, tell the operators

Two consequences are visible to them and confusing without warning:

- **Records created after the backup are gone.** They will re-enter work that
  they know they did, and the audit trail will show it happening twice at
  different times. That is honest and unavoidable.
- **Evidence that cannot be produced still shows in the record.** The manual's
  advice for `500 evidence.corrupted` is to stop and report it
  ([user manual, chapter 18](../user-guide/18-troubleshooting.md)); after a
  partial recovery you can tell them which objects will do that and why.

---

[← Backups](11-backups.md) · [Guide index](README.md) · [Next: Upgrades →](13-upgrades.md)
