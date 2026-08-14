# 8. The web interface

[← Evidence storage](07-evidence-storage.md) · [Guide index](README.md) · [Next: Running behind a reverse proxy →](09-reverse-proxy.md)

The frontend is served by the API process. There is no second service, no build
step, no Node runtime, and nothing to deploy separately — the HTML, CSS, and ES
modules ship inside the Python package and are served from where pip put them.

What operators see in it is the [User Manual's chapter
15](../user-guide/15-web-interface.md). This chapter is about serving it.

## What is served, and where

With the defaults, verified on a running instance:

| Path | Response |
|------|----------|
| `/` | `200` — redirects to `/ui/` |
| `/ui/` | `200 text/html` |
| `/ui/config.json` | `200 application/json` |
| `/ui/app.js` | `200 text/javascript; charset=utf-8`, with `x-content-type-options: nosniff` |
| `/docs`, `/redoc`, `/openapi.json` | `200` |
| `/api/v1/health` | `200` |

`/ui/config.json` is not a file on disk; it is a route, and it is how the page
learns three things it cannot guess:

```
$ curl -s http://localhost:8000/ui/config.json
{"app_name":"Critical Facilities Manager API","app_version":"0.2.0","api_prefix":"/api/v1"}
```

That third field is why a non-default `CFM_API_PREFIX` works end to end without
touching the frontend — verified with `CFM_API_PREFIX=/api/v2`, where the page's
configuration reported `"api_prefix":"/api/v2"` and `/api/v1/health` answered
`404`.

## Turning it off

`CFM_WEB_ENABLED=false` removes the frontend entirely. Verified on a process
started with it:

| Path | With the default | With `CFM_WEB_ENABLED=false` |
|------|------------------|------------------------------|
| `/` | `200` | `404` |
| `/ui/` | `200` | `404` |
| `/ui/config.json` | `200` | `404` |
| `/docs` | `200` | `200` |
| `/api/v1/health` | `200` | `200` |

Nothing else changes: the API is untouched, and `/docs` is governed separately
by `CFM_DOCS_ENABLED` — which, verified, turns `/docs`, `/redoc`, and
`/openapi.json` into `404` while leaving `/ui/` working.

Turn the frontend off for an installation that only serves machine clients. Do
not turn it off as a security measure: it authenticates with the same tokens
the API takes and exposes nothing the API does not.

## What the frontend needs from you

**Same origin.** The page calls the API at the prefix it was told, on the host
it was loaded from. Served from the application itself, that is automatic, and
`CFM_CORS_ORIGINS` is irrelevant to it — CORS matters only for a *different*
browser application you host elsewhere.

**Correct media types.** The application pins them for the extensions it
serves, because a module script delivered as `text/plain` is refused outright by
browsers, and Python's media-type table can be seeded from the Windows registry
where `.js` is often registered as `text/plain`. Verified on this Windows
machine: `/ui/app.js` came back as `text/javascript; charset=utf-8`. If you put
a proxy or CDN in front that rewrites content types, you can break this from
outside; the browser console reporting a refused module script is what that
looks like.

**No caching layer that outlives a release.** The files are versioned by
nothing but the release. A proxy caching `/ui/app.js` across an upgrade serves
old code against a new API.

**Tokens in the browser.** The page keeps the token in `localStorage` and says
so next to the button that forgets it. That means the token survives closing
the tab, on a machine you do not control. Shared workstations are the risk
case; the answer is per-person tokens and revocation
([Token lifecycle](06-token-lifecycle.md)), not a setting.

## What it does not need

- A separate process, port, or supervisor entry.
- Node, npm, or any build artefact. The files in the package are the files
  served.
- A writable directory. It is static content and never writes.
- Its own certificate, virtual host, or path rewriting — see
  [Running behind a reverse proxy](09-reverse-proxy.md).

## Serving it under a path prefix

There is no setting for mounting the frontend somewhere other than `/ui`, and a
proxy that exposes the application under a path — `https://ops.example.com/cfm/`,
say — only half works.

The page's own assets are fetched relatively (`./app.css`, `./app.js`,
`./config.json`), so they follow a prefix without complaint. **The API base does
not.** The client builds each request as the API prefix resolved against the
window's *origin*, so a page served at `/cfm/ui/` still calls
`https://ops.example.com/api/v1/…`, not `/cfm/api/v1/…`.

The consequence is a rule, not a prohibition: if you serve the application under
a path, the proxy must also expose the API at the origin root under its
unchanged prefix. Serving the application at a host root, or on its own
subdomain, avoids the question entirely and is what this guide assumes.

## Confirming it after a deployment

```
$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/ui/
200
$ curl -s http://localhost:8000/ui/config.json
{"app_name":"Critical Facilities Manager API","app_version":"0.2.0","api_prefix":"/api/v1"}
$ curl -s -D - -o /dev/null http://localhost:8000/ui/app.js | grep -i "content-type\|nosniff"
content-type: text/javascript; charset=utf-8
x-content-type-options: nosniff
```

Three checks: the page is served, its configuration is reachable, and its
scripts arrive with a media type a browser will execute. A browser is the only
thing that proves the fourth — that the page actually runs — and the project's
CI does that with a headless Chromium against a seeded database.

---

[← Evidence storage](07-evidence-storage.md) · [Guide index](README.md) · [Next: Running behind a reverse proxy →](09-reverse-proxy.md)
