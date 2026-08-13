# 7. Evidence storage

[← Token lifecycle](06-token-lifecycle.md) · [Guide index](README.md) · [Next: The web interface →](08-web-interface.md)

Evidence files do not live in the database. They live in a directory whose
layout is derived entirely from the content of the files, and the database
holds rows that point into it by hash. That split is why the two must be backed
up together ([Backups](11-backups.md)), and it is the first thing to understand
about operating this system.

## The layout

`CFM_EVIDENCE_DIR` is the root. Everything under it is created on demand.
Verified, after a single upload into an empty directory:

```
incoming/
objects/
objects/da/
objects/da/27/
objects/da/27/da27eacd0b91f469ac246266101a438ab5d01c440b21ec88fcd07141a18e02c2   (23 bytes)
```

- **`objects/`** holds the files, each at `objects/<first 2>/<next 2>/<full
  hash>`. The name *is* the SHA-256 of the content; there is no index, no
  manifest, and no filename on disk. The two levels of prefix directories keep
  any one directory from growing to hundreds of thousands of entries.
- **`incoming/`** is where an upload streams while it is being hashed. The
  temporary file is flushed and fsynced, then atomically renamed onto its final
  path. A file left here is the debris of a crashed or rejected upload and is
  safe to delete when nothing is uploading.

The stored size equals the file size exactly: a 23-byte upload produced a
23-byte object. There is no container, no compression, and no encryption at
rest.

**The directories are created lazily, at the first write — not at startup.**
Verified: with `CFM_EVIDENCE_DIR` set to a path that did not exist, the
application started, answered `/health` with `"status":"ok"`, and the directory
still did not exist. See [What a broken evidence directory
does](#what-a-broken-evidence-directory-does).

## Write-once, and what that means for you

The path is the hash, so the same bytes always land on the same path, and an
existing object is never rewritten. Two consequences, both verified:

**Identical files are stored once.** The same 23 bytes were attached to a punch
item and then to a maintenance order. Both attachments referenced the same
object id and the same hash, and the object count on disk stayed at 1. The
second upload's declared filename (`copy-of-the-same-file.txt`) was discarded:
the store keeps the declaration made by the **first** upload of that content
(`ups-a-load-bank.txt`). Do not treat the recorded filename as a property of an
attachment; it is a property of the content.

**The same file cannot be attached twice to one target:**

```
{"status":409,"error_code":"evidence.already_attached",
 "detail":"Evidence object 826d748a-… (sha256 da27eacd…) is already attached to this maintenance order."}
```

There is no delete endpoint, anywhere. An attachment cannot be removed, an
object cannot be removed, and the database refuses to do it even from a direct
connection — see [Retention and legal
hold](16-retention-and-legal-hold.md).

## Downloads are verified, not just served

Every download re-hashes the stored bytes and compares them against the
recorded SHA-256 before anything is sent. A healthy download:

```
$ curl -D - -H "Authorization: Bearer $TOKEN" $API/evidence-objects/<id>/content
HTTP/1.1 200 OK
content-disposition: attachment; filename="ups-a-load-bank.txt"; filename*=UTF-8''ups-a-load-bank.txt
x-content-type-options: nosniff
content-type: text/plain; charset=utf-8
```

Overwriting the object on disk with different bytes and downloading again:

```
{"status":500,"error_code":"evidence.corrupted",
 "detail":"Evidence object 826d748a-… cannot be served: Evidence object da27eacd… failed
 verification: its content hashes to 92e78d0b…. The object is corrupted and will not be served."}
```

Deleting the object file instead:

```
{"status":500,"error_code":"evidence.corrupted",
 "detail":"Evidence object 826d748a-… cannot be served: Evidence object da27eacd… is missing from the store."}
```

And with the original bytes restored, the download answered `200` again with
the identical content. Three things follow for an administrator:

1. **`500 evidence.corrupted` is a storage alarm, not a bug report.** It means
   the bytes on disk are not the bytes that were uploaded, or are gone. Causes,
   in the order worth checking: an incomplete restore, `CFM_EVIDENCE_DIR`
   pointing at the wrong directory, a filesystem or disk fault, someone editing
   files in the store.
2. **The metadata endpoint keeps working while the content fails.** Verified:
   `GET /evidence-objects/{id}` returned `200` with the hash, size, filename,
   and uploader for an object whose content was corrupt. The record of *what*
   was attached survives losing the bytes — which is the honest outcome, and
   worth telling the auditor.
3. **Recovery is possible if the bytes exist anywhere else.** The object path
   is a pure function of the content, so restoring the file from a backup to
   the same path makes the download work again with nothing else touched.

## Permissions

The application needs to create directories and files under
`CFM_EVIDENCE_DIR`, and to read them back. Nothing else needs access.

- Give the directory to the service account, and nobody else write access.
- Do not put it on a share where other software prunes, deduplicates, or
  rewrites files. Anything that alters an object's bytes turns it into a
  permanent `500`.
- Backup readers need read access only.

## What a broken evidence directory does

Two failures were produced deliberately. Both share a shape you should know:
**the application starts, `/health` says `ok`, and only an upload fails.**

**A directory the service cannot write to** (write permission denied):

```
HTTP 500
Internal Server Error
```

with this in the service log:

```
PermissionError: [WinError 5] Access is denied: '…\readonly-evidence\incoming'
```

**`CFM_EVIDENCE_DIR` pointing at a file rather than a directory:**

```
HTTP 500
Internal Server Error
```

```
FileNotFoundError: [WinError 3] The system cannot find the path specified: '…\evidence-is-a-file.txt\incoming'
```

Note what the client gets: a bare `Internal Server Error`, **not** a
problem-details body with an `error_code`. Storage-configuration failures escape
the error handling that domain refusals go through, so an operator reporting
"uploads give 500 with no error code" is describing this class of fault. The
diagnosis lives in the service log, not in the response.

Since nothing checks the directory at startup, check it yourself after any
change to `CFM_EVIDENCE_DIR`, a host move, or a restore: attach one small file
to a scratch record and download it again.

## Upload size and what enforces it

`CFM_EVIDENCE_MAX_MB` (default 25) caps one file. Two guards enforce it:

- The request body is rejected before multipart parsing when it exceeds the cap
  plus one mebibyte of framing. Verified with `CFM_EVIDENCE_MAX_MB=1` and a
  3 MiB file:

  ```
  {"status":413,"error_code":"evidence.request_too_large",
   "detail":"The request body exceeds the evidence upload limit of 2097152 bytes
   (the configured file cap plus multipart framing)."}
  ```

  The `incoming/` directory was empty afterwards — the rejection leaves no
  debris.
- The exact per-file cap is enforced while hashing. Verified with the same
  1 MiB cap and a 1.5 MiB file — large enough for the file cap, small enough to
  get past the body cap:

  ```
  {"status":413,"error_code":"evidence.file_too_large",
   "detail":"Evidence file 'between.bin' exceeds the upload limit of 1048576 bytes."}
  ```

  `incoming/` was empty afterwards here too: a partial upload is removed when
  the write is abandoned.

A file just under the cap uploaded normally in the same run. Raising the cap
raises two costs deliberately: the disk it consumes, and the memory a download
buffers, because verification reads the whole object before serving it.

## Sizing the disk

Storage is dominated by the files people attach, and the only mechanism that
reduces it is deduplication of byte-identical content.

```
disk ≈ (attachments per month) × (average file size) × (months retained)
```

with **no upper bound built in**: nothing expires, nothing is deleted, and
`CFM_EVIDENCE_MAX_MB` bounds a single file, not the total. A crew attaching 40
files a month averaging 3 MB accumulates about 1.4 GB per year, and keeps
accumulating, because retention has no mechanism yet
([Retention and legal hold](16-retention-and-legal-hold.md)).

Two practical notes:

- **Orphaned objects are possible and harmless.** A crash between writing the
  bytes and committing the row leaves an object no row references. It is
  invisible to the API and will be reused if the same content is uploaded
  again. There is no garbage collection, and writing one is not safe without
  knowing what the rows reference.
- **Monitor free space on the evidence volume.** A full disk was not simulated
  here, but it fails on the same code path that produced the permission error
  above, so expect the same unstructured `500` with the operating system's
  error in the log.

## Moving the store

The store has no absolute paths inside it: object paths are relative to the
root, and the database records hashes, not paths. Moving it is therefore a copy
and a setting change:

1. Stop the service.
2. Copy the directory, preserving the tree under `objects/`.
3. Point `CFM_EVIDENCE_DIR` at the new location.
4. Start the service and download one known object to confirm.

Step 4 is not optional: a wrong path produces exactly the same `500
evidence.corrupted` as a corrupted file, and you want to learn that now rather
than during an audit.

---

[← Token lifecycle](06-token-lifecycle.md) · [Guide index](README.md) · [Next: The web interface →](08-web-interface.md)
