from __future__ import annotations

import argparse
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text

from grocea.config import get_settings
from grocea.db import build_engine
from grocea.main import app
from grocea.seeding import seed_database

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_DATABASES = {"grocea", "grocea_test"}
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url)
    return config


def migrate() -> None:
    command.upgrade(alembic_config(get_settings().database_url), "head")


def reset_database(confirmed: bool) -> None:
    if not confirmed:
        raise SystemExit("Refusing reset without --yes")
    database_url = get_settings().database_url
    url = make_url(database_url)
    if url.host not in LOCAL_HOSTS or url.database not in ALLOWED_DATABASES:
        raise SystemExit("Refusing reset outside local grocea/grocea_test database")
    reset_engine = build_engine(database_url)
    with reset_engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    reset_engine.dispose()
    migrate()
    seed_database()


def export_openapi() -> None:
    destination = ROOT / "openapi" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grocea")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply all database migrations")
    subparsers.add_parser("seed", help="Idempotently seed the local profile and global catalog")
    reset = subparsers.add_parser("reset", help="Recreate, migrate, and seed the local schema")
    reset.add_argument("--yes", action="store_true", help="Confirm destructive reset")
    subparsers.add_parser("export-openapi", help="Write the committed OpenAPI contract")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "migrate":
        migrate()
    elif args.command == "seed":
        seed_database()
    elif args.command == "reset":
        reset_database(args.yes)
    elif args.command == "export-openapi":
        export_openapi()


if __name__ == "__main__":
    main()
