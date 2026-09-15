"""Command-line entry point for local dashboard operations."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
import getpass
import json
import secrets
import sys
import time
import warnings
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
from .hosted_bridge import (
    HostedBridgeLaunchAgent,
    create_hosted_bridge,
    generate_bridge_secret,
    load_bridge_config,
    save_bridge_config,
    store_bridge_secret,
    store_sites_access_token,
)
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

    hosted_bridge = subparsers.add_parser(
        "hosted-bridge", help="Manage the outbound-only hosted dashboard bridge"
    )
    hosted_bridge.add_argument(
        "action",
        choices=("configure", "run", "once", "install", "start", "stop", "status", "uninstall"),
    )
    hosted_bridge.add_argument("--url", help="Hosted dashboard HTTPS origin")
    hosted_bridge.add_argument("--secret-stdin", action="store_true", help=argparse.SUPPRESS)
    hosted_bridge.add_argument("--sites-token-stdin", action="store_true", help=argparse.SUPPRESS)
    hosted_bridge.add_argument("--generate-secret", action="store_true", help=argparse.SUPPRESS)
    hosted_bridge.add_argument("--interval-seconds", type=int, default=10)

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

    telegram = subparsers.add_parser(
        "telegram", help="Manage private-group Telegram relevance alerts"
    )
    telegram.add_argument(
        "action",
        choices=("pair", "send-test", "enable", "disable", "status", "dispatch", "unpair"),
    )
    telegram.add_argument("--launcher", help="Stable launcher path used by the Telegram LaunchAgent")
    telegram.add_argument("--pair-timeout", type=int, default=120, help=argparse.SUPPRESS)
    telegram.add_argument("--max-seconds", type=int, default=50, help=argparse.SUPPRESS)
    telegram.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
    telegram.add_argument("--confirm-received", action="store_true", help=argparse.SUPPRESS)
    return parser


def _telegram_components(
    data_root: str | None,
    launcher: str | None = None,
    with_keychain: bool = True,
):
    """Lazy-load Telegram-only modules so existing commands stay independent."""
    from .telegram import TelegramClient, TelegramDispatcher, TelegramService
    from .telegram_scheduler import TelegramLaunchAgent

    database = _database(data_root)
    keychain = _telegram_keychain(database) if with_keychain else None
    scheduler = TelegramLaunchAgent(
        database,
        launcher=Path(launcher).expanduser() if launcher else None,
    )
    return database, keychain, scheduler, TelegramClient, TelegramDispatcher, TelegramService


def _telegram_keychain(database):
    from .telegram_keychain import TelegramKeychain

    return TelegramKeychain(database.paths.root)


def _telegram_setup_lock(scheduler):
    factory = getattr(scheduler, "setup_lock", None)
    return factory() if callable(factory) else nullcontext()


def _telegram_error(error: Exception, token: str = "") -> str:
    message = str(error).strip() or type(error).__name__
    if token:
        message = message.replace(token, "[redacted]")
    return message[:500]


def _telegram_pair_updates(client, *, username: str, nonce: str, timeout_seconds: int) -> dict[str, object]:
    command = f"/newswire_pair@{username} {nonce}"
    deadline = time.monotonic() + max(1, min(600, timeout_seconds))
    offset: int | None = None
    while time.monotonic() < deadline:
        payload: dict[str, object] = {"allowed_updates": ["message"], "limit": 100, "timeout": 5}
        if offset is not None:
            payload["offset"] = offset
        updates = client.call("getUpdates", payload, timeout=8)
        if not isinstance(updates, list):
            raise RuntimeError("Telegram returned an invalid pairing update list")
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            message = update.get("message")
            if isinstance(update_id, int):
                offset = max(offset or update_id, update_id + 1)
            if not isinstance(message, dict) or str(message.get("text") or "").strip() != command:
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict):
                continue
            return {"update": update, "chat": chat, "message": message}
        time.sleep(0.05)
    raise RuntimeError("Pairing command was not received before the timeout")


def _telegram_confirm_target(chat: dict[str, object], *, confirm: bool) -> bool:
    title = str(chat.get("title") or "(untitled group)")
    chat_type = str(chat.get("type") or "unknown")
    print(f"Telegram pairing target: {title} ({chat_type})")
    if confirm:
        return True
    try:
        answer = input("Pair this private team group? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _telegram_main(arguments, parser: argparse.ArgumentParser, data_root: str | None) -> int:
    action = arguments.action
    token = ""
    lifecycle = ExitStack()
    try:
        database, keychain, scheduler, TelegramClient, TelegramDispatcher, TelegramService = _telegram_components(
            data_root, arguments.launcher, action not in {"status", "disable", "unpair"}
        )
        service = TelegramService(database)
        if action in {"pair", "send-test", "enable", "disable", "unpair"}:
            lifecycle.enter_context(_telegram_setup_lock(scheduler))
        if action == "pair":
            # Invalidate the old test before any credential write. A cancelled
            # or interrupted re-pair must not authorize the replaced token.
            service.disable(require_test=True)
            scheduler.disable()
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise RuntimeError("Telegram pairing requires an interactive terminal for hidden token input")
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    token = getpass.getpass("Telegram bot token: ").strip()
            except getpass.GetPassWarning as error:
                raise RuntimeError("Telegram pairing refused an echoed token prompt") from error
            if not token:
                raise ValueError("Telegram bot token is required")
            client = TelegramClient(token)
            bot = client.call("getMe", {}, timeout=10)
            if not isinstance(bot, dict) or not bot.get("id") or not bot.get("username"):
                raise RuntimeError("Telegram did not return a usable bot identity")
            if bool(bot.get("can_read_all_group_messages")):
                raise RuntimeError("Telegram privacy mode is disabled; enable it before pairing")
            webhook = client.call("getWebhookInfo", {}, timeout=10)
            if isinstance(webhook, dict) and str(webhook.get("url") or ""):
                raise RuntimeError("Telegram bot already has a webhook; remove it before pairing")
            username = str(bot["username"])
            nonce = secrets.token_urlsafe(12)
            print(f"Add @{username} as a normal member of the private team group, then send:")
            print(f"/newswire_pair@{username} {nonce}")
            matched = _telegram_pair_updates(
                client, username=username, nonce=nonce, timeout_seconds=arguments.pair_timeout
            )
            update = matched["update"]
            chat = matched["chat"]
            if not isinstance(chat, dict):
                raise RuntimeError("Telegram pairing update did not contain a group")
            chat_type = str(chat.get("type") or "")
            if chat_type not in {"group", "supergroup"}:
                raise RuntimeError("Telegram pairing requires a group or supergroup")
            if str(chat.get("username") or ""):
                raise RuntimeError("Telegram pairing requires a private group without a public username")
            chat_id = chat.get("id")
            if isinstance(chat_id, bool) or not isinstance(chat_id, (int, str)) or not str(chat_id):
                raise RuntimeError("Telegram pairing update did not contain a valid chat ID")
            details = client.call("getChat", {"chat_id": chat_id}, timeout=10)
            if not isinstance(details, dict):
                raise RuntimeError("Telegram returned invalid group details")
            if str(details.get("id")) != str(chat_id):
                raise RuntimeError("Telegram group details did not match the pairing update")
            if str(details.get("type") or "") not in {"group", "supergroup"} or str(details.get("username") or ""):
                raise RuntimeError("Telegram group is not private")
            member = client.call(
                "getChatMember", {"chat_id": chat_id, "user_id": int(bot["id"])}, timeout=10
            )
            if not isinstance(member, dict) or str(member.get("status") or "") != "member":
                raise RuntimeError("Telegram bot must remain a normal group member with send rights")
            target = {"id": chat_id, "title": details.get("title") or chat.get("title") or "", "type": details.get("type")}
            if not _telegram_confirm_target(target, confirm=arguments.confirm):
                print(json.dumps({"status": "cancelled"}, indent=2))
                return 2
            keychain.store_token(token)
            service.pair(
                chat_id=str(chat_id),
                title=str(target["title"]),
                bot_id=int(bot["id"]),
                bot_username=username,
            )
            update_id = update.get("update_id") if isinstance(update, dict) else None
            if isinstance(update_id, int):
                try:
                    client.call(
                        "getUpdates",
                        {"offset": update_id + 1, "limit": 100, "timeout": 0},
                        timeout=8,
                    )
                except Exception:
                    # Pairing state is already durable; leaving an old update queued
                    # is safer than turning a successful pair into a failure.
                    pass
            print(json.dumps({"status": "paired", "title": target["title"], "type": target["type"]}, indent=2))
            return 0

        if action == "send-test":
            token = keychain.get_token() or ""
            if not token:
                raise RuntimeError("Telegram bot token is not available in Keychain")
            state = service.status()
            if not state.get("paired") or not state.get("chat_id"):
                raise RuntimeError("Pair a private Telegram group first")
            client = TelegramClient(token)
            bot = client.call("getMe", {}, timeout=10)
            if not isinstance(bot, dict) or str(bot.get("id")) != str(state.get("bot_id")):
                raise RuntimeError("Telegram bot identity does not match the paired bot")
            result = client.call(
                "sendMessage",
                {
                    "chat_id": state["chat_id"],
                    "text": "Open Source AI News Wire pairing test. Please confirm receipt in the private team group.",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if not isinstance(result, dict) or not result.get("message_id"):
                raise RuntimeError("Telegram test message was not acknowledged")
            service.mark_tested()
            print(json.dumps({"status": "test_sent", "message_id": result["message_id"]}, indent=2))
            return 0

        if action == "enable":
            state = service.status()
            if not state.get("paired"):
                raise RuntimeError("Pair a private Telegram group first")
            if not state.get("test_passed"):
                raise RuntimeError("Run telegram send-test and confirm receipt before enabling alerts")
            if not arguments.confirm_received:
                try:
                    answer = input("Did the Telegram test message arrive in the group? [y/N] ")
                except EOFError:
                    answer = ""
                if answer.strip().lower() not in {"y", "yes"}:
                    print(json.dumps({"status": "confirmation_required"}, indent=2))
                    return 2
            scheduler.enable()
            result = service.enable()
            print(json.dumps(result, indent=2))
            return 0

        if action == "disable":
            scheduler.disable()
            result = service.disable()
            print(json.dumps(result, indent=2))
            return 0

        if action == "status":
            result = dict(service.status())
            scheduler_status = scheduler.status()
            result["scheduler"] = {
                "installed": scheduler_status.installed,
                "loaded": scheduler_status.loaded,
                "state": scheduler_status.state,
                "plist_path": str(scheduler_status.plist_path),
            }
            print(json.dumps(result, indent=2))
            return 0

        if action == "dispatch":
            if arguments.max_seconds != 50:
                raise ValueError("Telegram dispatch is fixed to a 50-second deadline")
            dispatcher = TelegramDispatcher(
                database,
                token_provider=keychain.get_token,
                client_factory=TelegramClient,
            )
            print(json.dumps(dispatcher.dispatch(), indent=2))
            return 0

        if action == "unpair":
            scheduler_error = None
            try:
                scheduler.uninstall()
            except Exception as error:
                scheduler_error = _telegram_error(error)
            result = service.unpair()
            result = dict(result)
            result["status"] = "unpaired"
            keychain_error = None
            try:
                _telegram_keychain(database).delete_token()
            except Exception as error:
                keychain_error = _telegram_error(error)
            if scheduler_error:
                result["scheduler_error"] = scheduler_error
            if keychain_error:
                result["keychain_error"] = "keychain_deletion_failed"
                print(json.dumps(result, indent=2))
                return 2
            print(json.dumps(result, indent=2))
            return 2 if scheduler_error else 0
    except Exception as error:
        print(json.dumps({"status": "blocked", "reason": _telegram_error(error, token)}, indent=2))
        return 2
    finally:
        lifecycle.close()
    parser.error(f"unsupported telegram action: {action}")
    return 2


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

    if arguments.command == "hosted-bridge":
        database = _database(data_root)
        if arguments.action == "configure":
            if not arguments.url:
                parser.error("hosted-bridge configure requires --url")
            save_bridge_config(database.paths, arguments.url)
            if arguments.secret_stdin and arguments.generate_secret:
                parser.error("choose either --secret-stdin or --generate-secret")
            if arguments.secret_stdin:
                secret = sys.stdin.readline().strip()
                if not secret:
                    parser.error("no bridge secret was provided on standard input")
                store_bridge_secret(arguments.url, secret)
            elif arguments.generate_secret:
                store_bridge_secret(arguments.url, generate_bridge_secret())
            if arguments.sites_token_stdin:
                sites_token = sys.stdin.readline().strip()
                if not sites_token:
                    parser.error("no Sites access token was provided on standard input")
                store_sites_access_token(arguments.url, sites_token)
            print("Hosted dashboard bridge configuration saved; no secret was printed.")
            return 0
        base_url = arguments.url or load_bridge_config(database.paths)
        if arguments.action in {"run", "once"}:
            bridge = create_hosted_bridge(
                database,
                base_url,
                interval_seconds=max(10, arguments.interval_seconds),
            )
            if arguments.action == "once":
                print(json.dumps(bridge.sync_once(), indent=2))
            else:
                bridge.run_forever()
            return 0
        manager = HostedBridgeLaunchAgent(database, base_url)
        if arguments.action == "install":
            state = manager.install()
        elif arguments.action == "start":
            state = manager.start()
        elif arguments.action == "stop":
            state = manager.stop()
        elif arguments.action == "uninstall":
            state = manager.uninstall()
        else:
            state = manager.status()
        print(json.dumps({
            "installed": state.installed,
            "loaded": state.loaded,
            "running": state.running,
            "state": state.state,
            "last_exit_code": state.last_exit_code,
            "plist_path": str(state.plist_path),
        }, indent=2))
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
            # Listing is read-only, and an explicit rollback must be able to
            # verify and label an already installed audited-override manifest.
            # New installs keep the stricter provenance requirement.
            allow_unverified_source=arguments.action in {"list", "rollback"},
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

    if arguments.command == "telegram":
        return _telegram_main(arguments, parser, data_root)


if __name__ == "__main__":
    raise SystemExit(main())
