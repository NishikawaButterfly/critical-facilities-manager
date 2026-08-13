# 6. Token lifecycle

[← First run and bootstrap](05-first-run-and-bootstrap.md) · [Guide index](README.md) · [Next: Evidence storage →](07-evidence-storage.md)

An API token is the only credential this system has. There are no passwords, no
sessions, and no SSO. A token is a shared secret: whoever holds it is the user
it belongs to, with that user's role and site grants, for every request they
make — including the ones the audit trail will attribute to that person by
name.

## What a token is

- 32 random bytes from `secrets.token_urlsafe`, which renders as a
  43-character string.
- Stored only as its SHA-256 hash; the presented secret is hashed and compared
  in constant time.
- Bound to one user. It carries no scopes of its own: role and site grants live
  on the user, and a token inherits whatever that user has at the moment of the
  request.
- Optionally dated. Otherwise it lasts until it is revoked or its user is
  deactivated.

## Minting

Admin only, against the user who will hold it:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"label":"priya-laptop","expires_at":"2027-01-31T00:00:00Z"}' \
    $API/users/<user-id>/tokens
```

```
{"id":"9ccea10d-e4d6-491d-9132-c55bbb3ac2b0","label":"priya-laptop",
 "expires_at":"2027-01-31T00:00:00Z","revoked":false,
 "created_at":"2026-08-13T19:25:17.027888Z","token":"<the secret, this once>"}
```

`token` appears in this response and never again. `expires_at` is optional;
omit it for a token with no expiry.

Two refusals, both executed:

```
# expiry in the past
{"status":422,"error_code":"api_token.expiry_not_future",
 "detail":"The token expiry must lie in the future."}

# the user has been deactivated
{"status":422,"error_code":"api_token.user_inactive",
 "detail":"User 'priya-eng' is inactive; inactive users cannot receive tokens."}
```

**Give one token per person per device**, labelled so you can tell them apart.
The label is the only handle you will ever have on a token — you cannot
identify one by its secret, because you do not have its secret.

## Listing

```
$ curl -H "Authorization: Bearer $TOKEN" $API/users/<user-id>/tokens
{"items":[
  {"id":"7e97edcc-…","label":"bootstrap","expires_at":null,"revoked":false,
   "created_at":"2026-08-13T19:24:12.506442Z"},
  {"id":"9ccea10d-…","label":"short-lived","expires_at":"2026-08-13T19:25:20.017161Z",
   "revoked":false,"created_at":"2026-08-13T19:25:17.027888Z"}],
 "total":2,"limit":50,"offset":0}
```

Metadata only — verified, no field carries the secret or its hash. There is no
endpoint that lists tokens across all users: enumerate users first, then their
tokens.

## Expiry

Verified with a token minted to expire three seconds later. Before:

```
$ curl -H "Authorization: Bearer $SHORT_LIVED" $API/me
{"id":"…","username":"rowan-admin","role":"admin","is_active":true,…}
```

Four seconds later, the same token:

```
{"type":"…#problem-details","title":"Authentication required","status":401,
 "detail":"The API token is unknown, revoked, expired, or belongs to an inactive user.",
 "instance":"/api/v1/me","error_code":"auth.invalid_token"}
```

Expiry takes effect on the next request; nothing needs to restart. The
response deliberately does not say *which* of the four reasons applies — the
same body comes back for an unknown token, a revoked one, an expired one, and
one belonging to a deactivated user. That is a design choice against probing,
and it is why the operator troubleshooting question "why is this 401?" can only
be answered by looking at the token metadata, not at the response.

**Expiry dates are not enforced anywhere else.** Nothing warns a holder that
their token expires tomorrow, nothing emails anyone, and nothing lists tokens
expiring soon. If you set expiries, put the renewal in your own calendar.

## Revocation

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" \
    $API/users/<user-id>/tokens/<token-id>/revoke
```

Verified: the affected token answered `200` on `/me` immediately before, and
`401 auth.invalid_token` immediately after. There is no cache to clear and no
grace period. Revoking twice is a conflict, not an error you need to handle:

```
{"status":409,"error_code":"api_token.already_revoked",
 "detail":"API token 28a26884-… is already revoked."}
```

Revocation is one-way. A revoked token cannot be un-revoked; mint a new one.

## Deactivation — the emergency stop

Revocation handles one token. When you need to stop a *person* — a laptop lost
with an unknown number of tokens on it, someone who left, an account behaving
strangely — deactivate the user:

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" $API/users/<user-id>/deactivate
{"id":"…","username":"priya-eng","role":"engineer","is_active":false,…}
```

Verified end to end: a second, still-valid token belonging to that user
answered `200` before the call and `401 auth.invalid_token` after it, without
being revoked individually. Every token the user holds stops working at once,
because authentication checks the user's `is_active` flag after resolving the
token.

Two things to know:

- **The last active administrator is protected.** `409
  user.last_active_admin`, quoted in
  [First run and bootstrap](05-first-run-and-bootstrap.md).
- **There is no reactivation endpoint in this release.** Deactivation is the
  emergency stop, not a pause button. Treat it as final and create a new user
  if the person comes back.

### Choosing between them

| Situation | Do this |
|-----------|---------|
| One token pasted into a ticket, a log, or a chat | Revoke that token, mint a replacement |
| A device lost, or a token of unknown reach | Deactivate the user, then create a new user |
| Someone left the organisation | Deactivate the user |
| A token expired and the person still needs access | Mint a new one; the old is already inert |
| A whole installation compromised | Deactivate every user except one admin, then work through them |

## What the audit trail records

Every mint and revoke writes an entry. Real entries, with the actor being the
admin who made the call:

```
{"actor":"rowan-admin","entity_type":"api_token","action":"created","scope":null,
 "after":{"id":"2343f2d3-…","user_id":"60746d0e-…","label":"second-token",
          "expires_at":null,"revoked":false}}

{"actor":"rowan-admin","entity_type":"api_token","action":"revoked","scope":null,
 "after":{"revoked":true}}

{"actor":"rowan-admin","entity_type":"user","action":"created","scope":null,
 "after":{"id":"60746d0e-…","username":"priya-eng","display_name":"Priya",
          "role":"engineer","is_active":true,"site_scopes":["installation"]}}

{"actor":"rowan-admin","entity_type":"user","action":"deactivated","scope":null,
 "after":{"is_active":false}}
```

Read that as: **who** did it, **what** changed, and **when** (each entry also
carries `occurred_at`, in UTC). Neither the secret nor its hash appears —
verified across every `api_token` entry in a seeded database.

Two absences are deliberate and worth knowing:

- **`scope` is null** on user and token entries. Site scope applies to domain
  writes; user administration is gated by the admin role alone.
- **Token *use* is not recorded.** The trail records that a token was created
  and revoked, not that it authenticated. Failed authentications are not
  recorded either — deliberately, since an unauthenticated attacker could
  otherwise grow an append-only table without limit
  ([Rate limiting in production](10-rate-limiting.md)).

To review token activity for a user, query the trail by actor:

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/audit-entries?actor=priya-eng"
$ curl -H "Authorization: Bearer $TOKEN" "$API/audit-entries?entity_type=api_token"
```

## Handing tokens over

The secret exists in exactly two places at the moment it is created: your
terminal and the holder's hands. Get it from one to the other without leaving
copies — a password manager share, in person, anything that is not email, a
ticket, or a chat channel.

If a token does end up somewhere it should not be, treat it as compromised and
revoke it. The cost of a needless revocation is one API call and one
inconvenienced colleague.

---

[← First run and bootstrap](05-first-run-and-bootstrap.md) · [Guide index](README.md) · [Next: Evidence storage →](07-evidence-storage.md)
