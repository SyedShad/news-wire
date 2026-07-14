"""Command-line entry point for local dashboard operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import resolve_runtime_paths
from .demo import seed_demo_data
from .server import DashboardServer
from .services import DashboardService
from .storage import Database


def _database(data_root: str | None) -> Database:
    database = Database(resolve_runtime_paths(data_root))
    database.initialize()
    return database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-source-ai-news-wire")
    parser.add_argument("--data-root", help="Local runtime root outside every Git worktree")
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init", help="Initialize the local runtime store")
    initialize.add_argument("--demo", action="store_true", help="Add clearly labelled fictional demo records")

    seed = subparsers.add_parser("seed-demo", help="Add fictional records for dashboard evaluation")
    seed.add_argument("--force", action="store_true", help="Replace existing local records")

    dashboard = subparsers.add_parser("dashboard", help="Start the secured local dashboard")
    dashboard.add_argument("--port", type=int, default=0, help="Loopback port; defaults to a random free port")
    dashboard.add_argument("--no-browser", action="store_true", help="Do not open the default browser")
    dashboard.add_argument("--inactivity-minutes", type=int, default=30)

    subparsers.add_parser("status", help="Print local dashboard status as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    data_root = arguments.data_root

    if arguments.command == "init":
        database = _database(data_root)
        seeded = seed_demo_data(database) if arguments.demo else False
        print(f"Initialized local data at {database.paths.root}")
        if arguments.demo:
            print("Fictional demo data added." if seeded else "Existing data left unchanged.")
        return 0

    if arguments.command == "seed-demo":
        database = _database(data_root)
        seeded = seed_demo_data(database, force=arguments.force)
        print("Fictional demo data added." if seeded else "Existing data left unchanged; use --force to replace it.")
        return 0

    if arguments.command == "dashboard":
        server = DashboardServer(
            data_root=data_root,
            port=arguments.port,
            inactivity_seconds=max(1, arguments.inactivity_minutes) * 60,
        )
        print(f"Dashboard: http://127.0.0.1:{server.port}")
        print(f"Runtime data: {server.app.config['DATABASE'].paths.root}")
        print("The one-time access link is opening in the default browser." if not arguments.no_browser else f"One-time access link: {server.auth_url}")
        print("Press Ctrl-C or use Stop dashboard in the interface to end this review session.")
        try:
            server.serve(open_browser=not arguments.no_browser)
        except KeyboardInterrupt:
            server.request_stop()
        return 0

    if arguments.command == "status":
        service = DashboardService(_database(data_root))
        payload = {
            "runtime_root": str(service.database.paths.root),
            "schedule": service.schedule_status(),
            "overview": service.overview()["counts"],
        }
        print(json.dumps(payload, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
