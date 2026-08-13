# 17. Error messages explained

[← Common workflows](16-common-workflows.md) · [Manual index](README.md) · [Next: Troubleshooting →](18-troubleshooting.md)

Every refusal in this system answers in the same shape, and the field to read
is `error_code`.

## The shape of a refusal

```
{
  "type": "https://github.com/NishikawaButterfly/critical-facilities-manager#problem-details",
  "title": "Permission denied",
  "status": 403,
  "detail": "Role 'viewer' may only read; POST requires an engineer or admin.",
  "instance": "/api/v1/maintenance-orders",
  "error_code": "auth.forbidden"
}
```

| Field | What to do with it |
|-------|--------------------|
| `status` | The broad category — the sections below |
| `detail` | Written for you. Read it; it usually names the exact obstacle. |
| `error_code` | The precise cause. **Quote this when asking for help.** |
| `instance` | Which endpoint answered |
| `invalid_params` | Present on validation failures: which field, and why |
| `title`, `type` | Fixed strings. Ignore them. |

The [web interface](15-web-interface.md) shows the same information: a heading
naming the status, the `detail` verbatim underneath, and the `error_code` below
that.

---

## 401: not authenticated

Something is wrong with **the token itself**. Nothing to do with permissions.

| `error_code` | Means | Do this |
|--------------|-------|---------|
| `auth.credentials_required` | No `Authorization` header was sent | Send the token. In the browser, paste it into the API token field. |
| `auth.invalid_token` | The token was rejected | See below |

```
{"status":401,"error_code":"auth.invalid_token",
 "detail":"The API token is unknown, revoked, expired, or belongs to an inactive user."}
```

That message lists four causes and deliberately will not say which applies — an
attacker probing tokens must learn nothing, and that means you learn nothing
either. Check in this order:

1. **Copied wrongly?** A truncated or space-padded token looks identical to a
   wrong one. Re-copy it.
2. **Right instance?** A token from a test system is unknown to production.
3. **Revoked or expired?** Ask an administrator to list your tokens
   (`GET $API/users/{user_id}/tokens` shows the label, expiry, and revoked flag).
4. **Account deactivated?** Every token of a deactivated user stops working at
   once, without any of them being revoked.

You cannot resolve this yourself if the token is genuinely dead; only an
administrator can mint a new one.

Every 401 carries a `WWW-Authenticate: Bearer` header.

---

## 403: refused

The token is **valid and accepted**; the request is not allowed. Two quite
different causes, and the code tells them apart.

### `auth.forbidden`: your role

```
{"detail":"Role 'viewer' may only read; POST requires an engineer or admin."}
{"detail":"User and token management requires the admin role."}
```

Your role does not permit this kind of operation at all. Nothing you do will
change it: roles cannot be edited, so an administrator must create a new account
at the right role.

### `auth.scope_forbidden`: your site grants

```
{"detail":"User 'contractor-temp' holds no site grant covering site 6eddaddd-…; this write requires a grant on that site or an installation-wide grant."}

{"detail":"User 'contractor-temp' holds no installation-wide grant; writing objects that belong to no single site requires one."}
```

Your role permits the operation, but not *there*. The first message names the
site you need a grant on. The second means you tried to write a **procedure, a
constraint, or a site** — objects that belong to no single site and require an
installation-wide grant.

Ask an administrator for the grant, naming the site id from the message.

**Why the interface offered an action it could not do:** the page can read your
role but not your grants — only an admin token can read those. So it offers what
your role permits and lets the server refuse the rest. That is by design; see
[The web interface](15-web-interface.md#the-header).

---

## 404: not found

The thing the URL addressed does not exist.

| `error_code` | The missing thing |
|--------------|-------------------|
| `asset.not_found`, `location.not_found`, `maintenance_order.not_found`, `procedure.not_found`, `work_permit.not_found`, `incident.not_found`, `commissioning_test.not_found`, `punch_item.not_found`, `constraint.not_found`, `evidence.not_found`, `user.not_found` | That record |
| `api_token.not_found`, `site_grant.not_found` | That token or grant, **for that user** |
| `api.http_error` with `"detail":"Not Found"` | No such endpoint |

```
{"status":404,"error_code":"maintenance_order.not_found",
 "detail":"Maintenance order 00000000-0000-0000-0000-000000000000 was not found."}
```

Usually a mistyped or stale identifier. Note that tokens and site grants are
looked up *within* a user: the right grant id under the wrong user id gives
`site_grant.not_found`, not a permission error.

A missing record referenced in a request **body** rather than the URL answers
**422**, not 404 — see below.

`api.http_error` at 404 means the address itself is wrong. Check the method and
the path; a `PATCH` where only `POST` exists gives 405 with the same code.

---

## 409: conflicts with the current state

The request was well-formed and you were allowed to make it. The record is not
in a state that permits it. **This is the most common refusal in normal use, and
it is usually the system working correctly.**

### Wrong state for this transition

Every object's transition table produces the same shape, naming what *is*
allowed:

```
{"error_code":"maintenance_order.invalid_transition",
 "detail":"Maintenance order status cannot change from 'scheduled' to 'done'; allowed: cancelled, in_progress."}

{"error_code":"punch_item.invalid_transition",
 "detail":"Punch item status cannot change from 'open' to 'closed'; allowed: in_progress."}

{"error_code":"incident.invalid_transition",
 "detail":"Incident status cannot change from 'open' to 'resolved'; allowed: investigating."}
```

`allowed: none` means the record is terminal and will never change again.

The codes: `asset.invalid_transition`, `maintenance_order.invalid_transition`,
`procedure.invalid_transition`, `work_permit.invalid_transition`,
`incident.invalid_transition`, `punch_item.invalid_transition`,
`commissioning_test.invalid_transition`, `constraint.invalid_transition`.

### Separation of duties

Four codes, one rule: you cannot do both halves.

| `error_code` | Who is blocked |
|--------------|----------------|
| `procedure.approver_must_differ` | Whoever last edited the draft |
| `work_permit.issuer_must_differ` | Whoever requested the permit |
| `commissioning_test.witness_must_differ` | Whoever is recording the result |
| `punch_item.verifier_must_differ` | Whoever started the work |

**Get a colleague.** There is no override, no supervisor bypass, and an admin
token is no substitute — the check is on the username, not the role.

### The redundancy constraint

```
{"error_code":"maintenance_order.mutual_exclusion_conflict",
 "detail":"Constraint 'UPS redundancy pair' (586b0568-…) allows only one of its member assets under maintenance at a time; order af881c0d-… ('UPS 1 battery string replacement') is already in progress on asset 4cd1ad3e-…."}
```

**Do not work around this.** It exists to stop both halves of a redundant pair
being out at once. Find the named order, and either wait for it, complete it, or
cancel it. See [Operational constraints](09-operational-constraints.md).

### Open work blocking closure

```
{"error_code":"maintenance_order.open_permit",
 "detail":"The maintenance order still has an issued work permit open; close or revoke the permit before completing the order."}
```

Close the permit with its completion note (or revoke it), then complete the
order.

### Already in that state, or already linked

| `error_code` | Means |
|--------------|-------|
| `incident.corrective_order_exists` | This incident already has its one corrective order |
| `commissioning_test.punch_item_exists` | This test already has its one punch item |
| `procedure.successor_exists` | This version already has a successor |
| `api_token.already_revoked` | Already revoked |
| `user.already_inactive` | Already deactivated |
| `evidence.already_attached` | Those exact bytes are already on this record |
| `site_grant.duplicate` | That user already holds that grant |
| `user.username_taken` | Pick another username |

Mostly harmless — the outcome you wanted already holds. The exception is
`evidence.already_attached`, which may mean you uploaded the wrong file (an
identical one) rather than the intended one.

### Editing something that has moved on

| `error_code` | Means |
|--------------|-------|
| `maintenance_order.not_editable` | Editable only while `draft` or `scheduled` |
| `procedure.not_editable` | Editable only while `draft` — start a new version |
| `commissioning_test.not_pending` | Evidence notes only while `pending` |
| `evidence.target_not_active` | The record has reached a terminal state and takes no more files |
| `commissioning_test.not_failed` | A punch item can only come from a failed test |
| `incident.not_active` | A corrective order can only come from an open or investigating incident |
| `procedure.not_approved` | Only an approved procedure can start a new version |

### Something still depends on it

| `error_code` | Means |
|--------------|-------|
| `location.has_children` | Delete the child locations first |
| `location.has_assets` | Move or delete the assets first |
| `location.has_site_grants` | Remove the grants that name this site first |
| `asset.duplicate_tag` | That tag is already used in this site |
| `location.duplicate_code` | That code is already used under this parent |
| `user.last_active_admin` | Create another admin before deactivating this one |

---

## 413: the upload is too large

Two limits, two codes.

```
{"error_code":"evidence.file_too_large",
 "detail":"Evidence file 'big-25p5.bin' exceeds the upload limit of 26214400 bytes."}
```

The file is over the per-file cap — 25 MiB by default, 26214400 bytes.

```
{"error_code":"evidence.request_too_large",
 "detail":"The request body exceeds the evidence upload limit of 27262976 bytes (the configured file cap plus multipart framing)."}
```

The whole request body is over the transport limit; this one is refused while
the body is still arriving.

Either way: reduce the file. Compress a photograph, export a PDF at a lower
resolution, or split a long report. Nothing is stored by a rejected upload. If
your installation genuinely needs larger files, the cap is configurable —
raising it also raises what the server buffers when serving a download, so it is
a deliberate decision, not a formality.

---

## 422: the request broke a rule

Two quite different things share this status.

### `api.validation_failed`: the payload is malformed

The field is wrong before any domain rule is consulted, and `invalid_params`
names each problem:

```
{
  "status": 422,
  "error_code": "api.validation_failed",
  "detail": "One or more request fields are invalid.",
  "invalid_params": [
    {"name": "body.title", "reason": "String should have at least 1 character"},
    {"name": "body.due_date", "reason": "Field required"}
  ]
}
```

Common causes:

| `reason` | Cause |
|----------|-------|
| `Field required` | A required field is missing |
| `Input should be 'preventive' or 'corrective'` | An invalid enum value — the message lists the valid ones |
| `String should have at least 1 character` | An empty string where text is required |
| `Extra inputs are not permitted` | A field this endpoint does not accept. **Usually a typo in a field name** — the request bodies reject anything unrecognised rather than ignoring it. |
| `Input should be less than or equal to 200` | `limit` above 200 |

### Everything else: a domain rule

The payload was well-formed; what it asked for is not allowed.

**A referenced record does not exist.** These are 422 rather than 404 because
the missing thing was a value in your body, not the resource the URL addressed:

`maintenance_order.asset_not_found`, `incident.asset_not_found`,
`commissioning_test.asset_not_found`, `punch_item.asset_not_found`,
`punch_item.location_not_found`, `asset.location_not_found`,
`constraint.asset_not_found`, `work_permit.order_not_found`,
`work_permit.procedure_not_found`, `commissioning_test.procedure_not_found`,
`location.parent_not_found`, `site_grant.site_not_found`.

**A required note is blank.** Whitespace does not count:

`maintenance_order.completion_notes_required`,
`work_permit.completion_note_required`, `punch_item.closing_note_required`,
`incident.resolution_notes_required`,
`commissioning_test.evidence_note_required`,
`commissioning_test.witness_required`, `procedure.step_text_required`.

**The referenced thing is in the wrong state:**

| `error_code` | Means |
|--------------|-------|
| `work_permit.order_not_active` | Schedule the order before requesting a permit |
| `work_permit.procedure_not_approved` | Only approved procedures may be named on a permit |
| `commissioning_test.procedure_not_approved` | Same, for tests |
| `commissioning_test.witness_not_active_user` | The witness must hold an active account here |
| `api_token.user_inactive`, `site_grant.user_inactive` | Inactive users take no tokens or grants |
| `api_token.expiry_not_future` | The expiry must be in the future |

**The shape is wrong for this kind of object:**

| `error_code` | Means |
|--------------|-------|
| `punch_item.exactly_one_scope` | Give an asset *or* a location, never both, never neither |
| `location.parent_required`, `location.invalid_parent_kind` | The hierarchy is strict: site → building → floor → room |
| `constraint.too_few_members` | An enforced constraint needs at least two assets |
| `constraint.description_required` | An advisory constraint needs its guidance text |
| `constraint.duplicate_member` | Members must be distinct |
| `site_grant.not_a_site` | A grant must name a site, not a building or floor |
| `user.username_invalid` | Lowercase letters, digits, and inner hyphens |
| `evidence.empty` | The file had no content |
| `evidence.filename_required` | The filename reduced to nothing after being made safe |

---

## 429: rate limited

```
HTTP/1.1 429 Too Many Requests
retry-after: 300

{"status":429,"error_code":"auth.rate_limited",
 "detail":"Too many failed authentication attempts from this source; retry after the number of seconds given in the Retry-After header."}
```

Too many **failed authentications** came from your network address: 10 within 60
seconds by default, locking the source for 5 minutes.

While locked, **every request from that address is refused, including ones
carrying a perfectly valid token.** It is the source that is locked, not the
account — so a colleague behind the same office address is locked out too, by a
mistake neither of them made.

What to do:

1. **Wait** the number of seconds in `Retry-After`. It is exact.
2. **Do not retry in a loop.** Failed attempts during the lockout do not extend
   it, but they do not help either.
3. **Find the cause before retrying.** A locked source usually means a script,
   a browser tab, or a scheduled job somewhere is retrying a dead token.
4. **Fix the token first**, then try once.

Requests carrying no credentials at all never count toward the limit, and
`/health` answers throughout — so a monitoring probe on `/health` will not lock
you out, and will keep working if something else does.

---

## 500: evidence failed verification

One 500 in this system is deliberate and means something specific:

```
{"status":500,"error_code":"evidence.corrupted",
 "detail":"Evidence object … cannot be served: …"}
```

The stored bytes no longer hash to the value recorded when the file was
attached, or the file is missing from the store. The server is refusing to hand
you evidence it cannot prove — which is the correct behaviour, and much better
than serving wrong bytes with a success status.

**Report this immediately.** It means bit rot, a restore gone wrong, a
misconfigured evidence directory, or tampering. See
[Troubleshooting](18-troubleshooting.md).

Any other 500 is a fault, not a rule. Note what you were doing and report it.

---

## Quick reference

| Status | One line | First move |
|--------|----------|------------|
| 401 | The token is not accepted | Re-copy it; then ask an administrator |
| 403 `auth.forbidden` | Your role may not do this | You need a different account |
| 403 `auth.scope_forbidden` | Your grants do not reach there | Ask for a grant on the named site |
| 404 | No such record or endpoint | Check the id and the path |
| 409 | The record is in the wrong state | Read `detail` — it names what is allowed |
| 413 | The upload is too big | Make the file smaller |
| 422 `api.validation_failed` | The payload is malformed | Read `invalid_params` |
| 422 (anything else) | A domain rule refused it | Read `detail` — it names the rule |
| 429 | Too many failed logins from here | Wait `Retry-After`; find what is retrying |
| 500 `evidence.corrupted` | Stored evidence failed its hash check | Report it now |

---

[← Common workflows](16-common-workflows.md) · [Manual index](README.md) · [Next: Troubleshooting →](18-troubleshooting.md)
