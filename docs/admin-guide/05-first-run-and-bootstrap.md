# 5. First run and bootstrap

[← Migrations](04-migrations.md) · [Guide index](README.md) · [Next: Token lifecycle →](06-token-lifecycle.md)

A migrated database has no users. No users means no tokens, and every route
except `/health` needs one — including the route that creates users. The
bootstrap script breaks that circle exactly once.

## Creating the first administrator

```
$ python scripts/bootstrap_admin.py rowan-admin --display-name "Rowan Ellis"
Created admin user 'rowan-admin' (id 4628005c-ebb8-483b-a408-0d1a1c072868).
API token (label 'bootstrap'), shown once — store it now:
  <a 43-character secret, printed here and nowhere else>
```

That is the whole output, and the third line is the only time that token
exists in readable form. The database stores its SHA-256 hash; there is no
command, endpoint, or table that can show it again.

Options:

| Flag | Default | Meaning |
|------|---------|---------|
| `--display-name` | the username | Human-readable name |
| `--token-label` | `bootstrap` | Label the minted token carries |
| `--database-url` | `CFM_DATABASE_URL`, then the SQLite default | Target database |

**The script migrates an empty database itself.** Verified: run against a
SQLite path that did not exist, it created the file, brought it to head, and
created the admin — `alembic current` afterwards printed `0004 (head)`. You do
not need a separate `alembic upgrade head` before the first bootstrap. It will
not migrate a database that has tables but no revision stamp; that case is
reported instead, with the same advice as
[Migrations](04-migrations.md#adopting-a-database-that-predates-migrations).

## It runs once, and only once

```
$ python scripts/bootstrap_admin.py second-admin
error: The database already contains users; the bootstrap only creates the first admin. Manage further users through the API with an admin token.
```

Exit status 1, nothing written. This is not a lock you can pick: the check is
"does any user exist", so it stays refused for the life of the installation.
From here on, users and tokens are API operations
([Token lifecycle](06-token-lifecycle.md)).

## If the bootstrap token is lost

Losing it before any second admin token exists is the one situation with no
in-application answer. There is no password reset, no recovery code, and no
"forgot my token" endpoint, because there is no secret stored to recover.

What is available:

1. **Another admin token, if one exists.** Any admin can mint a new token for
   any user, including themselves. This is why the first thing to do after
   bootstrap is to create a second administrator.
2. **Direct database access.** A token row is a label plus a SHA-256 of the
   secret. You can insert a row whose hash you computed yourself:

   ```
   $ python -c "import hashlib,secrets; s=secrets.token_urlsafe(32); print(s); print(hashlib.sha256(s.encode()).hexdigest())"
   ```

   then `INSERT` into `api_tokens` with that hash, the target `user_id`, a
   label, `revoked = 0`, and `expires_at` null. This is a deliberate
   out-of-band act on a system whose whole point is a trustworthy record:
   it leaves **no audit entry**, because nothing went through the application.
   Do it knowingly, and write down that you did.
3. **Start over.** On an installation with no real data yet, deleting the
   database file (SQLite) or dropping and recreating the database
   (PostgreSQL) and bootstrapping again is faster and cleaner than either of
   the above.

Note that `api_tokens` is *not* one of the append-only tables, so the insert in
option 2 is not blocked by the triggers from migration `0004`
([Evidence storage](07-evidence-storage.md) lists what is protected).

## Do this immediately after bootstrapping

```
# 1. Confirm the token works and shows the expected identity
$ curl -H "Authorization: Bearer $TOKEN" $API/me
{"id":"…","username":"rowan-admin","display_name":"Rowan Ellis","role":"admin","is_active":true,…}

# 2. Create a second administrator, so one lost token is not an outage
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"username":"dana-admin","display_name":"Dana Okafor","role":"admin"}' \
    $API/users

# 3. Mint that administrator a token, and hand it over out of band
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"label":"dana-laptop"}' \
    $API/users/<dana-id>/tokens
```

The last active administrator cannot be deactivated — verified:

```
{"status":409,"error_code":"user.last_active_admin",
 "detail":"User 'rowan-admin' is the last active admin and cannot be deactivated."}
```

which protects you from locking the installation out by tidying up accounts,
but does nothing about losing the token of the only admin. Two administrators
from day one.

## Seeding the demo dataset

`scripts/seed_demo.py` fills a database with a fictional campus so the API and
the frontend have something to show. Real output:

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
Demo API tokens, shown once — store them now:
  rowan-admin: <token>
  priya-eng: <token>
  marco-eng: <token>
  vera-viewer: <token>
```

Four crew members with four tokens, each printed once, on the same terms as the
bootstrap token.

The seed refuses to run against a database that already holds locations or
users, so it cannot land on top of real data:

```
error: The database already contains locations or users; refusing to seed on top of existing data.
```

**Never seed a production database.** Not because it would overwrite anything —
it refuses to — but because a demo campus, four synthetic users, and their
tokens in the audit trail are indistinguishable from real records once they are
in there, and there is no delete. Use a separate database for demonstrations.

## Starting the service

```
$ python -m uvicorn cfm.main:app --host 127.0.0.1 --port 8000
```

Bind to a loopback or an internal address and put a reverse proxy in front of
it ([Running behind a reverse proxy](09-reverse-proxy.md)). `--reload` is a
development convenience; it watches files and restarts, which is not what you
want supervising a service.

Confirm the installation from outside:

```
$ curl -s $API/health
{"status":"ok","version":"0.2.0","database":"ok"}
```

Then read [Monitoring and health](14-monitoring-and-health.md) for what that
does and does not tell you.

---

[← Migrations](04-migrations.md) · [Guide index](README.md) · [Next: Token lifecycle →](06-token-lifecycle.md)
