# 11. Backups

[← Rate limiting in production](10-rate-limiting.md) · [Guide index](README.md) · [Next: Restore and disaster recovery →](12-restore-and-disaster-recovery.md)

This system keeps its state in two places, and a backup of one without the
other is not a backup.

## What has to be backed up

| What | Where | Contains |
|------|-------|----------|
| The database | `CFM_DATABASE_URL` | Every record, every audit entry, and the hash, size, filename, and uploader of every evidence object |
| The evidence store | `CFM_EVIDENCE_DIR` | The bytes those hashes refer to |

Neither can be regenerated from the other. The database knows a file's SHA-256
but not its content; the store knows the content but not what it evidences, who
attached it, or when — there is no filename, no metadata, and no index in the
store at all.

### Why a database backup alone is incoherent

Executed: an evidence file was attached, a full backup was taken, and then the
database alone was restored next to an empty evidence directory. The service
started, and:

```
$ curl -s $API/health
{"status":"ok","version":"0.2.0","database":"ok"}
```

Health is fine. The record is fine — the metadata endpoint still answers with
the hash, size, filename, and uploader. Only the content is gone:

```
{"status":500,"error_code":"evidence.corrupted",
 "detail":"Evidence object 131254a0-… cannot be served: Evidence object dd210c25… is
 missing from the store."}
```

Nothing about that installation announces itself as broken until somebody asks
for a file. A commissioning report that cannot produce its evidence is exactly
the failure this system exists to prevent, so treat "database and evidence are
one backup set" as a rule with no exceptions.

The reverse — evidence without the database — is worse: the store is a pile of
hash-named files with nothing to say what any of them is.

## Taking the backup

### SQLite

Both files are ordinary files. **Stop the service first.** The database runs in
journal mode `delete`, not WAL, so a copy taken mid-write can capture a
torn state, and a copying tool that takes a lock will make the running service
fail requests ([Database setup](03-database-setup.md)).

```
$ systemctl stop cfm            # or however the service is supervised
$ cp /var/lib/cfm/cfm.db        /backups/cfm-2026-08-14/cfm.db
$ cp -a /var/lib/cfm/evidence   /backups/cfm-2026-08-14/evidence
$ systemctl start cfm
```

The executed version of this — a `copy2` of the database and a `copytree` of the
evidence directory with the service down — restored perfectly; the procedure and
its verification are in
[Restore and disaster recovery](12-restore-and-disaster-recovery.md).

If stopping the service is not acceptable, use SQLite's own online backup
(`sqlite3 cfm.db ".backup /backups/cfm.db"`) rather than a file copy, and copy
the evidence directory afterwards — never before. That ordering matters and the
reason is in the next section. This variant was not executed here.

### PostgreSQL

> Not executed while writing this guide; no PostgreSQL server was available.

`pg_dump` (or your platform's snapshot mechanism) for the database, and a copy
of the evidence directory. The same ordering rule applies: **database first,
evidence second.**

## Order matters, and only in one direction

Evidence objects are written before the database row that references them
commits, and objects are never rewritten or deleted. So:

- **Database first, evidence second** — an object might exist that no row
  references. That is harmless: it is invisible to the API, and a later upload
  of the same content lands on the same path and reuses it.
- **Evidence first, database second** — a row might reference an object the
  evidence copy does not contain. That is the failure demonstrated above: a
  permanent `500` on download.

Same rule for online backups of either component.

## What is *not* in the backup

- **Token secrets.** Only hashes are stored. Restoring a backup restores the
  hashes, so existing tokens keep working — but no backup can ever give you a
  token you failed to write down.
- **Configuration.** `CFM_DATABASE_URL`, `CFM_EVIDENCE_DIR`, the rate-limit
  settings, `CFM_TRUSTED_PROXY`: none of it is in the database. Back up your
  environment file or deployment manifest separately, or a restore will come up
  pointing at the wrong paths.
- **The application itself.** Record the version you were running — a database
  at revision `0004` cannot be served by a release that only knows `0003`
  ([Upgrades](13-upgrades.md)).
- **Rate-limiter state**, which is in memory and irrelevant.

## Retention and sizing

The evidence store only grows: nothing in this release deletes an object
([Retention and legal hold](16-retention-and-legal-hold.md)). Full copies of a
growing directory get expensive, and the content-addressed layout has one
useful property here — **an object, once written, never changes.** Any
incremental or deduplicating backup tool will therefore transfer each object
exactly once, forever. Use one.

The database is small by comparison. A demo database with seven assets and
sixty-nine audit entries was 450 KB; the audit trail grows with activity and
never shrinks, but it is text.

## Testing the backup

A backup that has never been restored is a hypothesis. Test it on a schedule —
the procedure in the next chapter takes minutes on SQLite — and make the test
answer three questions:

1. Does the service start against the restored copies?
2. Does `GET /audit-entries` return the same total as the source?
3. Does a known evidence object download with `200` and identical bytes?

All three were verified end to end on a real restore, and the third is the one
that catches a backup set that copied only half of what matters.

---

[← Rate limiting in production](10-rate-limiting.md) · [Guide index](README.md) · [Next: Restore and disaster recovery →](12-restore-and-disaster-recovery.md)
