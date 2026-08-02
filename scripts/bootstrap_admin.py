"""Create the first admin user and print its API token once.

Usage:
    python scripts/bootstrap_admin.py USERNAME [--display-name NAME]
        [--token-label LABEL] [--database-url URL]

The database defaults to ``CFM_DATABASE_URL`` (or the SQLite default) exactly
as the API does. The script refuses to run when the database already contains
users; from then on, users and tokens are managed through the API with an
admin token. The printed token cannot be recovered later — only its hash is
stored.
"""

from __future__ import annotations

import argparse
import sys

from cfm.bootstrap import BootstrapError, bootstrap_admin
from cfm.errors import DomainError


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first admin user.")
    parser.add_argument("username", help="Admin username (lowercase slug).")
    parser.add_argument(
        "--display-name",
        default=None,
        help="Human-readable name; defaults to the username.",
    )
    parser.add_argument(
        "--token-label",
        default="bootstrap",
        help="Label for the minted API token (default: bootstrap).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy database URL; defaults to CFM_DATABASE_URL or the SQLite default.",
    )
    arguments = parser.parse_args()
    try:
        result = bootstrap_admin(
            arguments.username,
            arguments.display_name or arguments.username,
            label=arguments.token_label,
            database_url=arguments.database_url,
        )
    except (BootstrapError, DomainError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Created admin user {result.username!r} (id {result.user_id}).")
    print(f"API token (label {result.token_label!r}), shown once — store it now:")
    print(f"  {result.token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
