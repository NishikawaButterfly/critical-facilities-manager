# 13. Evidence

[← Punch items](12-punch-items.md) · [Manual index](README.md) · [Next: The audit trail →](14-audit-trail.md)

Evidence is the files that back up what the record claims: the cell voltage
sheet, the thermal image, the signed permit, the instrument printout. The system
stores each file under the SHA-256 hash of its contents, records who attached it
and to what, and checks the hash again every time the file is served.

## What you can attach evidence to

Four kinds of object, and only while each is still active:

| Attach to | Active while it is | Route |
|-----------|--------------------|-------|
| Maintenance order | `draft`, `scheduled`, `in_progress` | `POST $API/maintenance-orders/{id}/evidence-files` |
| Work permit | `requested`, `issued` | `POST $API/work-permits/{id}/evidence-files` |
| Commissioning test | `pending` | `POST $API/commissioning-tests/{id}/evidence-files` |
| Punch item | `open`, `in_progress` | `POST $API/punch-items/{id}/evidence-files` |

Once the target reaches a terminal state, it takes no more evidence — ever:

```
{"status":409,"error_code":"evidence.target_not_active",
 "detail":"Evidence can only be attached while the maintenance order is active; this one is 'done'."}
```

There is no evidence on assets, locations, incidents, or procedures. An
incident's photographs go on its corrective order; an asset's certificates go on
the commissioning test that produced them.

**Attach as you go, not at the end.** Every one of these deadlines is
irreversible, and the commissioning test's is the tightest: it closes the moment
the result is recorded.

## Uploading a file

Uploading and attaching are one operation — a file cannot be stored without
being attached to something. The request is a multipart form:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" \
    -F "file=@battery-cell-voltages.txt;type=text/plain" \
    -F "note=Cell voltages recorded after the string was replaced." \
    $API/maintenance-orders/af881c0d-29e8-404c-9d1d-692b677dd31a/evidence-files
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
    "uploaded_by": "priya-eng",
    "created_at": "2026-08-13T18:25:37.635450Z"
  },
  "commissioning_test_id": null,
  "punch_item_id": null,
  "order_id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "permit_id": null,
  "note": "Cell voltages recorded after the string was replaced.",
  "attached_by": "priya-eng",
  "attached_at": "2026-08-13T18:25:37.647015Z"
}
```

| Form field | Rules |
|------------|-------|
| `file` | Required. Must have content and a usable filename. |
| `note` | Optional. Why this file evidences this record. Write one. |

**Permission:** the site of whatever you are attaching to — the same authority
as any other write on it.

### The two halves of the response

The response has two layers, and the distinction matters when someone questions
the record later:

- **`evidence_object`** — the stored content. Its `sha256` and `size_bytes` were
  measured by the server as the bytes arrived. These are facts.
- **the attachment** — the act of attaching: which record, by whom, when, with
  what note. Also facts.

`filename` and `content_type` are neither. They are what your client *declared*,
recorded as such. A file named `signed-permit.pdf` proves nothing about its
contents; the hash does.

### Deduplication

Content is addressed by its hash, so uploading identical bytes twice stores them
once. The second upload reuses the same `evidence_object` — including the
filename and content type declared the *first* time — and creates a second
attachment. Both attachments' audit entries record the declaration made in their
own request, so nothing is lost, but the object metadata reflects the first
sighting of those bytes.

Attaching the same content to the *same* record twice is refused:

```
{"status":409,"error_code":"evidence.already_attached",
 "detail":"Evidence object b964f12a-… (sha256 99826de9…) is already attached to this maintenance order."}
```

One file can legitimately evidence several records — the same test certificate
on the order and on the commissioning test — and that is allowed; the bytes are
stored once.

### Size limits

Two limits, with two different messages, both 413:

```
Over the per-file cap (25 MiB by default):
{"status":413,"error_code":"evidence.file_too_large",
 "detail":"Evidence file 'big-25p5.bin' exceeds the upload limit of 26214400 bytes."}

Over the transport limit (the file cap plus 1 MiB of multipart framing):
{"status":413,"error_code":"evidence.request_too_large",
 "detail":"The request body exceeds the evidence upload limit of 27262976 bytes (the configured file cap plus multipart framing)."}
```

The second fires while the body is still arriving, before anything is parsed.
Neither leaves anything behind: a rejected upload stores no bytes.

The cap is configurable per installation. If you routinely need to attach video
walkthroughs, ask for it to be raised deliberately — the download path buffers a
whole object in memory to verify it, and the cap is what bounds that.

### Empty and unnamed files

```
{"status":422,"error_code":"evidence.empty",
 "detail":"An evidence file needs content; the upload was empty."}

{"status":422,"error_code":"evidence.filename_required",
 "detail":"An evidence upload needs a usable declared filename."}
```

The declared filename is reduced to a single safe component: directory parts are
stripped, control characters and quotes removed, length bounded. A name that
reduces to nothing is rejected, because the filename is part of what the audit
trail records.

## Listing what is attached

```
$ curl -H "Authorization: Bearer $TOKEN" \
    $API/maintenance-orders/af881c0d-…/evidence-files
```

Returns a plain list — not a paged envelope — of attachments in the order they
were made, each with its full `evidence_object`. The same endpoint pattern works
for permits, commissioning tests, and punch items.

Reading follows the record the evidence hangs off. Listing the attachments of
an order on a site you hold no grant for answers `404` — the same answer a
missing order gets — and so do the metadata and the content of the object
behind it. An evidence object attached to several records is readable as soon
as **one** of them is a record you may read, which is what keeps deduplicated
bytes usable to everyone entitled to them.

That covers the bytes, the attachments and every direct route to an object.
It does not cover the **declaration**. Deduplication is by content hash across
the whole installation, and the stored `filename`, `content_type`,
`uploaded_by` and `created_at` are the ones recorded at the *first* upload of
those bytes. If a file was first uploaded on another site and the same bytes
are later attached on yours, the object you legitimately read reports that
first upload's declaration — a filename and a username from a site you cannot
otherwise read. The upload response also carries `content_already_stored`,
which reveals that identical bytes already existed somewhere in the
installation. Neither is content and neither grants access, but both are
information crossing the site boundary; see
[Known limitations](19-known-limitations.md). Whether the declaration should
be recorded per attachment instead is an open decision.

## Downloading and verifying

Fetch metadata by object id:

```
$ curl -H "Authorization: Bearer $TOKEN" $API/evidence-objects/b964f12a-…
{
  "id": "b964f12a-0bf4-46d9-a6f0-49b7ffe9df8a",
  "sha256": "99826de98e1da74a25499c65c7f01ae1502ccc1b863d0ed82c7cf14d49938200",
  "size_bytes": 165,
  "filename": "battery-cell-voltages.txt",
  "content_type": "text/plain",
  "uploaded_by": "priya-eng",
  "created_at": "2026-08-13T18:25:37.635450Z"
}
```

Fetch the bytes:

```
$ curl -H "Authorization: Bearer $TOKEN" -O -J \
    $API/evidence-objects/b964f12a-…/content
```

The response headers:

```
content-disposition: attachment; filename="battery-cell-voltages.txt"; filename*=UTF-8''battery-cell-voltages.txt
x-content-type-options: nosniff
content-length: 165
content-type: text/plain; charset=utf-8
```

Always an attachment, never rendered in the browser, and never sniffed — because
the content type is a client declaration and cannot be trusted.

### Verifying it yourself

The server re-hashes every object before it serves it, so a corrupted or missing
file is refused rather than served wrongly:

```
{"status":500,"error_code":"evidence.corrupted",
 "detail":"Evidence object … cannot be served: …"}
```

A 500 here is not a bug report — it is the system refusing to hand you evidence
it can no longer prove. Report it immediately; see
[Troubleshooting](18-troubleshooting.md).

You do not have to take the server's word for it. Hash the file you downloaded
and compare:

```
$ sha256sum battery-cell-voltages.txt
99826de98e1da74a25499c65c7f01ae1502ccc1b863d0ed82c7cf14d49938200
```

That is the value recorded on the object, on the attachment's audit entry, and
in the store's own path. Three independent copies of one number; if they agree,
the bytes in your hands are the bytes that were attached.

## Evidence in the audit trail

Every attachment writes an audit entry against the record it evidences:

```
{
  "id": 84,
  "occurred_at": "2026-08-13T18:25:37.652833Z",
  "actor": "priya-eng",
  "entity_type": "maintenance_order",
  "entity_id": "af881c0d-29e8-404c-9d1d-692b677dd31a",
  "action": "evidence_attached",
  "scope": "installation",
  "before": null,
  "after": {
    "attachment_id": 1,
    "evidence_object_id": "b964f12a-0bf4-46d9-a6f0-49b7ffe9df8a",
    "sha256": "99826de98e1da74a25499c65c7f01ae1502ccc1b863d0ed82c7cf14d49938200",
    "size_bytes": 165,
    "filename": "battery-cell-voltages.txt",
    "content_type": "text/plain",
    "note": "Cell voltages recorded after the string was replaced.",
    "content_already_stored": false
  }
}
```

`content_already_stored` says whether these bytes were already in the store —
`true` means this upload deduplicated onto content that arrived earlier. One
consequence is worth stating: the object's `filename` and `uploaded_by` are the
declaration made by whoever uploaded the bytes *first*, which on a
multi-site installation may be somebody on a site you cannot read. Your own
attachment's audit entry records the declaration your request made.

Because the hash is in the audit trail, and the audit trail cannot be altered,
the link between "this record" and "these exact bytes" is fixed at the moment of
attachment.

## What cannot be done

- **Nothing can be deleted.** There is no delete endpoint for an attachment or
  an object, anywhere. A file attached to the wrong record stays attached to the
  wrong record; the correction is a note on the right one.
- **Nothing can be replaced.** Objects are write-once. A superseded document is
  a new upload alongside the old one.
- **Notes cannot be edited.** The attachment note is fixed at upload.
- **There is no way to browse evidence.** No gallery, no "all evidence for this
  asset", no search by filename. You reach a file through the record it is
  attached to, or by its object id. Nothing in the web interface shows evidence
  at all.

This is the same discipline as the [audit trail](14-audit-trail.md), for the
same reason: evidence that could be swapped out later would not be evidence.

---

[← Punch items](12-punch-items.md) · [Manual index](README.md) · [Next: The audit trail →](14-audit-trail.md)
