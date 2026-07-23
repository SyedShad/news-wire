"""Command-line entry point for local dashboard operations."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from .config import resolve_runtime_paths
from .assistance import (
    CodexInvoker,
    assistance_isolation_current,
    assistance_status,
    run_assistance_work,
    run_isolation_canary,
)
from .demo import seed_demo_data
from .installer import LocalInstaller
from .operations import create_purge_plan, execute_purge_plan, export_diagnostics
from .pilot import PilotGateError, PilotManager
from .runner import Worker
from .scheduler import LaunchAgentManager
from .server import DashboardServer
from .services import DashboardService
from .source_registry import set_source_enabled, synchronize_sources
from .storage import Database


def _database(data_root: str | None) -> Database:
    database = Database(resolve_runtime_paths(data_root))
    database.initialize()
    return database


def _uninitialized_database(data_root: str | None) -> Database:
    return Database(resolve_runtime_paths(data_root))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-source-ai-news-wire")
    parser.add_argument("--data-root", help="Local runtime root outside every Git worktree")
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init", help="Initialize the local runtime store")
    initialize.add_argument("--demo", action="store_true", help="Add clearly labelled fictional demo records")

    seed = subparsers.add_parser("seed-demo", help="Add fictional records for dashboard evaluation")
    seed.add_argument("--force", action="store_true", help="Replace existing local records")

    subparsers.add_parser("migrate", help="Explicitly migrate the local database schema")

    dashboard = subparsers.add_parser("dashboard", help="Start the secured local dashboard")
    dashboard.add_argument("--port", type=int, default=0, help="Loopback port; defaults to a random free port")
    dashboard.add_argument("--no-browser", action="store_true", help="Do not open the default browser")
    dashboard.add_argument("--inactivity-minutes", type=int, default=30)
    dashboard.add_argument("--story", help="Open one story after the one-time local authorization")

    subparsers.add_parser("status", help="Print local dashboard status as JSON")

    scan = subparsers.add_parser("scan", help="Run one bounded deterministic scan")
    scan.add_argument(
        "--trigger",
        choices=("manual", "scheduled", "recovery"),
        default="manual",
    )

    catch_up = subparsers.add_parser("catch-up", help="Run a human-selected extended catch-up")
    catch_up.add_argument("--start", required=True, help="Inclusive ISO date or timestamp")
    catch_up.add_argument("--end", required=True, help="Inclusive ISO date or timestamp")

    schedule = subparsers.add_parser("schedule", help="Manage the user-level macOS scheduler")
    schedule.add_argument(
        "action",
        choices=("install", "run-now", "pause", "resume", "status", "uninstall"),
    )
    schedule.add_argument("--launcher", help="Stable launcher path used by the LaunchAgent")

    sources = subparsers.add_parser("sources", help="Inspect or change local source enablement")
    sources.add_argument("action", choices=("list", "enable", "disable", "health"))
    sources.add_argument("source_id", nargs="?")

    diagnostics = subparsers.add_parser("diagnostics", help="Export redacted local diagnostics")
    diagnostics.add_argument("action", choices=("export",))
    diagnostics.add_argument("--output", required=True)

    purge = subparsers.add_parser("purge", help="Preview or execute explicit local deletion")
    purge.add_argument("action", choices=("preview", "execute"))
    purge.add_argument("--category", action="append", default=[])
    purge.add_argument("--include-protected", action="store_true")
    purge.add_argument("--plan-id")

    application = subparsers.add_parser("app", help="Install or manage immutable local application releases")
    application.add_argument("action", choices=("install", "list", "rollback", "uninstall"))
    application.add_argument("--release-id")
    application.add_argument("--source-root", default=str(Path.cwd()))
    application.add_argument(
        "--validation-report",
        help="JSON validation report bound to the exact release commit (required for install)",
    )

    assistance = subparsers.add_parser("assistance", help="Manage packet-only ChatGPT assistance")
    assistance.add_argument(
        "action",
        choices=("check-isolation", "enable", "disable", "run-pending", "status"),
    )

    pilot = subparsers.add_parser("pilot", help="Manage shadow and notification activation gates")
    pilot.add_argument(
        "action",
        choices=(
            "start-shadow", "status", "readiness", "notification-canary",
            "extend-validation", "activate-notifications", "stop-notifications",
        ),
    )
    pilot.add_argument("--confirm-reviewed", action="store_true")
    pilot.add_argument("--auto-activate", action="store_true")
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

    if arguments.command == "migrate":
        database = _uninitialized_database(data_root)
        version = database.migrate()
        print(f"Database schema is current at version {version}: {database.paths.database}")
        return 0

    if arguments.command == "dashboard":
        server = DashboardServer(
            data_root=data_root,
            port=arguments.port,
            inactivity_seconds=max(1, arguments.inactivity_minutes) * 60,
            start_path=f"/stories/{arguments.story}" if arguments.story else None,
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
        database = _database(data_root)
        scheduler = LaunchAgentManager(
            database,
            launcher=Path.home() / ".local" / "bin" / "open-source-ai-news-wire",
        )
        service = DashboardService(database, scheduler=scheduler)
        payload = {
            "runtime_root": str(service.database.paths.root),
            "schedule": service.schedule_status(),
            "overview": service.overview()["counts"],
        }
        print(json.dumps(payload, indent=2))
        return 0

    if arguments.command == "scan":
        database = _database(data_root)
        result = Worker(database).run(trigger=arguments.trigger)
        payload = {
            "status": result.status,
            "coalesced": result.coalesced,
            "scan": asdict(result.summary) if result.summary else None,
        }
        print(json.dumps(payload, indent=2))
        return 0

    if arguments.command == "catch-up":
        database = _database(data_root)
        try:
            start = date.fromisoformat(arguments.start)
            end = date.fromisoformat(arguments.end)
        except ValueError as error:
            parser.error(f"catch-up dates must use YYYY-MM-DD: {error}")
        if start > end:
            parser.error("catch-up start cannot be after end")
        result = Worker(database).run(
            trigger="extended",
            interval_start=f"{start.isoformat()}T00:00:00Z",
            interval_end=f"{end.isoformat()}T23:59:59Z",
        )
        print(json.dumps({"status": result.status, "coalesced": result.coalesced}, indent=2))
        return 0

    if arguments.command == "schedule":
        database = _database(data_root)
        manager = LaunchAgentManager(
            database,
            launcher=Path(arguments.launcher).expanduser() if arguments.launcher else None,
        )
        if arguments.action == "run-now":
            result = Worker(database).run(trigger="manual")
            print(json.dumps({"status": result.status, "coalesced": result.coalesced}, indent=2))
            return 0
        if arguments.action == "install":
            status = manager.install()
        elif arguments.action == "pause":
            status = manager.pause()
        elif arguments.action == "resume":
            status = manager.resume()
        elif arguments.action == "uninstall":
            status = manager.uninstall()
        else:
            status = manager.status()
        print(json.dumps({
            "installed": status.installed,
            "loaded": status.loaded,
            "state": status.state,
            "plist_path": str(status.plist_path),
        }, indent=2))
        return 0

    if arguments.command == "sources":
        database = _database(data_root)
        synchronize_sources(database)
        if arguments.action in {"enable", "disable"}:
            if not arguments.source_id:
                parser.error("sources enable/disable requires source_id")
            set_source_enabled(database, arguments.source_id, arguments.action == "enable")
        rows = database.query(
            """
            SELECT r.id, r.name, r.family, r.monitoring_role, r.adapter, r.definition_json,
                   s.enabled, s.health, s.failure_streak, s.last_checked_at, s.last_success_at,
                   s.last_error_class
            FROM source_registry r JOIN source_state s ON s.source_id = r.id
            ORDER BY r.family, r.name
            """
        )
        for row in rows:
            try:
                definition = json.loads(str(row.pop("definition_json") or "{}"))
            except json.JSONDecodeError:
                definition = {}
            row["validation_status"] = definition.get("validation_status", "unknown")
            row["operational_status"] = (
                row["health"] if row["enabled"] else row["validation_status"]
            )
        print(json.dumps(rows, indent=2))
        return 0

    if arguments.command == "diagnostics":
        database = _database(data_root)
        destination = export_diagnostics(database, Path(arguments.output))
        print(f"Redacted diagnostics exported to {destination}")
        return 0

    if arguments.command == "purge":
        database = _database(data_root)
        if arguments.action == "preview":
            preview = create_purge_plan(
                database,
                arguments.category,
                allow_protected=arguments.include_protected,
            )
            print(json.dumps({
                "plan_id": preview.plan_id,
                "categories": preview.categories,
                "estimated_bytes": preview.estimated_bytes,
                "effects": preview.effects,
            }, indent=2))
            return 0
        if not arguments.plan_id:
            parser.error("purge execute requires --plan-id from a prior preview")
        print(json.dumps(execute_purge_plan(database, arguments.plan_id), indent=2))
        return 0

    if arguments.command == "app":
        # Installation owns the explicit forward migration. Initializing the
        # incoming application database here would reject the supported prior
        # schema before LocalInstaller can run its commit-bound release and
        # migration transaction.
        database = (
            _uninitialized_database(data_root)
            if arguments.action == "install"
            else _database(data_root)
        )
        if arguments.action == "install" and not arguments.validation_report:
            parser.error("app install requires --validation-report")
        installer = LocalInstaller(
            Path(arguments.source_root),
            database.paths,
            validation_report=(
                Path(arguments.validation_report)
                if arguments.validation_report
                else None
            ),
        )
        if arguments.action == "install":
            release = installer.install()
            print(json.dumps({
                "release_id": release.release_id,
                "path": str(release.path),
                "active": release.active,
                "launcher": str(installer.launcher),
            }, indent=2))
            return 0
        if arguments.action == "list":
            print(json.dumps([
                {
                    "release_id": item.release_id,
                    "path": str(item.path),
                    "active": item.active,
                    "version": item.version,
                    "schema_version": item.schema_version,
                    "verified": item.verified,
                    "provenance": item.provenance,
                }
                for item in installer.list_releases()
            ], indent=2))
            return 0
        if arguments.action == "rollback":
            if not arguments.release_id:
                parser.error("app rollback requires --release-id")
            release = installer.rollback(arguments.release_id)
            print(f"Active release: {release.release_id}")
            return 0
        if database.get_state("schedule_installed", "false") == "true":
            parser.error("Uninstall the schedule before uninstalling the application")
        installer.uninstall_application()
        print("Application releases removed. Runtime data was left untouched.")
        return 0

    if arguments.command == "assistance":
        database = _database(data_root)
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if arguments.action == "check-isolation":
            passed = run_isolation_canary(database, CodexInvoker())
            print(json.dumps({"isolation_gate": "passed" if passed else "failed"}, indent=2))
            return 0 if passed else 1
        if arguments.action == "enable":
            if not assistance_isolation_current(database):
                parser.error(
                    "assistance cannot be enabled until the current release-bound isolation check passes"
                )
            database.set_state("assistance_enabled", "true", now)
        elif arguments.action == "disable":
            database.set_state("assistance_enabled", "false", now)
        elif arguments.action == "run-pending":
            output_id = run_assistance_work(database)
            print(json.dumps({"output_id": output_id}, indent=2))
            return 0
        print(json.dumps(assistance_status(database), indent=2))
        return 0

    if arguments.command == "pilot":
        database = _database(data_root)
        manager = PilotManager(database)
        try:
            if arguments.action == "start-shadow":
                status = manager.start_shadow()
            elif arguments.action == "readiness":
                print(json.dumps(asdict(manager.readiness()), indent=2))
                return 0
            elif arguments.action == "notification-canary":
                passed = manager.run_notification_canary()
                print(json.dumps({"notification_canary": "passed" if passed else "failed"}, indent=2))
                return 0 if passed else 1
            elif arguments.action == "extend-validation":
                status = manager.extend_validation(auto_activate=arguments.auto_activate)
            elif arguments.action == "activate-notifications":
                status = manager.activate_notifications(
                    human_review_confirmed=arguments.confirm_reviewed
                )
            elif arguments.action == "stop-notifications":
                status = manager.stop_notifications()
            else:
                status = manager.status()
        except PilotGateError as error:
            print(json.dumps({"status": "blocked", "reason": str(error)}, indent=2))
            return 2
        print(json.dumps(asdict(status), indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
