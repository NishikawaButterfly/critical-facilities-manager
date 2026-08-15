# 2. Getting started

[← Introduction](01-introduction.md) · [Manual index](README.md) · [Next: Roles and permissions →](03-roles-and-permissions.md)

This chapter takes you from a running service to your first successful
operation. It assumes someone has already installed and started the software —
if that someone is you, the repository
[README](../../README.md#local-development) covers installation, and
[Troubleshooting](18-troubleshooting.md) covers the two ways startup usually
fails.

## 1. Confirm the service is up

The health endpoint is the only one that answers without a token. Use it to
confirm you are talking to the right instance and that it can reach its
database.

```
$ curl http://localhost:8000/api/v1/health
{"status":"ok","version":"0.2.0","database":"ok"}
```

The `version` field is the version this manual documents. If it reads something
else, parts of this manual may not apply.

## 2. Get a token

There is no login page, no password, and no self-registration. Authentication
is a single API token, minted for you by an administrator, and sent with every
request.

Ask your administrator for one. They create it as described in
[Roles and permissions](03-roles-and-permissions.md#creating-a-user); what they
send you is a single opaque string of about 43 characters, like this
(illustrative only — yours will be different, and no token in this manual is
real):

```
EXAMPLEtokenNOTREALdoNotUseThisValueAnywhere
```

Three things to know about it:

- **It is shown once.** Only a hash is stored. If you lose it, nobody can
  recover it — an administrator revokes it and mints you a new one.
- **It is your identity.** Every record you create or change is stamped with
  the username behind this token. Do not share it; a colleague acting under
  your token is indistinguishable from you in the audit trail, and it will
  defeat the four-eyes rules that exist to protect you.
- **It may expire.** Tokens can carry an expiry date. An expired token behaves
  exactly like a wrong one.

### Seeding the demo dataset

If you are evaluating the software rather than operating a real facility, seed
the fictional Camellia Demo Campus. It creates the whole worked example this
manual uses, including four users and one token each:

```
$ python scripts/seed_demo.py
Seeded the demo dataset:
  api_tokens: 4
  assets: 7
  audit_entries: 65
  commissioning_tests: 2
  constraints: 2
  incidents: 2
  locations: 7
  maintenance_orders: 6
  procedures: 4
  punch_items: 2
  site_grants: 4
  users: 4
  work_permits: 2
Demo API tokens, shown once - store them now:
  rowan-admin: <token>
  priya-eng: <token>
  marco-eng: <token>
  vera-viewer: <token>
```

Copy all four tokens before the terminal scrolls. They are shown once, exactly
like real ones. The seed refuses to run twice:

```
$ python scripts/seed_demo.py
error: The database already contains locations or users; refusing to seed on top of existing data.
```

The four demo users, used throughout this manual:

| Username | Name | Role | Typical part in the examples |
|----------|------|------|------------------------------|
| `rowan-admin` | Rowan Ellis | admin | Creates users, mints tokens, manages site grants |
| `priya-eng` | Priya Sharma | engineer | Does the work: raises orders, executes tests, fixes defects |
| `marco-eng` | Marco Alvarez | engineer | Does the second half: approves, issues, witnesses, verifies |
| `vera-viewer` | Vera Lindqvist | viewer | Reads the whole demo estate, changes nothing |

## 3. Open the web interface

Point a browser at the service root. It redirects to the interface:

```
$ curl -i http://localhost:8000/
HTTP/1.1 307 Temporary Redirect
location: /ui/
```

The page opens on the **Asset tree** view with nothing loaded and this line
under the header:

> No token entered. The API answers nothing but /health without one.

### Entering your token

1. Paste the token into the **API token** field at the top right of the page.
2. Press **Use token**.

The identity line under the field changes to name the account and say what it
may do:

> Token belongs to Priya Sharma (priya-eng), role engineer - may run workflow
> actions, subject to its site grants.

If it says something else, go to
[Error messages explained](17-error-messages.md#401-not-authenticated).

The page also tells you where it put the token:

> Kept in this browser's localStorage so a reload keeps it - which also means
> any script on this origin could read it. Use Forget token when you are done.

That is a real caution, not boilerplate. The token survives a reload, and any
script running on that origin can read it. Press **Forget token** when you
finish, especially on a shared machine. In a private window, or wherever the
browser refuses persistent storage, the page falls back to holding the token in
memory for the life of the tab and says so.

### Moving around

Three views, selected by the tabs under the header. Each has its own address,
so you can bookmark one or link a colleague straight to it:

| Tab | Address | What it shows |
|-----|---------|---------------|
| Asset tree | `/ui/#/tree` | The location hierarchy with assets filed under it |
| Order board | `/ui/#/board` | Maintenance orders in columns by status, with the actions each allows |
| Audit timeline | `/ui/#/audit` | The audit trail, newest first, 50 at a time |

There is no menu beyond these three, no search box, and no way to reach a
procedure, permit, incident, commissioning test, punch item, or evidence file
from the page. Each is covered in [The web interface](15-web-interface.md).

## 4. Make your first API call

Everything the page cannot do is done over HTTP. Confirm who your token belongs
to:

```
$ export TOKEN=<your token>
$ export API=http://localhost:8000/api/v1

$ curl -H "Authorization: Bearer $TOKEN" $API/me
{
  "id": "e5d0a9c8-9e8a-4445-b859-e89bc8bc6a0a",
  "username": "priya-eng",
  "display_name": "Priya Sharma",
  "role": "engineer",
  "is_active": true,
  "created_at": "2026-08-13T18:17:49.400415Z",
  "updated_at": "2026-08-13T18:17:49.400419Z"
}
```

The token goes in an `Authorization: Bearer` header on every request. Without
it you get a 401:

```
$ curl $API/me
{
  "type": "https://github.com/NishikawaButterfly/critical-facilities-manager#problem-details",
  "title": "Authentication required",
  "status": 401,
  "detail": "Authentication is required: send an API token as 'Authorization: Bearer <token>'.",
  "instance": "/api/v1/me",
  "error_code": "auth.credentials_required"
}
```

Every failure in this system answers in that shape. `error_code` is the field to
read and to quote when reporting a problem; the full catalogue is in
[Error messages explained](17-error-messages.md).

### Reading a list

List endpoints return an envelope with the page and the total:

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/assets?limit=2&offset=2"
{
  "items": [ ... two assets ... ],
  "total": 7,
  "limit": 2,
  "offset": 2
}
```

`limit` defaults to 50 and may not exceed 200 or fall below 1; `offset`
defaults to 0. Ask for more and the request is rejected:

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/assets?limit=500"
{ ... "status": 422, "error_code": "api.validation_failed",
  "invalid_params": [{"name": "query.limit",
                      "reason": "Input should be less than or equal to 200"}]}
```

Every list endpoint supports filters; they are given in each object's chapter.

### Browsing the API interactively

If the installation leaves them enabled — they are on by default — two
generated reference pages document every endpoint, including request and
response fields:

- `http://localhost:8000/docs` — try requests from the browser
- `http://localhost:8000/redoc` — reference layout

Both need your token entered in their own **Authorize** control. They are
generated from the code, so they are always accurate about *shape*; they say
nothing about the rules, which is what this manual is for.

## Where to go next

- If you are being set up and want to know what you may do:
  [Roles and permissions](03-roles-and-permissions.md).
- If you want to see the system work end to end:
  [Common workflows](16-common-workflows.md).
- If something has already gone wrong:
  [Error messages explained](17-error-messages.md).

---

[← Introduction](01-introduction.md) · [Manual index](README.md) · [Next: Roles and permissions →](03-roles-and-permissions.md)
