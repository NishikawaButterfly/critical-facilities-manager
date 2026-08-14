# 9. Running behind a reverse proxy

[← The web interface](08-web-interface.md) · [Guide index](README.md) · [Next: Rate limiting in production →](10-rate-limiting.md)

The application speaks plain HTTP and has no opinion about TLS. Everything in
this chapter is about the layer you put in front of it.

## TLS is yours

There is no certificate setting, no HTTPS listener, and no redirect from HTTP.
The application has no cookies, so there is no secure-cookie flag to get wrong
either — and no session to steal. What there is instead is a bearer token in an
`Authorization` header on **every single request**, including the ones the
frontend makes from a browser.

Without TLS, every one of those headers is readable by anything on the path,
and a token read off the wire is a full impersonation of its user until someone
revokes it. There is no second factor and no origin binding to fall back on.

So: terminate TLS at the proxy, redirect plain HTTP to HTTPS there, and bind
the application itself somewhere the outside cannot reach:

```
$ python -m uvicorn cfm.main:app --host 127.0.0.1 --port 8000
```

`--host 127.0.0.1` is not decoration. It is what makes the next section safe.

## What the proxy must do

| Requirement | Why |
|-------------|-----|
| Pass `Authorization` through unchanged | It is the only credential |
| Allow request bodies at least as large as `CFM_EVIDENCE_MAX_MB` plus multipart framing | Otherwise the proxy rejects evidence uploads the application would have accepted — and its rejection will not be a problem-details body. nginx's `client_max_body_size` defaults to 1 MB, well under the application's 25 MB default |
| Not rewrite media types | The application pins them deliberately ([The web interface](08-web-interface.md)) |
| Not cache `/ui/*` across releases | Old frontend against a new API |
| Keep the API reachable at the origin root under `CFM_API_PREFIX` | The frontend resolves API URLs against the origin |
| Append the real client address as the last `X-Forwarded-For` entry | The rest of this chapter |

Read timeouts deserve one thought: an evidence download re-hashes the whole
object before sending a byte, so a large file has a pause before the first byte,
not a slow stream.

## Client addresses, and the layer underneath

The application attributes failed authentications to a network source, and locks
that source out after too many failures ([Rate limiting in production](10-rate-limiting.md)).
`CFM_TRUSTED_PROXY` decides where that source comes from:

- **`true`** — the **rightmost** entry of `X-Forwarded-For`, the one a proxy
  directly in front of the application appended. Earlier entries came from
  outside and stay untrusted. The rightmost rule is right: nginx, traefik, and
  caddy all append.
- **`false`** (default) — the client address the application is handed, *unless
  the request carries an `X-Forwarded-For` header at all*. Every request that
  does is attributed to one shared source named `untrusted-forwarded`, whatever
  address it claims.

That second rule looks odd until you know what runs underneath the application.

### uvicorn rewrites the client address before the application sees it

uvicorn enables `--proxy-headers` **by default**, and `--forwarded-allow-ips`
defaults to `$FORWARDED_ALLOW_IPS` or, failing that, `127.0.0.1`. When the
socket peer is in that list, uvicorn replaces the client address with an entry
from `X-Forwarded-For` — the rightmost one that is not itself a trusted address
— before any application code runs. Verified against the installed uvicorn
0.52.1: a header reading `203.0.113.77, 127.0.0.1` produced a lockout against
`203.0.113.77`, the trusted rightmost entry being skipped.

So with no proxy declared, "the peer address" is not something the application
can ask for. What it gets may be the peer, or may be a string a client typed
into a header, and nothing in the request distinguishes them. It therefore
declines to divide its counters by that address: everything arriving with the
header shares one bucket, so an attacker who rotates the value accumulates
failures against that one bucket and gets locked out like anyone else.

Four processes were started with the limits at 3 failures, a 60-second window
and a 120-second lockout. Each was sent, from the loopback, four attempts
carrying `X-Forwarded-For: 198.51.100.9, 203.0.113.7`, one more that changed
the header to `192.0.2.123`, and four carrying no forwarded header at all.
Every process answered `401 401 401 429` to the first group and `401 401 401
429` to the last; their lockout log lines read:

| Process | `CFM_TRUSTED_PROXY` | Source, with the header | Source, without it |
|---------|---------------------|-------------------------|--------------------|
| `uvicorn cfm.main:app` | unset (`false`) | `untrusted-forwarded` | `127.0.0.1` |
| `uvicorn cfm.main:app` | `true` | `203.0.113.7` | `127.0.0.1` |
| `uvicorn … --no-proxy-headers` | unset (`false`) | `untrusted-forwarded` | `127.0.0.1` |
| `uvicorn … --forwarded-allow-ips ""` | unset (`false`) | `untrusted-forwarded` | `127.0.0.1` |

The attempt with the rotated header was refused `429` in every row but the
second — where `CFM_TRUSTED_PROXY=true` says a different rightmost entry is a
genuinely different client, and gave it its own budget (`401`). Rotating the
header buys nothing anywhere else.

Rows three and four are worth reading twice: turning uvicorn's rewriting off
does **not** put header-carrying requests back on their peer address. The
header is still in the request, the application still cannot verify who wrote
it, and it still refuses to count on it. What those flags change is the address
uvicorn reports — which is what the last column shows.

### What it costs

The shared bucket is not free, and the price falls on the deployment that never
declared its proxy:

- **All clients behind an undeclared proxy share one lockout.** Their requests
  all carry the header the proxy appended, so ten failures from one user lock
  out everyone else's tokens for `CFM_AUTH_LOCKOUT_SECONDS`. Setting
  `CFM_TRUSTED_PROXY=true` — which that deployment should have set anyway —
  gives every client its own bucket again.
- **A client can hold two budgets.** Sending the header spends the shared
  bucket; not sending it spends the one for the address the application is
  given. An attacker willing to alternate gets two lockout budgets instead of
  one. That is a factor of two on a throttle, not a way around it.

Both are worse for availability than counting per address, and both are
strictly better than a lockout that anyone can step around by editing a header.

### What to actually run

**A proxy on the same host, application bound to loopback** (the common case):

```
$ python -m uvicorn cfm.main:app --host 127.0.0.1 --port 8000
CFM_TRUSTED_PROXY=true
```

uvicorn's default trust of `127.0.0.1` is sound *because* the bind is
loopback-only: the only thing that can connect is something already on the
host. `CFM_TRUSTED_PROXY=true` is what makes the application agree, and it is
not optional: leave it at `false` and every client behind that proxy counts
into the one shared bucket, where any of them can lock out all the others.

**A proxy on another host:**

```
$ python -m uvicorn cfm.main:app --host 10.0.0.5 --port 8000 --forwarded-allow-ips 10.0.0.4
CFM_TRUSTED_PROXY=true
```

Name the proxy's address explicitly, and firewall the port so nothing else can
reach it. `--forwarded-allow-ips` is what lets uvicorn substitute the forwarded
address for a proxy that is not on the loopback; `CFM_TRUSTED_PROXY=true` is
what lets the application count per client instead of putting the whole office
in the shared bucket.

**No proxy at all** (a development or single-machine installation):

```
$ python -m uvicorn cfm.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
CFM_TRUSTED_PROXY=false
```

`--no-proxy-headers` stops uvicorn from substituting anything, so the address
the application is handed is the socket peer. `--forwarded-allow-ips ""` does
the same by trusting nobody. Neither makes the application *ignore*
`X-Forwarded-For`: a client that sends one anyway still lands in the shared
bucket, because nothing in the request tells the application the header went
unused. That costs the sender a budget it shares with every other header
sender, and it forges no address, which is the trade worth having.

### If you trust a proxy you do not have

Setting `CFM_TRUSTED_PROXY=true` while clients reach the application directly
hands every client the ability to name its own source. The consequences, in
order of seriousness:

1. **The lockout becomes optional.** An attacker rotates
   `X-Forwarded-For` and never accumulates failures against one bucket. Online
   token guessing is then limited only by network speed.
2. **The lockout log becomes fiction.** `Locked authentication source
   203.0.113.7` names an address the attacker chose.
3. **A source can be locked out on purpose.** Sending failures with someone
   else's address in the header locks that address for
   `CFM_AUTH_LOCKOUT_SECONDS`.

None of this touches token validity — the tokens are still SHA-256 hashes and
still compared in constant time — but it removes the throttle that makes
guessing them impractical.

## What the proxy cannot do for you

- **It cannot rate-limit authentication for the application.** If you want a
  shared limit across workers or hosts, the proxy is the only place to put it
  today ([Rate limiting in production](10-rate-limiting.md)).
- **It cannot enforce authorization.** Roles and site grants are decided in the
  application, from the token.
- **It cannot hide `/docs`** without a path rule of its own; use
  `CFM_DOCS_ENABLED=false` instead ([Security hardening](15-security-hardening.md)).

---

[← The web interface](08-web-interface.md) · [Guide index](README.md) · [Next: Rate limiting in production →](10-rate-limiting.md)
