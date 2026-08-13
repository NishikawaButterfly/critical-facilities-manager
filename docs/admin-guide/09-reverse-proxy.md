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

## Client addresses, and a trap in the defaults

The application attributes failed authentications to a network source, and locks
that source out after too many failures ([Rate limiting in production](10-rate-limiting.md)).
Which address counts as "the source" is what `CFM_TRUSTED_PROXY` claims to
control:

- **`false`** (default) — use the peer address of the TCP connection.
- **`true`** — use the **rightmost** entry of `X-Forwarded-For`, the one a
  proxy directly in front of the application appended. Earlier entries came
  from outside and stay untrusted.

The rightmost rule is right: nginx, traefik, and caddy all append. But there is
a layer underneath the application, and it acts first.

### uvicorn rewrites the client address before the application sees it

uvicorn enables `--proxy-headers` **by default**, with
`--forwarded-allow-ips` defaulting to `127.0.0.1`. When the socket peer is in
that list, uvicorn replaces the client address with the rightmost untrusted
`X-Forwarded-For` entry — before any application code runs.

Four processes were started, each sent two failed authentications carrying
`X-Forwarded-For: 198.51.100.9, 203.0.113.7` from the loopback, and their
lockout log lines read:

| Process | `CFM_TRUSTED_PROXY` | Source the lockout was recorded against |
|---------|---------------------|------------------------------------------|
| `uvicorn cfm.main:app` | unset (`false`) | `203.0.113.7` |
| `uvicorn cfm.main:app` | `true` | `203.0.113.7` |
| `uvicorn … --no-proxy-headers` | unset (`false`) | `127.0.0.1` |
| `uvicorn … --forwarded-allow-ips ""` | unset (`false`) | `127.0.0.1` |

Read the first row again: **with `CFM_TRUSTED_PROXY` at its default of `false`,
a client-supplied header still decided the source.** In the same run, a good
token sent with the locked address got `429`, and the identical token sent with
a different address got `200` — the lockout was evaded by changing a header.

This is not a contradiction inside the application; it is the server underneath
having already done the substitution. It matters because the setting's name
promises otherwise, and because the condition — peer address `127.0.0.1` — is
exactly the same-host proxy layout most deployments use.

### What to actually run

**A proxy on the same host, application bound to loopback** (the common case):

```
$ python -m uvicorn cfm.main:app --host 127.0.0.1 --port 8000
CFM_TRUSTED_PROXY=true
```

uvicorn's default trust of `127.0.0.1` is sound *because* the bind is
loopback-only: the only thing that can connect is something already on the
host. Setting `CFM_TRUSTED_PROXY=true` makes the application's own view agree
with uvicorn's rather than depending on it, and documents the intent.

**A proxy on another host:**

```
$ python -m uvicorn cfm.main:app --host 10.0.0.5 --port 8000 --forwarded-allow-ips 10.0.0.4
CFM_TRUSTED_PROXY=true
```

Name the proxy's address explicitly, and firewall the port so nothing else can
reach it. Without `--forwarded-allow-ips`, uvicorn will not trust the remote
proxy's headers, and with `CFM_TRUSTED_PROXY=false` every client behind that
proxy would share one lockout bucket — one attacker locks out the whole
office.

**No proxy at all** (a development or single-machine installation):

```
$ python -m uvicorn cfm.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
CFM_TRUSTED_PROXY=false
```

`--no-proxy-headers` is what actually makes the application ignore
`X-Forwarded-For`. Verified: the same test then recorded the lockout against
`127.0.0.1`.

### If you trust a proxy you do not have

Setting `CFM_TRUSTED_PROXY=true` — or leaving uvicorn's proxy headers on — while
clients reach the application directly hands every client the ability to name
its own source. The consequences, in order of seriousness:

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
