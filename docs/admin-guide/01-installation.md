# 1. Installation

[Guide index](README.md) · [Next: Configuration →](02-configuration.md)

## What you need

- **Python 3.12 or newer.** The package declares `requires-python = ">=3.12"`.
  CI builds and tests on 3.12 and 3.13; this guide was verified on CPython
  3.13.3.
- **A checkout of the repository.** Not just the package — see
  [what the package ships](#what-the-package-ships) below.
- **A writable directory for the evidence store** and, if you use SQLite, for
  the database file.

Nothing else. There is no message broker, no cache, no background worker, and
no separate frontend build. The application is one process.

## Installing from a checkout

```
$ git clone https://github.com/NishikawaButterfly/critical-facilities-manager.git
$ cd critical-facilities-manager
$ python -m venv .venv
$ .venv/bin/activate            # Windows: .venv\Scripts\activate
$ python -m pip install -e .
```

`pip install -e .` installs the runtime dependencies only: FastAPI, Starlette,
uvicorn, SQLAlchemy, Alembic, psycopg, pydantic, pydantic-settings, and
python-multipart. Add the `dev` extra (`pip install -e ".[dev]"`) if you also
want to run the test suite on the deployment host; it pulls in pytest, mypy,
ruff, and httpx, none of which the application needs at runtime.

The PostgreSQL driver is a hard dependency, not an extra: `psycopg[binary]`
installs whether or not you use PostgreSQL. That means a SQLite deployment
carries a driver it never loads, and a PostgreSQL deployment needs no extra
install step.

### Confirming the install

```
$ python -c "import cfm; print(cfm.__version__)"
0.2.0
$ python -m uvicorn cfm.main:app --port 8000
```

The second command will refuse to start until the database exists and is
migrated — that refusal is the correct first result, and
[Migrations](04-migrations.md) explains it. If instead you get a
`ValidationError` naming a setting, your configuration is wrong; see
[Configuration](02-configuration.md).

## What the package ships

Building and installing the wheel (`pip install .`) puts this into
`site-packages/cfm`, verified by listing the installed directory:

- Every application module: `main.py`, `config.py`, `database.py`,
  `migrate.py`, `evidence.py`, `ratelimit.py`, `bodylimit.py`, `bootstrap.py`,
  `demo.py`, the `api/` routers, and the `services/` layer.
- **The migration chain**: `migrations/env.py`, `migrations/script.py.mako`,
  and `migrations/versions/0001_baseline.py` through
  `0004_append_only_triggers.py`.
- **The web frontend**: `web/index.html`, `web/app.css`, `web/app.js`,
  `web/api.js`, `web/dom.js`, `web/order-actions.json`, and
  `web/views/{tree,board,audit}.js`. There is no build step; the files in the
  tree are the files served.
- `py.typed`, so type checkers see the annotations.

What it does **not** ship, and what that costs you:

| Not packaged | Consequence |
|--------------|-------------|
| `scripts/bootstrap_admin.py`, `scripts/seed_demo.py` | No command exists to create the first admin on a host that only has the wheel |
| `alembic.ini` | The `alembic` CLI has no configuration to find |
| `.env.example`, `tests/`, `docs/` | No reference configuration, no self-test, no documentation on the host |

The distribution declares **no console entry points** — verified: the installed
distribution's entry point list is empty, and the only executables in the
environment's `Scripts`/`bin` directory come from dependencies (`alembic`,
`uvicorn`, `fastapi`, and so on).

Two consequences follow, and they drive the rest of this guide:

**The `alembic` CLI only works from a checkout.** Run from anywhere else:

```
$ alembic upgrade head
FAILED: No 'script_location' key found in configuration.
```

**The functionality is still reachable, through the packaged helpers.** Both
were verified from a clean virtual environment holding nothing but the wheel:

```
$ python -c "
from cfm.config import get_settings
from cfm.migrate import upgrade_to_head, current_revision, head_revision
url = get_settings().database_url
upgrade_to_head(url)
print('head revision:', head_revision(), '| database now at:', current_revision(url))
from cfm.bootstrap import bootstrap_admin
result = bootstrap_admin('installed-admin', 'Installed Admin')
print('bootstrapped:', result.username, '| token length:', len(result.token))
"
head revision: 0004 | database now at: 0004
bootstrapped: installed-admin | token length: 43
```

**Recommendation: deploy from a checkout.** Install with `pip install -e .`
from the cloned repository and keep the working tree in place. You then have
`alembic.ini`, both scripts, and `.env.example` where the rest of this guide
expects them. A wheel-only install is supported, but every migration and
bootstrap step becomes a `python -c` invocation of the helpers above.

## What the project does not provide

Verified by listing every tracked file: there is no `Dockerfile`, no compose
file, no systemd unit, no nginx configuration, and no installer. Packaging the
service for your platform, supervising the process, and terminating TLS are
yours to arrange — see [Running behind a reverse proxy](09-reverse-proxy.md).

## Where things live at runtime

Two paths matter, and both default to values **relative to the process's
working directory**:

| Path | Default | Set with |
|------|---------|----------|
| Database | `./cfm.db` (SQLite) | `CFM_DATABASE_URL` |
| Evidence store | `./evidence` | `CFM_EVIDENCE_DIR` |

Relative defaults mean that starting the service from a different directory
silently points it at a different database and a different evidence store.
Verified: the same `./evidence` setting resolved to two different absolute
roots when the process was started from two different directories. **Set both
to absolute paths in any real deployment.**

---

[Guide index](README.md) · [Next: Configuration →](02-configuration.md)
