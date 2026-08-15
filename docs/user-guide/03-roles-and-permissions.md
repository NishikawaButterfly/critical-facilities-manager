# 3. Roles and permissions

[← Getting started](02-getting-started.md) · [Manual index](README.md) · [Next: Sites and locations →](04-sites-and-locations.md)

Permission in this system has two independent halves:

- **Your role** says *what kind of thing* you may do.
- **Your site grants** say *where* you may do it.

Both must allow an operation, and both apply to reading as well as to writing.
Your role decides whether you may read at all; your grants decide which sites'
records you read.

## The three roles

| Role | May read | May run workflow operations | May manage users, tokens, and grants |
|------|:--------:|:---------------------------:|:------------------------------------:|
| `viewer` | yes | no | no |
| `engineer` | yes | yes | no |
| `admin` | yes | yes | yes |

"May read" in that table means *may read within your grants*. No role, `admin`
included, reads a site it holds no grant on — an administrator who needs the
whole estate holds an installation-wide grant, which is the default every new
account gets.

That is a scoping rule, not a control over administrators. User, token and
grant administration stays installation-wide: any admin may change any
account's grants, including their own coverage, under the existing
administrative policy. So an admin whose grants cover one site reads one site
*until they change that* — and the trail records the change. Grants bound what
a token reaches; they are not a barrier against someone who may edit grants.

There is no fourth role, no per-object permission, and no way to grant one
engineer the right to approve procedures but not to issue permits. If you can
write at all, you can run every workflow operation your grants reach.

A role cannot be changed after the account is created. To move someone from
viewer to engineer, an administrator deactivates the account and creates a new
one — which is deliberate: the audit trail must stay readable, and a username
that meant "viewer" last year must not silently become something else.

### What a viewer sees

A viewer reads every kind of record — assets, orders, procedures, permits,
incidents, tests, punch items, evidence files, audit entries — on the sites
their grants cover, and nothing at all on the sites they do not. What they
cannot do is change anything:

```
$ curl -X POST -H "Authorization: Bearer $VIEWER_TOKEN" -H "Content-Type: application/json" \
    -d '{"order_type":"preventive","asset_id":"4cd1ad3e-...","title":"x","due_date":"2026-12-01"}' \
    $API/maintenance-orders
{
  "title": "Permission denied",
  "status": 403,
  "detail": "Role 'viewer' may only read; POST requires an engineer or admin.",
  "error_code": "auth.forbidden"
}
```

The [order board](15-web-interface.md#order-board) recognises a viewer token and
offers no action buttons at all, with the reason stated:

> **This token may read but not write.** Its role is not engineer or admin, so
> the API would refuse every workflow action with 403. No actions are offered
> rather than offering ones that cannot succeed.

### What an engineer may do

Everything in chapters 4 to 14: locations, assets, orders, procedures, permits,
constraints, incidents, commissioning tests, punch items, evidence. Subject to
site grants, and subject to the four-eyes rules, which are not about roles —
two engineers are needed to approve a procedure or issue a permit, and an admin
is no substitute for the second engineer.

An engineer cannot manage accounts. The whole `/users` tree is closed to them:

```
$ curl -H "Authorization: Bearer $ENGINEER_TOKEN" $API/users
{
  "title": "Permission denied",
  "status": 403,
  "detail": "User and token management requires the admin role.",
  "error_code": "auth.forbidden"
}
```

### What an admin adds

Creating users, minting and revoking tokens, granting and removing site
access, and deactivating accounts. An admin also has every engineer power.

## Site grants: where your authority applies

Your role says you may act. Your grants say where. Every operation resolves the
site its target belongs to: a write requires a grant covering that site, and a
read only ever returns records on sites your grants cover.

How each object resolves to a site:

| Object | Site it belongs to |
|--------|--------------------|
| Location (building, floor, room) | Walk up the tree to the site above it |
| Asset | Recorded on the asset when it is created or moved |
| Maintenance order, incident, commissioning test | The site of its asset |
| Work permit | The site of its order's asset |
| Punch item | The site of its asset, or of its location |
| Evidence upload | The site of whatever it attaches to |

Three kinds of object belong to no single site — **sites themselves**,
**procedures**, and **constraints**. Writing those requires an
*installation-wide* grant, not a grant on any particular site. A procedure is
one document used across the estate; a constraint may span sites; creating or
deleting a site changes what "scoped" means. None of them can be owned by one
site, so none of them can be written with only a site-scoped grant.

Reading them is the opposite: because they belong to no site, no site grant can
withhold them, and any token reads them. That is deliberate rather than an
oversight. The engineer whose order was refused by a constraint has to be able
to read the constraint that refused it, and the technician working under a
permit has to be able to read the procedure it names.

What *is* withheld from a cross-site constraint is its **structured
site-owned part**: the member assets are listed only where your grants cover
them, so you do not receive the identifiers of the members on the other site.
Its **free text is not withheld**. A constraint's `name` and `description` are
unstructured fields any authenticated token reads in full, and whoever wrote
them may have named the other site, its equipment, or its people in them —
nothing checks that, and nothing redacts it. The same is true of a procedure's
title and steps.

A cross-site constraint can also disclose through a **refusal**. When it blocks
your order, the 409 names the constraint and the conflicting work so the
refusal is actionable — and that conflicting work may be on the other site.
[Known limitations](19-known-limitations.md) lists exactly what that message
contains.

### The two grant kinds

| Grant | `site_id` | Covers |
|-------|-----------|--------|
| Installation-wide | `null` | Every site, present and future, plus the site-less objects above |
| Site | a site's id | That site and everything under it — buildings, floors, rooms, and all their assets and work |

A user may hold several grants. Holding an installation-wide grant makes every
site grant redundant.

### Watching it work

The examples below use a temporary engineer account with the login name
`contractor-temp`. It starts with the default installation-wide grant:

```
$ curl -H "Authorization: Bearer $ADMIN_TOKEN" $API/users/1592e418-.../site-grants
{
  "items": [
    {
      "id": "a18b3679-8146-45e9-8ba6-f41388a7a737",
      "user_id": "1592e418-cc75-4d75-a4cd-f47e89e2c416",
      "site_id": null,
      "scope": "installation",
      "created_at": "2026-08-13T18:22:42.040070Z"
    }
  ],
  "total": 1, "limit": 50, "offset": 0
}
```

With that grant, a write on the demo campus succeeds. Remove the grant:

```
$ curl -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
    $API/users/1592e418-.../site-grants/a18b3679-8146-45e9-8ba6-f41388a7a737
(204 No Content)
```

and the same write is refused — with the role untouched:

```
$ curl -X POST -H "Authorization: Bearer $CONTRACTOR_TOKEN" -H "Content-Type: application/json" \
    -d '{"asset_id":"c6713d06-...","severity":"low","title":"Scope demonstration incident"}' \
    $API/incidents
{
  "title": "Permission denied",
  "status": 403,
  "detail": "User 'contractor-temp' holds no site grant covering site 6eddaddd-e6cf-4ca8-bdf3-c9f1921587cf; this write requires a grant on that site or an installation-wide grant.",
  "error_code": "auth.scope_forbidden"
}
```

Note the error code: `auth.scope_forbidden`, not `auth.forbidden`. The two 403s
mean different things and call for different fixes — see
[Error messages explained](17-error-messages.md#403-refused).

Reads go the same way. With no grants, the inventory is not merely unwritable,
it is empty — and the count agrees, because the total describes what you may
read, not what exists:

```
$ curl -H "Authorization: Bearer $CONTRACTOR_TOKEN" "$API/assets?limit=1"
{"items":[], "total":0, "limit":1, "offset":0}
```

Asking for one of those assets by id gets the same answer a made-up id gets:

```
$ curl -H "Authorization: Bearer $CONTRACTOR_TOKEN" $API/assets/4cd1ad3e-...
{
  "title": "Resource not found",
  "status": 404,
  "detail": "Asset 4cd1ad3e-... was not found.",
  "error_code": "asset.not_found"
}
```

That is on purpose, and it is the point of the whole chapter: a record you have
no grant for must not be distinguishable from a record that does not exist. A
403 saying "that asset is on a site you cannot see" would confirm the asset —
and on a shared installation, the existence of another client's equipment is
itself something they are paying you not to disclose.

Grant the campus site only:

```
$ curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
    -d '{"site_id":"6eddaddd-e6cf-4ca8-bdf3-c9f1921587cf"}' \
    $API/users/1592e418-.../site-grants
{
  "id": "f0aca07c-e36d-4a6f-bf7e-b5bc6784b323",
  "user_id": "1592e418-cc75-4d75-a4cd-f47e89e2c416",
  "site_id": "6eddaddd-e6cf-4ca8-bdf3-c9f1921587cf",
  "scope": "site:6eddaddd-e6cf-4ca8-bdf3-c9f1921587cf",
  "created_at": "2026-08-13T18:23:09.101535Z"
}
```

The incident now succeeds, and so does the read: the campus inventory appears,
counted to the campus. Creating a procedure still does not work, because a
procedure belongs to no site:

```
{
  "status": 403,
  "detail": "User 'contractor-temp' holds no installation-wide grant; writing objects that belong to no single site requires one.",
  "error_code": "auth.scope_forbidden"
}
```

Reading that same procedure works, as it must for anyone whose work refers to
it.

That is the whole mechanism.

### What the scope does not hide

Site scoping is a read boundary over site-owned domain records. It is not a
guarantee that a token cannot learn another site exists. Four things stay
outside it, and all four are deliberate:

- **Write-side probing.** A user who may write at all — an engineer or an
  admin — can tell an out-of-scope id from a made-up one by *attempting a
  write* on it: a real record on another site answers
  `403 auth.scope_forbidden`, an invented id answers 404. That refusal is
  deliberately explanatory, because it is what tells an engineer who mistyped
  an id, or whose grants were narrowed this morning, what actually happened. A
  `viewer` never reaches the distinction, because the role gate refuses the
  write first — but that closes this one channel, not the three below.
- **Installation-scoped free text.** Procedure titles and steps, and
  constraint names and descriptions, are readable by every authenticated token
  and may name another site, its equipment or its people. No role restricts
  them and nothing redacts them.
- **Cross-site constraint refusals.** A 409 from a constraint names the
  conflicting work, which may live on a site you cannot read. See
  [Known limitations](19-known-limitations.md).
- **Evidence declaration metadata.** When identical bytes are uploaded on two
  sites, the stored declaration is the one from the first upload. See
  [Evidence](13-evidence.md).

So: reads of site-owned records are scoped, and a token is not told what it
may not read. If your requirement is that an account must not be able to learn
that another site exists at all, this release does not provide it — a separate
installation does.

## Administration tasks

All of these require an admin token. **None of them has a screen** — the web
interface has no administration area whatsoever.

### Creating a user

```
$ curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
    -d '{"username":"contractor-temp","display_name":"Contractor Account","role":"engineer"}' \
    $API/users
{
  "id": "1592e418-cc75-4d75-a4cd-f47e89e2c416",
  "username": "contractor-temp",
  "display_name": "Contractor Account",
  "role": "engineer",
  "is_active": true,
  "created_at": "2026-08-13T18:22:42.035992Z",
  "updated_at": "2026-08-13T18:22:42.035996Z"
}
```

| Field | Rules |
|-------|-------|
| `username` | Required. Lowercase letters, digits, and inner hyphens; 1–64 characters. Must be unique, including against deactivated accounts. |
| `display_name` | Required, 1–255 characters. |
| `role` | Required: `viewer`, `engineer`, or `admin`. |
| `site_ids` | Optional list of site ids. Omit it for one installation-wide grant — the default. Supplying it creates one grant per site instead. |

Supplying `site_ids` at creation time is the tidiest way to scope somebody,
because the grants then appear inside the user's own "created" audit entry
rather than as separate entries.

Two refusals worth recognising:

```
{"status":422,"error_code":"api.validation_failed",
 "invalid_params":[{"name":"body.username",
                    "reason":"String should match pattern '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$'"}]}

{"status":409,"error_code":"user.username_taken",
 "detail":"Username 'priya-eng' is already taken."}
```

### Minting a token

```
$ curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
    -d '{"label":"manual-demo"}' $API/users/1592e418-.../tokens
{
  "id": "ba20fb38-41d7-4a2f-92e6-700788f26990",
  "label": "manual-demo",
  "expires_at": null,
  "revoked": false,
  "created_at": "2026-08-13T18:22:51.367796Z",
  "token": "EXAMPLEtokenNOTREALdoNotUseThisValueAnywhere"
}
```

The `token` field appears in this one response and nowhere else, ever. Send it
to its owner over a channel you trust, and do not keep a copy.

`label` (required, 1–128 characters) is how you will tell one token from another
later — "laptop", "ci-pipeline", "handover-2026". `expires_at` is optional and
must be in the future; a naive timestamp is read as UTC.

```
{"status":422,"error_code":"api_token.expiry_not_future",
 "detail":"The token expiry must lie in the future."}
```

A user must be active to receive a token:

```
{"status":422,"error_code":"api_token.user_inactive",
 "detail":"User 'contractor-temp' is inactive; inactive users cannot receive tokens."}
```

### Listing and revoking tokens

`GET $API/users/{user_id}/tokens` lists a user's tokens as metadata only —
label, expiry, revoked flag, creation time. The secret is never in it.

```
$ curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
    $API/users/1592e418-.../tokens/ba20fb38-.../revoke
{
  "id": "ba20fb38-41d7-4a2f-92e6-700788f26990",
  "label": "manual-demo",
  "expires_at": null,
  "revoked": true,
  "created_at": "2026-08-13T18:22:51.367796Z"
}
```

Revocation is immediate and permanent — there is no un-revoke, and a second
attempt is refused with `api_token.already_revoked`. The revoked token now
behaves like any other bad token:

```
{"status":401,"error_code":"auth.invalid_token",
 "detail":"The API token is unknown, revoked, expired, or belongs to an inactive user."}
```

That single message covers unknown, revoked, expired, and inactive-user on
purpose: an attacker probing tokens learns nothing from it. It also means *you*
learn nothing from it — if a token stops working, ask an administrator to look
at the token list rather than guessing.

### Deactivating a user

```
$ curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" $API/users/1592e418-.../deactivate
{ ... "is_active": false ... }
```

Users are never deleted, so every username in the audit trail keeps resolving.
Deactivation stops all of the account's tokens working at once — they are not
individually revoked, but authentication rejects an inactive user's tokens.

The last active admin cannot be deactivated:

```
{"status":409,"error_code":"user.last_active_admin",
 "detail":"User 'rowan-admin' is the last active admin and cannot be deactivated."}
```

Create the replacement admin first.

### The first admin

A brand-new installation has no users, so nobody can call any endpoint. One
command-line script breaks that circle:

```
$ python scripts/bootstrap_admin.py rowan-admin --display-name "Rowan Ellis"
```

It creates one admin and prints one token, once, and refuses to run if the
database already has users. Everything after that is done through the API.

## Rate limiting

Failed authentications are counted per network source. After 10 failures within
60 seconds, that source is locked out for 5 minutes and every request from it —
including ones carrying a perfectly good token — is answered 429 until the
lockout expires. Requests without any credentials do not count. See
[Error messages explained](17-error-messages.md#429-rate-limited).

The three numbers are configurable per installation; the defaults are above.

---

[← Getting started](02-getting-started.md) · [Manual index](README.md) · [Next: Sites and locations →](04-sites-and-locations.md)
