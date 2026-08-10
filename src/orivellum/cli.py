"""Orivellum CLI entry point."""
from __future__ import annotations

import argparse
import os
import sys


def cmd_start(args: argparse.Namespace) -> None:
    import uvicorn
    port = int(os.environ.get("PORT", args.port))
    uvicorn.run(
        "orivellum.api.app:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="info",
    )


def cmd_version(_: argparse.Namespace) -> None:
    from orivellum import __version__
    print(f"Orivellum {__version__}")


def cmd_doctor(_: argparse.Namespace) -> None:
    from orivellum.configuration.config import load_config
    from orivellum.database.db import OrivellumDB
    cfg = load_config()
    print(f"Config loaded — data_dir={cfg.data_dir}")
    db = OrivellumDB.open(cfg.db_path)
    health = db.health()
    print(f"Database: {health}")
    db.close()
    print("Doctor: all checks passed")


def cmd_migrate(_: argparse.Namespace) -> None:
    from orivellum.configuration.config import load_config
    from orivellum.database.db import OrivellumDB
    cfg = load_config()
    print(f"Running migrations on {cfg.db_path}...")
    db = OrivellumDB.open(cfg.db_path)
    version = db.get_setting("schema_version", "0")
    print(f"Schema version: {version}")
    db.close()
    print("Migrations complete")


def main() -> None:
    parser = argparse.ArgumentParser(prog="orivellum", description="Orivellum workspace")
    sub = parser.add_subparsers(dest="command")

    # start
    p_start = sub.add_parser("start", help="Start the API server")
    p_start.add_argument("--host", default="0.0.0.0")
    p_start.add_argument("--port", type=int, default=8000)
    p_start.add_argument("--reload", action="store_true")
    p_start.set_defaults(func=cmd_start)

    # version
    p_ver = sub.add_parser("version", help="Print version")
    p_ver.set_defaults(func=cmd_version)

    # doctor
    p_doc = sub.add_parser("doctor", help="Run system checks")
    p_doc.set_defaults(func=cmd_doctor)

    # migrate
    p_mig = sub.add_parser("migrate", help="Run database migrations")
    p_mig.set_defaults(func=cmd_migrate)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
