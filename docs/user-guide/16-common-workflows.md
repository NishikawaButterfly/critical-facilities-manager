# 16. Common workflows

[← The web interface](15-web-interface.md) · [Manual index](README.md) · [Next: Error messages explained →](17-error-messages.md)

Three complete jobs, run end to end. Every request and response below was
executed against version 0.2.0 with the demo dataset seeded; the identifiers are
from that run and will differ on yours.

Throughout:

```
export API=http://localhost:8000/api/v1
export PRIYA=<priya-eng's token>     # does the work
export MARCO=<marco-eng's token>     # does the second half
```

Two engineers are needed. That is not a convention of these examples — it is
enforced, at three separate points in the first workflow alone.

---

## Workflow 1: planned UPS maintenance, end to end

**The job.** UPS System 1 needs its battery string A replaced. It is one half of
a redundant pair, so the work must not overlap with anything on UPS System 2. It
must be done under an approved procedure, authorized by a second person, and
evidenced.

**What it will demonstrate.** Four-eyes approval, four-eyes permit issue, the
redundancy constraint firing, the permit gate on completion, evidence with a
verifiable hash, and the audit trail that results.

### Step 1 — Find the asset

There is no search. List the site's critical assets and read:

```
$ curl -H "Authorization: Bearer $PRIYA" \
    "$API/assets?site_id=6eddaddd-e6cf-4ca8-bdf3-c9f1921587cf&criticality=critical"
```

```
UPS-C-01  4cd1ad3e-1436-410b-a7ee-f2c86143fd8d  operational
UPS-C-02  8338e91b-3bd7-4978-b894-4e885263009c  operational
GEN-C-01  6ccedcd6-fa37-4273-b511-b903ebfee630  operational
```

Alternatively, click the asset in the [asset tree](15-web-interface.md#asset-tree)
and copy its id from the detail panel.

### Step 2 — Check what constrains it

Do this **before** planning anything. Nothing will remind you:

```
$ curl -H "Authorization: Bearer $PRIYA" \
    "$API/constraints?asset_id=4cd1ad3e-1436-410b-a7ee-f2c86143fd8d"
```

```
active  mutual_exclusive_maintenance  UPS redundancy pair
        UPS 1 and UPS 2 back each other; only one may be worked on at a time.
```

Now you know the work must not overlap with UPS-C-02.

### Step 3 — Draft the procedure (Priya)

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"kind":"mop","title":"UPS battery string replacement",
         "steps":["Confirm the redundant UPS is carrying the load and is healthy.",
                  "Transfer UPS 1 to maintenance bypass and confirm the bypass is closed.",
                  "Isolate the battery string and verify zero volts at the terminals.",
                  "Replace the string, torque the links, and record the cell voltages.",
                  "Return to normal operation and confirm the transfer with operations."]}' \
    $API/procedures
```

```
{
  "id": "a9bfa826-957c-4449-93b4-63996ac0c511",
  "kind": "mop", "status": "draft", "version": 1,
  "last_edited_by": "priya-eng", "approved_by": null, …
}
```

Skip this step if an approved procedure already exists — the demo campus ships
an approved MOP for CRAC fan replacement.

### Step 4 — Approve it (Marco)

Priya cannot approve her own draft:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" $API/procedures/a9bfa826-…/approve
{"status":409,"error_code":"procedure.approver_must_differ",
 "detail":"A procedure must be approved by someone other than the author of its last draft edit ('priya-eng')."}
```

Marco can:

```
$ curl -X POST -H "Authorization: Bearer $MARCO" $API/procedures/a9bfa826-…/approve
{"id":"a9bfa826-…","status":"approved","version":1,
 "last_edited_by":"priya-eng","approved_by":"marco-eng",
 "updated_at":"2026-08-13T18:24:04.267974Z"}
```

**Four eyes, one.**

### Step 5 — Create the maintenance order (Priya)

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"order_type":"preventive","asset_id":"4cd1ad3e-1436-410b-a7ee-f2c86143fd8d",
         "title":"UPS 1 battery string replacement",
         "description":"Replace battery string A under the approved MOP; UPS 2 carries the load.",
         "due_date":"2026-09-30"}' \
    $API/maintenance-orders
```

```
{
  "id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "order_type": "preventive", "status": "draft",
  "asset_id": "4cd1ad3e-1436-410b-a7ee-f2c86143fd8d",
  "title": "UPS 1 battery string replacement",
  "due_date": "2026-09-30", "completion_notes": null,
  "created_at": "2026-08-13T18:24:04.490959Z", …
}
```

### Step 6 — Schedule it

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" $API/maintenance-orders/af881c0d-…/schedule
{"id":"af881c0d-…","status":"scheduled", …}
```

Also available as **Schedule** on the [order board](15-web-interface.md#order-board).

This step is not optional bureaucracy: a permit cannot be requested against a
draft order.

### Step 7 — Request the permit, under the approved procedure (Priya)

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"order_id":"af881c0d-29e8-404c-9d1d-692b677dd31a",
         "procedure_id":"a9bfa826-957c-4449-93b4-63996ac0c511",
         "scope":"Replace battery string A of UPS-C-01 in Electrical Room 1A; UPS on maintenance bypass."}' \
    $API/work-permits
```

```
{
  "id": "58722f9c-3b33-462a-a807-8b3bdf362c5c",
  "order_id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "procedure_id": "a9bfa826-957c-4449-93b4-63996ac0c511",
  "scope": "Replace battery string A of UPS-C-01 in Electrical Room 1A; UPS on maintenance bypass.",
  "status": "requested",
  "requested_by": "priya-eng", "issued_by": null, …
}
```

The `scope` text is what a technician reads on site. Write it for them.

### Step 8 — A second actor issues it (Marco)

The requester cannot:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" $API/work-permits/58722f9c-…/issue
{"status":409,"error_code":"work_permit.issuer_must_differ",
 "detail":"A work permit must be issued by someone other than its requester ('priya-eng')."}
```

Marco issues:

```
$ curl -X POST -H "Authorization: Bearer $MARCO" $API/work-permits/58722f9c-…/issue
{"id":"58722f9c-…","status":"issued",
 "requested_by":"priya-eng","issued_by":"marco-eng",
 "updated_at":"2026-08-13T18:24:26.606771Z"}
```

**Four eyes, two.** Note there is no route back: a requested permit can only be
issued, never withdrawn ([Work permits](08-work-permits.md#a-requested-permit-cannot-be-withdrawn)).

### Step 9 — Start the order, and meet the constraint check

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" $API/maintenance-orders/af881c0d-…/start
{"id":"af881c0d-…","status":"in_progress","updated_at":"2026-08-13T18:24:26.862827Z"}
```

Work is now in progress on UPS-C-01. This is the moment the redundancy
constraint takes effect. A colleague who now tries to start work on UPS-C-02 —
having scheduled it perfectly legitimately — is stopped:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" $API/maintenance-orders/2d05a32f-…/start
{
  "status": 409,
  "error_code": "maintenance_order.mutual_exclusion_conflict",
  "detail": "Constraint 'UPS redundancy pair' (586b0568-36a5-4f5b-9379-831ae84df916) allows only one of its member assets under maintenance at a time; order af881c0d-29e8-404c-9d1d-692b677dd31a ('UPS 1 battery string replacement') is already in progress on asset 4cd1ad3e-1436-410b-a7ee-f2c86143fd8d."
}
```

The same message appears on the order board card when they press **Start**.

**Note what did not happen.** Nothing checked that the permit had been issued —
an order with no permit at all would have started just as readily. And the
asset's own status is untouched:

```
$ curl -H "Authorization: Bearer $PRIYA" $API/assets/4cd1ad3e-…
UPS-C-01  operational   (updated_at still 2026-08-13T18:17:49.601521Z)
```

### Step 10 — Take the asset out of service

If the equipment really is out of service, say so:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"to_status":"under_maintenance"}' $API/assets/4cd1ad3e-…/transition
UPS-C-01  under_maintenance
```

Nobody will do this for you, and the inventory is wrong until somebody does.

### Step 11 — Do the work, and attach the evidence

While the order is `in_progress`, attach what proves the work:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" \
    -F "file=@battery-cell-voltages.txt;type=text/plain" \
    -F "note=Cell voltages recorded after the string was replaced." \
    $API/maintenance-orders/af881c0d-…/evidence-files
```

```
{
  "id": 1,
  "evidence_object": {
    "id": "b964f12a-0bf4-46d9-a6f0-49b7ffe9df8a",
    "sha256": "99826de98e1da74a25499c65c7f01ae1502ccc1b863d0ed82c7cf14d49938200",
    "size_bytes": 165,
    "filename": "battery-cell-voltages.txt",
    "content_type": "text/plain",
    "uploaded_by": "priya-eng", …
  },
  "order_id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "note": "Cell voltages recorded after the string was replaced.",
  "attached_by": "priya-eng",
  "attached_at": "2026-08-13T18:25:37.647015Z"
}
```

Check the hash against the file on your own machine:

```
$ sha256sum battery-cell-voltages.txt
99826de98e1da74a25499c65c7f01ae1502ccc1b863d0ed82c7cf14d49938200
```

They agree, so the bytes recorded are the bytes you sent.

### Step 12 — Try to complete, and meet the permit gate

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"completion_notes":"String replaced; all cells within tolerance."}' \
    $API/maintenance-orders/af881c0d-…/complete
{"status":409,"error_code":"maintenance_order.open_permit",
 "detail":"The maintenance order still has an issued work permit open; close or revoke the permit before completing the order."}
```

This is the only place a permit is enforced, and it is enforced at the end
rather than the beginning: the system does not check that you *had* authorization
to start, only that you *closed out* the authorization you took.

### Step 13 — Close the permit

A completion note is required:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{}' $API/work-permits/58722f9c-…/close
{"status":422,"error_code":"api.validation_failed",
 "invalid_params":[{"name":"body.completion_note","reason":"Field required"}]}
```

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"completion_note":"Work finished, bypass removed, UPS back on inverter; area left clear."}' \
    $API/work-permits/58722f9c-…/close
{
  "id": "58722f9c-…", "status": "closed",
  "requested_by": "priya-eng", "issued_by": "marco-eng",
  "completion_note": "Work finished, bypass removed, UPS back on inverter; area left clear.",
  "updated_at": "2026-08-13T18:26:25.575723Z"
}
```

Closing is *not* four-eyes — the requester may close their own permit. Only
issuing needs the second person.

### Step 14 — Complete the order

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"completion_notes":"Battery string A replaced. Cell voltages recorded in the attached evidence file. UPS returned to normal operation."}' \
    $API/maintenance-orders/af881c0d-…/complete
```

```
{
  "id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "status": "done",
  "completion_notes": "Battery string A replaced. Cell voltages recorded in the attached evidence file. UPS returned to normal operation.",
  "updated_at": "2026-08-13T18:26:26.028101Z"
}
```

Also available as **Complete** on the order board, which opens a form for the
notes.

### Step 15 — Return the asset to service

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"to_status":"operational"}' $API/assets/4cd1ad3e-…/transition
UPS-C-01  operational
```

And UPS-C-02 may now be worked on — the constraint released the moment the first
order left `in_progress`.

### What the record now proves

Ordered by audit id, the trail across the procedure, the order, the permit, and
the asset:

```
#74  18:23:54  priya-eng  procedure          created
#75  18:24:04  marco-eng  procedure          status_changed      (approved)
#76  18:24:04  priya-eng  maintenance_order  created
#77  18:24:13  priya-eng  maintenance_order  status_changed      (scheduled)
#78  18:24:13  priya-eng  work_permit        created             (requested)
#79  18:24:26  marco-eng  work_permit        status_changed      (issued)
#80  18:24:26  priya-eng  maintenance_order  status_changed      (in_progress)
#83  18:25:02  priya-eng  asset              status_changed      (under_maintenance)
#84  18:25:37  priya-eng  maintenance_order  evidence_attached
#85  18:26:25  priya-eng  work_permit        status_changed      (closed)
#86  18:26:26  priya-eng  maintenance_order  status_changed      (done)
#87  18:26:26  priya-eng  asset              status_changed      (operational)
```

Read as evidence, that sequence establishes:

- **Two people were involved, and which parts each did.** Marco approved the
  procedure and issued the permit; Priya wrote the procedure, ran the work, and
  closed it out. Entries 75 and 79 are the only ones with Marco's name, and they
  are exactly the two controls.
- **The work was authorized before it began.** Entry 79 (issued) precedes entry
  80 (started) by 260 milliseconds. Nothing in the software required that
  ordering — the record shows it happened, which is different from the system
  having enforced it.
- **The procedure existed and was approved before the permit named it.** Entries
  74 and 75 precede 78.
- **The redundant unit was protected throughout**, because the constraint
  refused every attempt to start work on it while entry 80's order was open.
- **The evidence is exactly the file that was attached.** Entry 84 carries
  `sha256: 99826de9…`, the object row carries it, and the store holds the bytes
  under it. Downloading re-hashes and compares before serving.
- **None of it can be changed.** Every line above is in an append-only table
  that the database refuses to update or delete
  ([The audit trail](14-audit-trail.md#why-it-cannot-be-changed)).

What it does **not** establish: that anyone read the procedure, that the permit
was on site, that the work matched the scope, or that the cell voltages in the
file were measured rather than typed. Those remain human facts.

---

## Workflow 2: a commissioning test fails and becomes a punch item

**The job.** CRAC unit 2 is being brought into service. Its condensate alarm
failed on first test; the wiring has been re-terminated and it needs retesting.

### Step 1 — Create the test

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"asset_id":"dd7b486d-c66b-471a-9f6e-07bc7145a485",
         "title":"CRAC unit 2 condensate alarm retest",
         "planned_date":"2026-08-20"}' \
    $API/commissioning-tests
{"id":"b329dbe6-b99f-4d29-b21e-ece7001ce25e","status":"pending",
 "executed_by":null,"witnessed_by":null,"evidence":[], …}
```

Add `procedure_id` if the test runs to an approved procedure.

### Step 2 — Record evidence *while it is pending*

Text notes:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"note":"Float switch continuity confirmed at the unit; wiring re-terminated."}' \
    $API/commissioning-tests/b329dbe6-…/evidence
```

Files:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" \
    -F "file=@alarm-log.txt;type=text/plain" \
    -F "note=Head-end log covering the retest window." \
    $API/commissioning-tests/b329dbe6-…/evidence-files
{"id":2,"evidence_object":{"sha256":"fbda7d2bd00af83ab0777eeb0f64a2d7297db8d506d552b377849bf3ca517b64", …}}
```

**Both routes close permanently the moment the result is recorded.** Attach
everything now.

### Step 3 — Record the result, with a witness

The executor cannot witness their own test:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"witness":"priya-eng"}' $API/commissioning-tests/b329dbe6-…/fail
{"status":409,"error_code":"commissioning_test.witness_must_differ",
 "detail":"A commissioning test must be witnessed by someone other than its executor ('priya-eng')."}
```

Nor can somebody who does not hold an account here:

```
$ curl … -d '{"witness":"someone-not-here"}' …/fail
{"status":422,"error_code":"commissioning_test.witness_not_active_user",
 "detail":"The witness must be an active user; 'someone-not-here' is not."}
```

With Marco witnessing:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"witness":"marco-eng",
         "evidence_note":"Simulated overflow still did not raise the alarm at the head end."}' \
    $API/commissioning-tests/b329dbe6-…/fail
```

```
{
  "status": "failed",
  "executed_by": "priya-eng",
  "witnessed_by": "marco-eng",
  "evidence": [
    {"id":5,"note":"Float switch continuity confirmed at the unit; wiring re-terminated.",
     "actor":"priya-eng","recorded_at":"2026-08-13T18:27:19.135868Z"},
    {"id":6,"note":"Simulated overflow still did not raise the alarm at the head end.",
     "actor":"priya-eng","recorded_at":"2026-08-13T18:27:33.333442Z"}
  ], …
}
```

**Four eyes, three.** The result is now permanent — `failed` is terminal, and a
retest is a new test.

### Step 4 — Open the punch item

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"category":"defect","severity":"major",
         "description":"Alarm still does not reach monitoring after re-termination; suspect the head-end point mapping.",
         "due_date":"2026-09-05"}' \
    $API/commissioning-tests/b329dbe6-…/punch-item
```

```
{
  "id": "4bd91116-003f-4d45-9172-e6585d19b5a3",
  "asset_id": "dd7b486d-c66b-471a-9f6e-07bc7145a485",
  "category": "defect", "severity": "major", "status": "open",
  "title": "CRAC unit 2 condensate alarm retest",
  "due_date": "2026-09-05", …
}
```

The test now carries `punch_item_id`, and the title defaulted to the test's —
which names the test rather than the fault. Pass a `title` and avoid that.

### Step 5 — Work it, and have somebody else verify

Closing straight from `open` is refused:

```
{"status":409,"error_code":"punch_item.invalid_transition",
 "detail":"Punch item status cannot change from 'open' to 'closed'; allowed: in_progress."}
```

Priya takes it on:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" $API/punch-items/4bd91116-…/start
{"status":"in_progress","started_by":"priya-eng"}
```

and is now barred from closing it:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"closing_note":"Fixed."}' $API/punch-items/4bd91116-…/close
{"status":409,"error_code":"punch_item.verifier_must_differ",
 "detail":"A punch item must be closed by someone other than whoever started the work ('priya-eng')."}
```

Marco verifies and closes:

```
$ curl -X POST -H "Authorization: Bearer $MARCO" -H "Content-Type: application/json" \
    -d '{"closing_note":"Head-end point remapped and alarm confirmed at the monitoring station."}' \
    $API/punch-items/4bd91116-…/close
{
  "status": "closed",
  "started_by": "priya-eng",
  "verified_by": "marco-eng",
  "closing_note": "Head-end point remapped and alarm confirmed at the monitoring station.", …
}
```

**Four eyes, four.**

### Step 6 — Retest

The defect is closed, but the *test* still says failed and always will. Create a
new commissioning test and run steps 1 to 3 again. The pair of records — failed,
then passed on retest — is the correct commissioning history.

Attach the closeout photograph to the punch item **before** step 5; a closed
item takes no more evidence.

---

## Workflow 3: an incident becomes corrective work

**The job.** The standby generator failed to start on its weekly test.

### Step 1 — Report it

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"asset_id":"6ccedcd6-fa37-4273-b511-b903ebfee630","severity":"high",
         "title":"Generator failed to start on weekly test",
         "description":"Crank attempt timed out; battery charger showing a fault light."}' \
    $API/incidents
{"id":"e1629c64-f721-4d59-b665-a3f35c2bd2de","status":"open",
 "corrective_order_id":null, …}
```

### Step 2 — Start investigating

Required — an incident cannot go straight from `open` to `resolved`:

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" $API/incidents/e1629c64-…/start-investigation
{"status":"investigating"}
```

### Step 3 — Raise the corrective order — before resolving

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"due_date":"2026-08-27",
         "title":"Replace generator start battery and charger",
         "description":"Renew the start battery, replace the faulty charger module, retest under load."}' \
    $API/incidents/e1629c64-…/corrective-order
```

```
{
  "id": "4f6ea665-63cf-4795-8574-8c34fe0ff5c4",
  "order_type": "corrective", "status": "draft",
  "asset_id": "6ccedcd6-fa37-4273-b511-b903ebfee630",
  "title": "Replace generator start battery and charger",
  "due_date": "2026-08-27", …
}
```

One only, and only while the incident is still open or investigating:

```
{"status":409,"error_code":"incident.corrective_order_exists", …}
{"status":409,"error_code":"incident.not_active",
 "detail":"A corrective order can only be created while the incident is open or investigating; this one is 'resolved'."}
```

**Raise the order before you resolve the incident.** Afterwards the link cannot
be made at all.

### Step 4 — Run the corrective order

From here it is an ordinary maintenance order: schedule it, permit it if the
work needs authorization, start it, evidence it, complete it — steps 6 to 14 of
Workflow 1. Check the generator's constraints first, exactly as in step 2.

### Step 5 — Resolve the incident

```
$ curl -X POST -H "Authorization: Bearer $PRIYA" -H "Content-Type: application/json" \
    -d '{"resolution_notes":"Charger module replaced under the corrective order; generator started on retest."}' \
    $API/incidents/e1629c64-…/resolve
resolved | Charger module replaced under the corrective order; generator started on retest.
         | corrective: 4f6ea665-63cf-4795-8574-8c34fe0ff5c4
```

Nothing checks that the corrective order was actually completed. The resolution
notes are your statement; name the order in them, as above.

### What the record shows

```
#104  priya-eng  incident  status_changed            {"status": "resolved", "resolution_notes": …}
#103  priya-eng  incident  corrective_order_created  {"corrective_order_id": "4f6ea665-…"}
#100  priya-eng  incident  status_changed            {"status": "investigating"}
#99   priya-eng  incident  created                   {"id": "e1629c64-…", "asset_id": …}
```

with the matching `created_from_incident` entry on the order, so the link reads
from either end.

---

## The pattern behind all three

1. **Look before you plan.** Constraints, existing orders, and open punch items
   are only visible if you ask.
2. **Get the second person early.** Approvals and issues need someone else and
   there is no queue that tells them; they need to be told.
3. **Evidence before you close.** Every window shuts on the terminal state, for
   good.
4. **Write notes for a stranger.** Completion notes, closing notes, resolution
   notes, and permit scopes are the only "why" the record will ever hold.
5. **Update the asset yourself.** The equipment's status is never inferred from
   the work.

---

[← The web interface](15-web-interface.md) · [Manual index](README.md) · [Next: Error messages explained →](17-error-messages.md)
