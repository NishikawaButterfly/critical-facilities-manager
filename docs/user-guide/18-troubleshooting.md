# 18. Troubleshooting

[← Error messages explained](17-error-messages.md) · [Manual index](README.md) · [Next: Known limitations →](19-known-limitations.md)

This chapter is for when something does not behave the way the rest of the
manual says. For a refusal with a status code and an `error_code`, go to
[Error messages explained](17-error-messages.md) instead — that is normal
operation, not a fault.

## Start here

Two commands answer most questions about whether the problem is yours or the
system's:

```
$ curl http://localhost:8000/api/v1/health
{"status":"ok","version":"0.2.0","database":"ok"}

$ curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/me
{"username":"priya-eng","role":"engineer","is_active":true, …}
```

- `/health` fails or does not answer → the service is down or you have the
  wrong address. Talk to whoever runs it.
- `/health` is fine, `/me` gives 401 → the problem is your token
  ([401](17-error-messages.md#401--not-authenticated)).
- Both fine → the service and your token are both good; the problem is with the
  specific operation.

`/health` needs no token, so it works even when everything else refuses you.

## The web interface

### The page is blank, or says it could not start

> **The frontend could not start** — The page could not load its own
> configuration (404).

The page fetches its settings before doing anything. This failure means it is
being served from somewhere the API is not — a copied file, a proxy rewriting
paths, or a stale bookmark. Go to the service root and follow the redirect to
`/ui/` rather than typing the path.

If the browser console complains about a script's media type, the installation
is serving JavaScript wrongly; that is an operator problem, not a browser
setting.

### "No token entered" although a token was pasted

Press **Use token**. Typing into the field does nothing until it is submitted.

If it still says so after pressing it, the browser is refusing to keep the
token. The help text under the field says which mode it is in — if it reads
*"This browser refused persistent storage…"* the token is held in memory only,
and closing the tab loses it. That is normal in a private window.

### "The API could not be reached"

> The request never got a response. Check that the API is running and reload.

The page could not reach the service at all — it is stopped, the address is
wrong, or something between you and it is blocking the request. Check `/health`
in the same browser: if that fails too, the service is the problem.

### A button I expected is not there

Almost always intended. Work through:

1. **Is the order in the right state?** Cards in Done and Cancelled offer
   nothing, and say so.
2. **Is your token a viewer?** The board shows a banner and offers no actions at
   all.
3. **Is it an operation the interface simply does not have?** Most of them are.
   The interface can only move maintenance orders between states — nothing else,
   for any object. See [The web interface](15-web-interface.md).

### The view shows stale data

Nothing refreshes on its own and nothing polls. Reload the page to see changes
made elsewhere. After running an action, the board updates only the card that
changed.

### A warning about locations or assets that are not shown

> N location(s) the API returned are not shown: their parent is not among the
> loaded locations, so the page cannot place them without inventing a position.

The tree refuses to guess. It happens when the hierarchy is inconsistent or when
paging did not bring back everything. Query `/locations` directly and look for
records whose `parent_id` points at something absent.

## Tokens and access

### It worked yesterday and gives 401 today

In order of likelihood: the token expired, it was revoked, or the account was
deactivated. The message will not distinguish them, by design. An administrator
can see which — `GET $API/users/{user_id}/tokens` shows each token's expiry and
revoked flag, and `GET $API/users/{user_id}` shows `is_active`.

### Suddenly everything answers 429

Something is retrying a dead token from your network address. Ten failures in a
minute lock the whole source for five minutes, including requests with good
tokens.

Look for a script, a scheduled job, a second browser tab, or a colleague using a
token that has been revoked. Wait out the `Retry-After` value, fix the cause,
then try once.

`/health` keeps answering throughout — useful for confirming the service itself
is fine.

### An action the interface offered was refused with 403

Expected, and explained in
[Roles and permissions](03-roles-and-permissions.md). The page knows your role
but cannot read your site grants, so it offers what your role permits and lets
the server refuse what your grants do not reach.

## Data that looks wrong

### An asset is still "operational" although work is in progress

Correct behaviour. Asset status never changes on its own — see
[Assets](05-assets.md#the-asset-lifecycle). Somebody must transition it.

### An order says "done" but the work was not finished

Completion is terminal and there is no reopening. Raise a new order for the
outstanding work, and reference the first one in its description. The audit
trail keeps both.

### A permit is still `issued` on an order that was cancelled

Also correct, and worth checking for. Cancelling an order does not touch its
permits, so a live authorization can be left behind by an abandoned job. Find
them all:

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/work-permits?status=issued"
```

Then close or revoke the ones whose orders are no longer active. Nothing does
this for you and nothing surfaces it.

### A punch item has the wrong severity or title

There is no edit. Close it — which needs a second person — with a note
explaining the error, and open a correct one. See
[Punch items](12-punch-items.md).

### A commissioning test recorded the wrong result

The result is terminal. Create a new test with a title that says it corrects the
earlier one, and record it properly. Both stay on the record, which is honest;
the audit trail shows the sequence.

### A file was attached to the wrong record

There is no delete, anywhere in the evidence system. Attach it to the right
record too, with a note; and add an evidence note or a completion note on the
wrong one explaining the error. The mistaken attachment stays.

### Evidence will not attach

```
{"error_code":"evidence.target_not_active",
 "detail":"Evidence can only be attached while the … is active; this one is 'done'."}
```

The record reached a terminal state. There is no way to add evidence
afterwards — see [Evidence](13-evidence.md). Attach it to a related record that
is still open, with a note, and accept that the link is textual.

### A download answers 500 `evidence.corrupted`

**Stop and report it.** The stored bytes no longer match the hash recorded when
the file was attached, or the file is missing from the store. Causes: disk
corruption, a partial restore, the evidence directory pointed somewhere else, or
tampering.

Note the object id and tell whoever runs the installation. Do not treat the
evidence as available until it is resolved: the record still proves *which*
bytes were attached — the hash is in the audit trail — it just cannot produce
them.

## The service will not start

For whoever runs it.

### "The configured database is empty…"

```
SchemaOutOfDateError: The configured database is empty. Run 'alembic upgrade head'
before starting the application, or set CFM_DB_AUTO_UPGRADE=true to let the
application migrate at startup.
```

Deliberate: the application refuses to serve a database whose schema is not at
the expected revision, rather than working around it. Either run the migrations
or opt into automatic migration at startup. The same error appears after an
upgrade whose migrations have not been applied.

### "The database already contains locations or users"

```
error: The database already contains locations or users; refusing to seed on top
of existing data.
```

The demo seed refuses to run twice, and will never touch real data. Point it at
an empty database.

### Nobody can authenticate on a fresh installation

A new database has no users, so no token exists and no endpoint can be called.
Break the circle with the bootstrap script, which creates one admin and prints
one token, once:

```
$ python scripts/bootstrap_admin.py rowan-admin --display-name "Rowan Ellis"
```

It refuses to run if the database already has users.

## When reporting a problem

Include:

1. **What you were trying to do**, in operational terms.
2. **The exact request** — method and path, with the token removed.
3. **The complete response**, especially `status` and `error_code`.
4. **The record's id**, so its audit trail can be read.
5. **The version** from `/health`.

Never include a token. If one has appeared in a message, a screenshot, or a log,
have it revoked.

---

[← Error messages explained](17-error-messages.md) · [Manual index](README.md) · [Next: Known limitations →](19-known-limitations.md)
