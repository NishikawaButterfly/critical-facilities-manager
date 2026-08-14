# 16. Retention and legal hold

[← Security hardening](15-security-hardening.md) · [Guide index](README.md) · [Next: Troubleshooting →](17-troubleshooting.md)

This release has **no retention mechanism**. Nothing expires, nothing is
deleted, and there is no legal-hold flag. That is a deliberate position rather
than an oversight, and this chapter states it plainly so you can decide whether
it is acceptable to your installation before you put real records in it.

## What is append-only, and how hard

Four tables, protected by database triggers since migration `0004`:

- `audit_entries`
- `commissioning_evidence`
- `evidence_attachments`
- `evidence_objects`

The API exposes no update or delete path for any of them. Neither does a direct
database connection. Verified on a live database, on an ordinary connection:

```
UPDATE audit_entries SET actor='someone-else'
  -> IntegrityError: audit_entries is append-only: UPDATE is not allowed
DELETE FROM evidence_attachments
  -> IntegrityError: evidence_attachments is append-only: DELETE is not allowed
```

Eight triggers on SQLite (an `UPDATE` and a `DELETE` guard per table); on
PostgreSQL, one statement-level trigger per table over a `plpgsql` function,
which also covers `TRUNCATE`. They come back with the schema on a restore —
verified ([Restore and disaster recovery](12-restore-and-disaster-recovery.md)).

Everything else — assets, orders, permits, procedures, users — is ordinary
data. The append-only rule covers history and evidence, not the operational
record.

The **object files** are not covered by any trigger. They are protected only by
the store's own write-once discipline: an object is never rewritten, but a file
deleted from the directory simply becomes a permanent `500 evidence.corrupted`
on download, with the row still asserting it exists
([Evidence storage](07-evidence-storage.md)).

## The open decision

A facility that keeps commissioning records will eventually meet at least one
of:

- **A retention period.** "Evidence for decommissioned assets is kept seven
  years, then destroyed."
- **A legal hold.** "Nothing relating to this incident is deleted until told
  otherwise" — which requires the ability to delete in the first place, plus a
  way to mark what is exempt.
- **An erasure request.** A person named in an audit entry, or visible in an
  uploaded photograph, asking for their data to be removed.

**None of these has an answer in this release.** There is no retention setting,
no hold flag, no anonymisation, and no delete endpoint. The audit trail records
usernames and cannot be edited by anything short of a migration; an uploaded
file cannot be removed at all through the application.

Do not read the append-only design as a claim that these obligations do not
apply. It is a claim about *how* a deletion should have to happen — visibly,
deliberately, and in a form somebody reviews — not that it never will. Until
that mechanism is designed, the honest statement to a compliance owner is:
this system retains everything, and removing anything is an out-of-band
operation on the database.

## The route that exists today

Exactly one, and it is deliberately expensive: **a migration that drops the
trigger, deletes, and recreates it.** The full cycle was executed on a copy of
a real database.

At head, with 8 triggers and 69 audit entries, the delete is refused. Then:

```
$ alembic downgrade 0003
INFO  [alembic.runtime.migration] Running downgrade 0004 -> 0003, Database-level append-only enforcement for audit and evidence tables.
```

Triggers: 0. Audit rows: still 69 — the downgrade removes the protection, not
the data. The delete now succeeds:

```
DELETE FROM audit_entries WHERE entity_type='api_token'
deleted rows: 4
```

And the protection goes back on:

```
$ alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 0003 -> 0004, Database-level append-only enforcement for audit and evidence tables.
```

Triggers: 8 again. Audit rows: 65.

For a real deletion you would write your own migration rather than riding
`0004`'s downgrade — one that drops the trigger, performs exactly the deletion
you intend, recreates the trigger, and lands in the repository where it is
reviewed and kept. Evidence *files* are a second, separate step: delete the
object at `objects/<first 2>/<next 2>/<hash>` yourself, after the rows that
reference it are gone, or you will have created the `500 evidence.corrupted`
case on purpose.

**The friction is the feature.** A deletion has to arrive as a migration in the
repository's history, which means it is visible, reviewable, and dated — not a
`DELETE` somebody ran against production one afternoon.

### Before you use it

1. **Back up first**, both halves ([Backups](11-backups.md)).
2. **Do it with the service stopped.** Dropping a trigger while requests are
   being served leaves a window in which the application's guarantees are not
   true.
3. **Record what you did and why, outside the system.** The deletion cannot
   audit itself: the audit trail is one of the tables you are unprotecting.
4. **Delete the narrowest thing that satisfies the requirement.** Rows are
   cheap to over-delete and impossible to get back.
5. **Confirm the triggers are back** before restarting — the query is in
   [Restore and disaster recovery](12-restore-and-disaster-recovery.md).

## What to tell a compliance owner

- The system keeps a complete, tamper-evident operational history, enforced by
  the database and not merely by application convention.
- It has no automated retention, no legal hold, and no erasure mechanism.
- Deletion is possible, requires a reviewed schema migration and downtime, and
  is not recorded by the system itself.
- Storage therefore grows without bound; capacity planning is an operational
  task ([Evidence storage](07-evidence-storage.md#sizing-the-disk)).
- If a retention obligation is firm and near, the practical answer today is to
  decide **what you put in** — for example, keeping personal data out of
  uploaded files and notes — because taking it out later is expensive and
  manual.

---

[← Security hardening](15-security-hardening.md) · [Guide index](README.md) · [Next: Troubleshooting →](17-troubleshooting.md)
