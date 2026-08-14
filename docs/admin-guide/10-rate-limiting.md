# 10. Rate limiting in production

[← Running behind a reverse proxy](09-reverse-proxy.md) · [Guide index](README.md) · [Next: Backups →](11-backups.md)

One throttle exists, and it protects one thing: guessing a token. Nothing else
in the application is rate-limited — not reads, not writes, not uploads.

## How it behaves

`CFM_AUTH_MAX_FAILURES` failed authentications from one source within
`CFM_AUTH_FAILURE_WINDOW_SECONDS` lock that source for
`CFM_AUTH_LOCKOUT_SECONDS`. Verified with the limits set to 3 failures, a
60-second window, and a 10-second lockout:

```
failure 1: HTTP 401 auth.invalid_token
failure 2: HTTP 401 auth.invalid_token
failure 3: HTTP 401 auth.invalid_token
failure 4: HTTP 429 auth.rate_limited  Retry-After=10
```

The lock takes effect *at* the limit: the third failure was the one that
tripped it, and everything after it is refused. The service logged it once,
through the standard logging module:

```
Locked authentication source 127.0.0.1 for 10 seconds after 3 failures within 60 seconds.
```

While a source is locked, four behaviours matter, all verified:

| Request from the locked source | Response |
|--------------------------------|----------|
| A **valid** token | `429 auth.rate_limited`, `Retry-After: 10` |
| An invalid token | `429`, the identical body |
| No credentials at all | `429` |
| `GET /api/v1/health` | `200 {"status":"ok",…}` |

The body is one constant string whatever was presented:

```
{"status":429,"error_code":"auth.rate_limited",
 "detail":"Too many failed authentication attempts from this source; retry after the
 number of seconds given in the Retry-After header."}
```

A locked-out caller therefore learns nothing about any token by continuing to
probe, and legitimate users caught in someone else's lockout see the same thing.
After the lockout elapsed, a good token answered `200` again with nothing to
reset.

Two things deliberately do **not** count toward the limit:

- **Successful authentications.** They cost a dictionary lookup and nothing
  else.
- **Requests with no credentials.** Verified: six credential-less requests, then
  a valid token, still `200`. This is what stops unauthenticated scanning from
  locking out a NAT gateway shared with real users.

Failed attempts write nothing to the audit trail, on purpose: that table is
append-only, and an unauthenticated attacker who could add rows to it would have
an unbounded write primitive.

`CFM_AUTH_MAX_FAILURES=0` disables the limiter entirely. Verified: 20
consecutive bad tokens, all `401`, no `429`, nothing logged.

## The counters live in process memory

This is the fact that governs every production decision below. The limiter is a
dictionary behind a lock inside the process. It is not in the database, not in
a cache, and not shared.

Verified with `CFM_AUTH_MAX_FAILURES=2` and twelve consecutive bad tokens from
one address:

| Workers | Statuses returned | Attempts allowed through |
|---------|-------------------|--------------------------|
| 1 | `401 401 429 429 429 429 429 429 429 429 429 429` | 2 |
| 2 | `401 401 401 429 401 429 429 429 429 429 429 429` | 4 |

With two workers the service logged the lockout twice — once per process, each
against `127.0.0.1`. **The effective limit is the configured one multiplied by
the number of processes.** Four workers with the default 10 means up to 40
attempts per lockout period, and a rolling restart resets every counter to zero.

Nothing about this is hidden or accidental: a database-backed limiter would put
a write on the authentication path for every failed request, which is a worse
trade for a system this size. But you have to size the setting for the
deployment you actually run.

## Tuning

Start from what the throttle is for: making online guessing of a 256-bit secret
pointless, without locking out real people who mistyped a token.

| Setting | Default | Raise it when | Lower it when |
|---------|---------|---------------|---------------|
| `CFM_AUTH_MAX_FAILURES` | `10` | Several people share one source — one address, or the `untrusted-forwarded` bucket — and get caught in each other's mistakes | You run several workers and want the *effective* limit near the default |
| `CFM_AUTH_FAILURE_WINDOW_SECONDS` | `60` | Failures are naturally spread out (a polling integration retrying slowly) | You want failures to have to be fast to count |
| `CFM_AUTH_LOCKOUT_SECONDS` | `300` | Under active attack | A locked-out crew cannot wait five minutes |

Worked example: four workers, and you want the real limit to stay near ten
attempts per five minutes. Set `CFM_AUTH_MAX_FAILURES=3` — three per process,
twelve in total, close enough to the intent, and still comfortable for a person
who pastes a stale token twice.

Do not raise the lockout to hours. It is per source, so a long lockout punishes
whoever shares that source — everyone behind an undeclared proxy, or everyone
behind a declared one when the failures land on its own address
([Running behind a reverse proxy](09-reverse-proxy.md)).

## Which address gets locked

Whatever the application sees as the client address — which is not always the
client's, because uvicorn's proxy-header handling can replace it before the
application looks. Two rules follow from that:

- **A request with no `X-Forwarded-For` header** counts against the address the
  application is handed, which is the socket peer unless something upstream
  substituted one.
- **A request carrying that header** counts against its rightmost entry when
  `CFM_TRUSTED_PROXY=true`, and otherwise against a single shared source named
  `untrusted-forwarded` — whatever address the header claims, and whatever
  address the application was handed. An unverifiable address is not allowed to
  divide the counters, or rotating the header would buy a fresh budget per
  value.

Verified with the defaults: three failures carrying `X-Forwarded-For:
198.51.100.9, 203.0.113.7` locked `untrusted-forwarded`, and a fourth attempt
that changed the header to `192.0.2.123` was refused `429` rather than starting
a new count.

The mechanism, the behaviour of each uvicorn variant, and what the shared
bucket costs a deployment that never declared its proxy are in
[Running behind a reverse proxy](09-reverse-proxy.md). It is required reading
before tuning anything here — a limiter counting the wrong address is worse
than none, because it looks like it is working.

## What this does not defend against

- **Attacks from many addresses.** Per-source accounting sees each one
  separately. A hundred sources get a hundred budgets.
- **Anything after authentication succeeds.** A valid token can call every
  endpoint as fast as the server answers. There is no request quota, no
  concurrency limit, and no protection against a client that lists everything
  in a loop.
- **Offline attacks.** There is nothing to take: only SHA-256 hashes of the
  secrets are stored.
- **Upload floods.** `CFM_EVIDENCE_MAX_MB` bounds one file, not a rate. A valid
  engineer token can fill the evidence volume.

If any of those matter to your installation, they belong at the proxy: request
rate limits per address, connection limits, and a body-size ceiling. That is
also the only place a limit shared across workers can live today.

## What to watch

The lockout log line is the one signal this subsystem emits. It is a
`WARNING` on the `cfm.ratelimit` logger and contains the source, the duration,
the failure count, and the window. Alert on:

- **Repeated lockouts of the same source** — something is retrying a dead
  token, or someone is guessing.
- **Lockouts of many different sources** — a distributed attempt, or a proxy
  misconfiguration attributing every request to a different address.
- **Any lockout naming `untrusted-forwarded`** — requests are arriving with an
  `X-Forwarded-For` header while `CFM_TRUSTED_PROXY` is `false`. The throttle
  is doing its job, but every client sending that header is sharing one bucket
  and can lock the others out. Declare the proxy, or stop the header from
  reaching the application ([Running behind a reverse
  proxy](09-reverse-proxy.md)).
- **No lockouts ever, on an internet-facing installation** — plausible, but
  worth confirming the limiter is not disabled.

---

[← Running behind a reverse proxy](09-reverse-proxy.md) · [Guide index](README.md) · [Next: Backups →](11-backups.md)
