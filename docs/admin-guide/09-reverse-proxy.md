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

The application attributes failed authentications to network sources, and locks
a source out after too many failures ([Rate limiting in production](10-rate-limiting.md)).
`CFM_TRUSTED_PROXY` decides which sources an attempt counts against:

- **`true`** — the **rightmost** entry of `X-Forwarded-For`, the one a proxy
  directly in front of the application appended. Earlier entries came from
  outside and stay untrusted. The rightmost rule is right: nginx, traefik, and
  caddy all append.
- **`false`** (default) — the client address the application is handed. A
  request that carries an `X-Forwarded-For` header at all counts against that
  address *and* against one shared source named `untrusted-forwarded`, whatever
  address it claims, and it is refused while either of them is locked.

That second rule looks odd until you know what runs underneath the application.

### uvicorn rewrites the client address before the application sees it

uvicorn enables `--proxy-headers` **by default**, and `--forwarded-allow-ips`
defaults to `$FORWARDED_ALLOW_IPS` or, failing that, `127.0.0.1`. When the
socket peer is in that list, uvicorn replaces the client address with an entry
from `X-Forwarded-For` — the rightmost one that is not itself a trusted address
— before any application code runs. Verified against uvicorn 0.54.0: three
failures from the loopback carrying `203.0.113.77, 127.0.0.1` locked
`203.0.113.77`, the trusted rightmost entry being skipped, and
`untrusted-forwarded` with it.

So with no proxy declared, "the peer address" is not something the application
can ask for. What it gets may be the peer, or may be a string a client typed
into a header, and nothing in the request distinguishes them. It therefore never
lets that address alone decide the count: everything arriving with the header
also counts against one shared bucket, so an attacker who rotates the value
accumulates failures against that bucket and gets locked out like anyone else.
The address is counted as well, so that when it is the client's own, leaving the
header out reaches no second budget.

Four processes were started with the limits at 3 failures, a 60-second window
and a 120-second lockout. Each was sent, from the loopback, four attempts
carrying `X-Forwarded-For: 198.51.100.9, 203.0.113.7`, one more that changed
the header to `192.0.2.123`, and four carrying no forwarded header at all.
Every process answered `401 401 401 429` to the first group. The sources their
lockout log lines named, and their answers to the last group, were:

| Process | `CFM_TRUSTED_PROXY` | Locked by the attempts with the header | Locked by the attempts without it | Answers without it |
|---------|---------------------|----------------------------------------|-----------------------------------|--------------------|
| `uvicorn cfm.main:app` | unset (`false`) | `203.0.113.7`, `untrusted-forwarded` | `127.0.0.1` | `401 401 401 429` |
| `uvicorn cfm.main:app` | `true` | `203.0.113.7` | `127.0.0.1` | `401 401 401 429` |
| `uvicorn … --no-proxy-headers` | unset (`false`) | `127.0.0.1`, `untrusted-forwarded` | nothing, already locked | `429 429 429 429` |
| `uvicorn … --forwarded-allow-ips ""` | unset (`false`) | `127.0.0.1`, `untrusted-forwarded` | nothing, already locked | `429 429 429 429` |

The attempt with the rotated header was refused `429` in every row but the
second — where `CFM_TRUSTED_PROXY=true` says a different rightmost entry is a
genuinely different client, and gave it its own budget (`401`). Rotating the
header buys nothing anywhere else.

Rows three and four are the case the address is counted for. With uvicorn's
rewriting off, the address the application is handed is the peer's own, so the
failures sent with the header counted against it as well as against the shared
bucket, and the same peer was refused without the header too: one client, one
budget. Turning the rewriting off does **not** take header-carrying requests out
of the shared bucket, though. The header is still in the request, and nothing in
the request tells the application whether uvicorn used it.

Row one is the case it cannot reach. uvicorn trusted the loopback and replaced
its address with `203.0.113.7` from the header, so the failures were charged to
an address the sender chose, and the loopback's own address kept a budget that
only the attempts without the header spent.

### What it costs

The shared bucket is not free, and the price falls on the deployment that never
declared its proxy:

- **All clients behind an undeclared proxy share one lockout.** Their requests
  all carry the header the proxy appended, so ten failures from one user lock
  out everyone else's tokens for `CFM_AUTH_LOCKOUT_SECONDS`. Setting
  `CFM_TRUSTED_PROXY=true` — which that deployment should have set anyway —
  gives every client its own bucket again.

Row one costs two more things, both confined to a peer that uvicorn trusts to
rewrite its address — anything connecting from the loopback, with uvicorn's
defaults, or from whatever `--forwarded-allow-ips` names — while
`CFM_TRUSTED_PROXY` is `false`. The application is handed the address uvicorn
substituted and nothing else, so it cannot tell such a peer from the address it
named:

- **That peer still holds two budgets.** Failures it sends with the header spend
  the shared bucket and the address the header named; failures it sends without
  the header spend its own. That is a factor of two on a throttle, not a way
  around it.
- **That peer can spend somebody else's budget.** The address its header names
  is charged as if the failures had come from there, so ten of them lock out a
  client that really connects from that address for `CFM_AUTH_LOCKOUT_SECONDS`,
  and lock the shared bucket at the same time.

Each of the three is a better trade than a lockout that anyone can step around
by editing a header, and the configurations below avoid all of them: two declare
the proxy, and the third stops uvicorn from substituting anything.

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
sender, on top of its own address's, and it forges no address, which is the
trade worth having. It is also the configuration where sending the header or
leaving it out spends the same budget, as rows three and four above show.

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
