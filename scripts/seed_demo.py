"""Seed a small synthetic demo site into the configured database.

Usage:
    python scripts/seed_demo.py [--database-url URL]

The database defaults to ``CFM_DATABASE_URL`` (or the SQLite default) exactly
as the API does, so a plain run seeds the database the API will serve. The
crew's API tokens are printed exactly once — only their hashes are stored.
"""

from __future__ import annotations

import argparse
import sys

from cfm.demo import SeedError, seed_demo


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the synthetic demo dataset.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy database URL; defaults to CFM_DATABASE_URL or the SQLite default.",
    )
    arguments = parser.parse_args()
    try:
        result = seed_demo(arguments.database_url)
    except SeedError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Seeded the demo dataset:")
    for name, value in sorted(result.counts.items()):
        print(f"  {name}: {value}")
    print("Demo API tokens, shown once — store them now:")
    for username, secret in result.tokens.items():
        print(f"  {username}: {secret}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
