# 15. Security hardening

[← Monitoring and health](14-monitoring-and-health.md) · [Guide index](README.md) · [Next: Retention and legal hold →](16-retention-and-legal-hold.md)

In the order that matters. Every item names what the application does for you
and what it leaves to you, and nothing here is theoretical — the behaviours
were executed and are cross-referenced to the chapter that shows them.

## 1. Understand what a token is

**A token is a shared secret with no second factor.** Whoever holds it *is*
that user: their role, their site grants, and their name in the audit trail.
There is no password, no device binding, no origin check, and no session to
invalidate separately.

What the application does: 32 random bytes, stored only as a SHA-256 hash,
compared in constant time, shown exactly once at creation, never in any listing
— verified.

What you must do:

- One token per person per device, labelled.
- Hand them over out of band. Never by email, ticket, or chat.
- Revoke on any suspicion; the cost is one API call
  ([Token lifecycle](06-token-lifecycle.md)).
- Deactivate the user, not just the token, when the reach of a compromise is
  unknown — verified to kill every token that user holds at once.
- Create a second administrator on day one, because a lost sole admin token has
  no recovery path ([First run and bootstrap](05-first-run-and-bootstrap.md)).

## 2. Terminate TLS, and let nothing bypass it

A bearer token rides on **every** request, including every request the browser
frontend makes. Plain HTTP publishes those tokens to anything on the path.

The application has no TLS support of its own — no certificate setting, no
HTTPS listener, no redirect. Terminate at the proxy, redirect HTTP there, and
bind the application to loopback or an internal address so nothing can reach it
directly ([Running behind a reverse proxy](09-reverse-proxy.md)).

## 3. Get the client address right

This one is easy to get wrong in a way that looks fine.

uvicorn enables proxy headers by default and trusts `127.0.0.1`, so it can
replace the client address from `X-Forwarded-For` before the application looks.
The application answers that by refusing to count on an address it cannot
verify: with `CFM_TRUSTED_PROXY` at its default of `false`, every request
carrying the header counts into one shared `untrusted-forwarded` bucket.
**Verified:** three failures under one forwarded address locked that bucket, and
a fourth attempt under a different one was refused `429` — the header no longer
buys a fresh budget.

That is safe but blunt: clients behind an undeclared proxy share the bucket and
can lock each other out. Match the setting to your topology and it does not
happen. Details in
[Running behind a reverse proxy](09-reverse-proxy.md#uvicorn-rewrites-the-client-address-before-the-application-sees-it).
The short version:

| Deployment | Run uvicorn with | Set |
|------------|------------------|-----|
| Proxy on the same host, app bound to `127.0.0.1` | defaults | `CFM_TRUSTED_PROXY=true` |
| Proxy on another host | `--forwarded-allow-ips <proxy address>` | `CFM_TRUSTED_PROXY=true` |
| No proxy | `--no-proxy-headers` | `CFM_TRUSTED_PROXY=false` |

Binding to loopback is what makes the first row safe: it means the only thing
that can present a forged header is something already on the host.

## 4. Know how far the admin role reaches

The admin role is **installation-wide for user administration**. Site grants
scope an admin's *domain writes*, not who they can manage: any admin can create
users, mint tokens for anyone, revoke anyone's tokens, deactivate users, and
change site grants anywhere in the installation.

There is no way to delegate "administer only this site". Treat every admin
token as a key to the whole installation, keep the number of admins small, and
give engineers the engineer role even when they are senior.

Domain **reads** are scoped, though: an admin reads the sites their own grants
cover and no others, exactly like anybody else. An administrator who needs to
see the whole estate holds an installation-wide grant — the default for a new
account — and one who should not see a client's site simply is not granted it.

Scoping reads is not the same as isolating a tenant. Site grants bound which
site-owned records a token reads; they do not stop it learning that another
site exists. Procedure and constraint free text is readable by every token,
evidence declarations follow the first upload of identical bytes, and an
engineer can probe an id through a write refusal. A cross-site constraint
refusal no longer names the blocking work unless the caller's grants would
have let them read it. If a client must be unable to observe another client at
all, run separate installations rather than separate grants.

## 5. Close the documentation endpoints

`CFM_DOCS_ENABLED=false` turns `/docs`, `/redoc`, and `/openapi.json` into
`404` — verified, with `/ui/` still working.

They require no token, so anything that can reach the port can read the whole
API surface. That is not a vulnerability — the endpoints behind it still demand
a token — but on an internet-facing installation it is free reconnaissance.
Leave them on internally if they are useful; turn them off at the edge.

`CFM_WEB_ENABLED=false` similarly removes `/ui`, but do not treat that as a
security control: the page holds no privilege the token does not already carry.

## 6. Protect the database credentials

They live in the process environment, in whatever file or manifest sets it —
never in the database, never in a backup of it
([Backups](11-backups.md#what-is-not-in-the-backup)).

- Restrict the environment file to the service account.
- Give the application's database role rights on its own database only. It
  needs DDL for migrations and does not need superuser
  ([Database setup](03-database-setup.md)).
- Rotating the password is an environment change and a restart; nothing in the
  database records it.
- Keep service logs restricted. In every startup failure produced for this
  guide the password did not appear, but a URL is one exception message away
  from a log line.

**A direct database connection defeats most of this system's guarantees** — it
can create users and tokens without an audit entry
([First run and bootstrap](05-first-run-and-bootstrap.md#if-the-bootstrap-token-is-lost)).
What it cannot do is rewrite history: the append-only triggers refuse every
`UPDATE` and `DELETE` on the audit and evidence tables whatever role connects
— verified, including on a restored database
([Restore and disaster recovery](12-restore-and-disaster-recovery.md)). On
PostgreSQL a superuser can still drop the trigger; the protection is against
silent mutation, not against a deliberate, visible schema change.

## 7. Lock down the evidence directory

The store is a plain directory of hash-named files with no encryption at rest
and no access control of its own — the application is the only thing that
should touch it.

- Write access for the service account; read access for backups; nothing else.
- Keep it off shares where other software prunes, deduplicates, or rewrites
  files. Altered bytes become a permanent `500 evidence.corrupted` — verified
  by tampering with one object.
- Anyone who can read the directory can read every attached file, with no
  token and no audit entry. Anyone who can write to it can destroy evidence
  (they cannot forge it: the path is the content hash).
- The failure of getting permissions wrong is silent until the first upload,
  because nothing is checked at startup — verified
  ([Evidence storage](07-evidence-storage.md#what-a-broken-evidence-directory-does)).

## 8. Set the limits deliberately

- `CFM_AUTH_MAX_FAILURES` is per process. Divide by your worker count to get
  the real limit ([Rate limiting in production](10-rate-limiting.md)).
- `CFM_EVIDENCE_MAX_MB` bounds one file, not a rate: a valid engineer token can
  fill the volume. Rate limits belong at the proxy.
- `CFM_CORS_ORIGINS` should be `[]` unless a separate browser application
  really exists. The bundled frontend is same-origin and needs no entry.
  Verified: a disallowed origin gets no `access-control-allow-origin` header
  back, which is what makes the browser refuse it.

## The checklist

```
[ ] TLS terminated at the proxy; HTTP redirected; app bound to loopback/internal
[ ] uvicorn proxy-header flags and CFM_TRUSTED_PROXY match the actual topology
[ ] CFM_DOCS_ENABLED=false on anything internet-facing
[ ] CFM_CORS_ORIGINS=[] unless a separate browser app exists
[ ] Proxy body limit >= CFM_EVIDENCE_MAX_MB + framing
[ ] Proxy does not log Authorization headers
[ ] Two or more admins; admin count reviewed
[ ] One labelled token per person per device; expiries set where they help
[ ] Database role scoped to its own database; credentials readable only by the service
[ ] Evidence directory owned by the service account, nothing else writable
[ ] Backups cover database AND evidence, and have been restored at least once
[ ] Append-only triggers confirmed present after every restore and upgrade
[ ] CFM_APP_VERSION unset, so /health reports the truth
```

## What this release cannot do, whatever you configure

State it to whoever is accepting the risk:

- No password login, MFA, or SSO. Tokens are the whole story.
- No site-scoped administration: any admin manages users, tokens, and grants
  anywhere, whatever their own grants cover.
- An engineer or admin can still confirm that an out-of-scope id exists by
  attempting a write on it (403 rather than 404); a viewer token never reaches
  that distinction. What the read path guarantees is narrower than silence: a
  direct read does not distinguish an out-of-scope site-owned record from a
  missing one. The cross-site channels listed above are outside that guarantee.
- No encryption at rest for evidence, and no integrity protection for the
  database beyond the append-only triggers.
- No shared rate limiting across processes.
- No deletion mechanism for evidence, so a data-protection erasure request has
  no clean answer today ([Retention and legal hold](16-retention-and-legal-hold.md)).

---

[← Monitoring and health](14-monitoring-and-health.md) · [Guide index](README.md) · [Next: Retention and legal hold →](16-retention-and-legal-hold.md)
