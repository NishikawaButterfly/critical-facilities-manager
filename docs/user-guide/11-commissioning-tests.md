# 11. Commissioning tests

[← Incidents](10-incidents.md) · [Manual index](README.md) · [Next: Punch items →](12-punch-items.md)

A commissioning test is a witnessed acceptance test on one asset. It is
recorded once, by the person who ran it, naming the person who watched — and
that pair of names is the point of the record.

## What a test records

| Field | Meaning |
|-------|---------|
| `asset_id` | The asset under test. Required. |
| `procedure_id` | The approved procedure it was run to. Optional. |
| `title` | What the test is. |
| `planned_date` | When it was meant to happen. |
| `status` | `pending`, `passed`, or `failed`. |
| `executed_by` | Who recorded the result. Set from your token. |
| `witnessed_by` | Who witnessed it. Named by the executor, must be someone else. |
| `evidence` | A list of text notes, each with its author and timestamp. |
| `punch_item_id` | The punch item opened from a failure, if any. |

`planned_date` is a plan, not a fact. Nothing records when the test was actually
run beyond the timestamp on the result, and a test may be recorded before or
after its planned date without complaint.

## The state machine

```
          ┌──▶ passed
pending ──┤
          └──▶ failed
```

| Status | What it means | May move to |
|--------|---------------|-------------|
| `pending` | Planned or in progress. Evidence may be added. Files may be attached. | `passed`, `failed` |
| `passed` | Accepted. **Terminal.** | — |
| `failed` | Not accepted. May open a punch item. **Terminal.** | — |

**A result is recorded once and never revised.** There is no way to correct a
test recorded as failed when it passed, or vice versa:

```
{"status":409,"error_code":"commissioning_test.invalid_transition",
 "detail":"Commissioning test status cannot change from 'failed' to 'passed'; allowed: none."}
```

A retest is a new test. That is the right shape for the record — a unit that
failed once and passed on retest should show both — but it means a typo in the
result is permanent and must be explained by the retest that follows it.

## Creating a test

**API only.**

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"asset_id":"dd7b486d-c66b-471a-9f6e-07bc7145a485",
         "title":"CRAC unit 2 condensate alarm retest",
         "planned_date":"2026-08-20"}' \
    $API/commissioning-tests
{
  "id": "b329dbe6-b99f-4d29-b21e-ece7001ce25e",
  "asset_id": "dd7b486d-c66b-471a-9f6e-07bc7145a485",
  "procedure_id": null,
  "title": "CRAC unit 2 condensate alarm retest",
  "planned_date": "2026-08-20",
  "status": "pending",
  "executed_by": null,
  "witnessed_by": null,
  "punch_item_id": null,
  "evidence": [],
  "created_at": "2026-08-13T18:27:18.481755Z",
  "updated_at": "2026-08-13T18:27:18.481759Z"
}
```

| Field | Rules |
|-------|-------|
| `asset_id` | Required; must exist |
| `procedure_id` | Optional; if given, must be **approved** |
| `title` | Required, 1–255 characters |
| `planned_date` | Required, `YYYY-MM-DD` |

A draft or retired procedure is refused with
`commissioning_test.procedure_not_approved`.

Nothing checks the asset's status. A test can be recorded against an
`operational` unit or a `decommissioned` one; commissioning normally happens
while the asset is `installed`, but that is your discipline, not the software's.

**Permission:** the site of the asset.

## Recording evidence while the test is pending

Two kinds of evidence, both only while the test is `pending`.

### Text notes

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"note":"Float switch continuity confirmed at the unit; wiring re-terminated."}' \
    $API/commissioning-tests/{id}/evidence
```

The response is the whole test, with the note appended to its `evidence` list:

```
{ ... "evidence": [
    {"id": 5,
     "note": "Float switch continuity confirmed at the unit; wiring re-terminated.",
     "actor": "priya-eng",
     "recorded_at": "2026-08-13T18:27:19.135868Z"}
  ] ... }
```

Each note carries who wrote it and when. Notes are **append-only at the database
level** — nothing in the product, and nothing with a direct database connection,
can edit or delete one afterwards. Write carefully.

Once the result is recorded the test stops taking notes:

```
{"status":409,"error_code":"commissioning_test.not_pending",
 "detail":"Evidence can only be appended while the test is pending; this one is 'passed'."}
```

### Files

Readings, photographs, instrument printouts, and signed sheets attach as files
on the same test, and are also limited to a `pending` test:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" \
    -F "file=@alarm-log.txt;type=text/plain" \
    -F "note=Head-end log covering the retest window." \
    $API/commissioning-tests/{id}/evidence-files
```

See [Evidence](13-evidence.md) for hashes, download, and verification.

**Attach everything before you record the result.** Both routes close the moment
the test passes or fails, and there is no way to add anything afterwards — not a
note, not a file, not a correction. This is the single most common way to end up
with an incomplete commissioning record.

## Recording the result

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"witness":"marco-eng",
         "evidence_note":"Simulated overflow still did not raise the alarm at the head end."}' \
    $API/commissioning-tests/{id}/fail
```

`/pass` and `/fail` take the same body:

| Field | Rules |
|-------|-------|
| `witness` | Required. The **username** of an active user, who must not be you. |
| `evidence_note` | Optional. Appended to the evidence list as a final note before the result is stored. |

The response is the completed test:

```
{
  "status": "failed",
  "executed_by": "priya-eng",
  "witnessed_by": "marco-eng",
  "evidence": [
    {"id": 5, "note": "Float switch continuity confirmed…", "actor": "priya-eng", …},
    {"id": 6, "note": "Simulated overflow still did not raise the alarm…", "actor": "priya-eng", …}
  ],
  …
}
```

Note that the final note is attributed to **you**, the executor, not to the
witness. A witness statement in the witness's own words has to be recorded by
the witness, as a note, before the result is submitted.

### The witness rules

**The witness must not be the executor:**

```
{"status":409,"error_code":"commissioning_test.witness_must_differ",
 "detail":"A commissioning test must be witnessed by someone other than its executor ('priya-eng')."}
```

**The witness must be an active user of this system**, named by username:

```
{"status":422,"error_code":"commissioning_test.witness_not_active_user",
 "detail":"The witness must be an active user; 'someone-not-here' is not."}
```

That means a client's representative, a third-party commissioning agent, or a
manufacturer's engineer cannot be recorded as the witness unless they hold an
account here. Where the real witness is external, the usual practice is to
record an internal witness and name the external one in an evidence note — which
is weaker than it looks, and worth knowing before an acceptance meeting. See
[Known limitations](19-known-limitations.md).

The witness never confirms anything in the system. The executor types their
username; the witness is not asked, notified, or given anything to sign. The
control is that the executor is on record as having named them.

## When a test fails: opening a punch item

A failed test may open **one** punch item, on the same asset:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"category":"defect","severity":"major",
         "description":"Alarm still does not reach monitoring after re-termination; suspect the head-end point mapping.",
         "due_date":"2026-09-05"}' \
    $API/commissioning-tests/{id}/punch-item
{
  "id": "4bd91116-003f-4d45-9172-e6585d19b5a3",
  "asset_id": "dd7b486d-c66b-471a-9f6e-07bc7145a485",
  "location_id": null,
  "category": "defect",
  "severity": "major",
  "status": "open",
  "title": "CRAC unit 2 condensate alarm retest",
  …
}
```

| Field | Rules |
|-------|-------|
| `severity` | Required: `minor`, `major`, `blocking` |
| `category` | Optional; defaults to `defect` |
| `title` | Optional — **defaults to the test's title** |
| `description` | Optional |
| `due_date` | Optional |

Let the title default only if the test title reads sensibly as a defect. Here it
became "CRAC unit 2 condensate alarm retest", which names the test rather than
the fault — a punch list full of test names is harder to work from than one
full of problems. Supply a title.

The refusals:

```
{"status":409,"error_code":"commissioning_test.not_failed",
 "detail":"A punch item can only be opened from a failed commissioning test; this one is 'passed'."}

{"status":409,"error_code":"commissioning_test.punch_item_exists",
 "detail":"Commissioning test 15a32633-… is already linked to punch item 664c7788-…."}
```

A test that failed for several distinct reasons gets one linked punch item; any
others must be created directly against the asset, with the link recorded only
in their text.

The punch item is then an ordinary one: started by whoever fixes it, closed by
somebody else. See [Punch items](12-punch-items.md).

## Finding tests

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/commissioning-tests?asset_id=dd7b486d-…&status=pending"
```

| Parameter | Effect |
|-----------|--------|
| `asset_id` | Tests on one asset |
| `status` | `pending`, `passed`, `failed` |

`?status=pending` is the outstanding test list. There is no filter by planned
date, so "what is overdue for test" cannot be asked — you list the pending ones
and read the dates.

Tests do not appear in the web interface at all.

---

[← Incidents](10-incidents.md) · [Manual index](README.md) · [Next: Punch items →](12-punch-items.md)
