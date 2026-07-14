"""Secured loopback-only Flask dashboard."""

from __future__ import annotations

import html
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from .config import resolve_runtime_paths
from .services import DashboardService
from .storage import Database


LOOPBACK_HOST = re.compile(r"^(localhost|127\.0\.0\.1)(:\d+)?$|^\[::1\](:\d+)?$", re.I)
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_matches_request(origin: str) -> bool:
    try:
        parsed_origin = urlsplit(origin)
        parsed_request = urlsplit(f"{request.scheme}://{request.host}")
        origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
        request_port = parsed_request.port or (443 if parsed_request.scheme == "https" else 80)
    except ValueError:
        return False
    return bool(
        parsed_origin.scheme in {"http", "https"}
        and parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}
        and origin_port == request_port
    )


def _parse_time(value: str | None, style: str = "compact") -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if style == "date":
        return moment.strftime("%b %-d, %Y")
    if style == "full":
        return moment.strftime("%b %-d, %Y · %H:%M UTC")
    return moment.strftime("%b %-d · %H:%M UTC")


def _slug_filename(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug[:80] or "news-wire-export"


def _safe_external_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "#"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "#"
    return value


def create_app(
    *,
    data_root: str | Path | None = None,
    auth_token: str | None = None,
    auth_required: bool = True,
    test_config: dict[str, Any] | None = None,
) -> Flask:
    paths = resolve_runtime_paths(data_root)
    database = Database(paths)
    database.initialize()
    service = DashboardService(database)

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        AUTH_REQUIRED=auth_required,
        AUTH_TOKEN=auth_token or secrets.token_urlsafe(32),
        AUTH_TOKEN_CONSUMED=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,
        MAX_CONTENT_LENGTH=1_000_000,
        DATABASE=database,
        DASHBOARD_SERVICE=service,
        STOP_CALLBACK=None,
        TOUCH_CALLBACK=None,
    )
    if test_config:
        app.config.update(test_config)

    app.jinja_env.filters["wire_time"] = _parse_time
    app.jinja_env.filters["from_json"] = lambda value: json.loads(value or "[]")
    app.jinja_env.filters["safe_external_url"] = _safe_external_url

    @app.before_request
    def enforce_local_session() -> Response | None:
        host = request.host.lower()
        if not LOOPBACK_HOST.fullmatch(host):
            abort(400, description="The dashboard accepts loopback requests only.")

        if not app.config["AUTH_REQUIRED"]:
            session["authorized"] = True
        public_exempt = {"authenticate", "locked", "health", "favicon", "static"}
        passive_endpoints = public_exempt | {"live_status"}
        if request.endpoint not in public_exempt and not session.get("authorized"):
            return redirect(url_for("locked"))

        if request.method in STATE_CHANGING_METHODS:
            origin = request.headers.get("Origin")
            # Chromium can serialize a local, user-initiated form submission as
            # an opaque Origin. In that case the session-bound CSRF token below
            # remains the authority; concrete origins must still be loopback on
            # the same port.
            if origin and origin != "null" and not _origin_matches_request(origin):
                abort(400, description="Cross-origin state changes are not allowed.")
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            expected = session.get("csrf_token")
            if not supplied or not expected or not secrets.compare_digest(supplied, expected):
                abort(400, description="Invalid or missing CSRF token.")

        touch = app.config.get("TOUCH_CALLBACK")
        if touch and request.endpoint not in passive_endpoints:
            touch()
        return None

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.context_processor
    def shared_context() -> dict[str, Any]:
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(24)
        unread = database.one("SELECT COUNT(*) AS count FROM alert WHERE read_at IS NULL") or {"count": 0}
        return {
            "csrf_token": session["csrf_token"],
            "unread_alerts": int(unread["count"]),
            "demo_mode": database.get_state("demo_mode", "false") == "true",
            "schedule_state": database.get_state("schedule_status", "not_installed"),
        }

    @app.get("/auth/<token>")
    def authenticate(token: str) -> Response:
        expected = str(app.config["AUTH_TOKEN"])
        if not secrets.compare_digest(token, expected):
            abort(403)
        if app.config["AUTH_TOKEN_CONSUMED"]:
            abort(403, description="This one-time dashboard link has already been used.")
        session.clear()
        session["authorized"] = True
        session["csrf_token"] = secrets.token_urlsafe(24)
        app.config["AUTH_TOKEN_CONSUMED"] = True
        touch = app.config.get("TOUCH_CALLBACK")
        if touch:
            touch()
        return redirect(url_for("overview"), code=303)

    @app.get("/locked")
    def locked() -> Response:
        return Response(render_template("locked.html"), status=401, content_type="text/html; charset=utf-8")

    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok", "scope": "loopback", "version": 1})

    @app.get("/status.json")
    def live_status() -> Response:
        unread = database.one("SELECT COUNT(*) AS count FROM alert WHERE read_at IS NULL") or {"count": 0}
        queued = database.one(
            "SELECT COUNT(*) AS count FROM work_item WHERE status IN ('pending', 'queued')"
        ) or {"count": 0}
        return jsonify(
            {
                "schema_version": 1,
                "unread_alerts": int(unread["count"] or 0),
                "queued_work": int(queued["count"] or 0),
                "schedule_status": database.get_state("schedule_status", "not_installed"),
            }
        )

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return send_file(Path(app.static_folder) / "favicon.svg", mimetype="image/svg+xml")

    @app.get("/")
    def overview() -> str:
        return render_template("overview.html", page="overview", data=service.overview())

    @app.get("/inbox")
    def inbox() -> str:
        kind = request.args.get("kind", "all")
        status = request.args.get("status", "all")
        lane = request.args.get("lane", "all")
        stories = service.list_stories(status=status, lane=lane, kind=kind)
        notices = service.list_inbox_notices(kind=kind)
        return render_template(
            "inbox.html",
            page="inbox",
            stories=stories,
            notices=notices,
            selected_kind=kind,
            selected_status=status,
            selected_lane=lane,
        )

    @app.post("/inbox/read-alerts")
    def read_alerts() -> Response:
        service.mark_alerts_read()
        flash("Alerts marked as read.", "success")
        return redirect(request.referrer or url_for("inbox"), code=303)

    @app.get("/stories/<story_id>")
    def story_detail(story_id: str) -> str:
        story = service.get_story(story_id)
        if not story:
            abort(404)
        return render_template("story.html", page="inbox", story=story)

    @app.post("/stories/<story_id>/review")
    def review_story(story_id: str) -> Response:
        action = request.form.get("action", "")
        reason = request.form.get("reason", "")
        try:
            status = service.review(story_id, action, reason)
        except (ValueError, LookupError) as error:
            flash(str(error), "error")
        else:
            labels = {
                "approved": "Draft request queued after human approval.",
                "archived": "Story archived. Evidence and history were preserved.",
                "withdrawn": "Story withdrawn. Its audit history was preserved.",
                "candidate": "Story accepted as a candidate.",
            }
            flash(labels.get(status, "Review action saved."), "success")
        return redirect(url_for("story_detail", story_id=story_id), code=303)

    @app.get("/stories/<story_id>/evidence.json")
    def export_evidence(story_id: str) -> Response:
        try:
            bundle = service.evidence_bundle(story_id)
        except LookupError:
            abort(404)
        response = jsonify(bundle)
        response.headers["Content-Disposition"] = f'attachment; filename="evidence-{_slug_filename(story_id)}.json"'
        return response

    @app.get("/drafts")
    def drafts() -> str:
        return render_template("drafts.html", page="drafts", drafts=service.list_drafts())

    @app.get("/drafts/<int:draft_id>")
    def draft_detail(draft_id: int) -> str:
        draft = service.get_draft(draft_id)
        if not draft:
            abort(404)
        return render_template(
            "draft.html",
            page="drafts",
            draft=draft,
            copy_text=service.draft_markdown(draft_id),
        )

    @app.post("/drafts/<int:draft_id>/save")
    def save_draft(draft_id: int) -> Response:
        try:
            new_id = service.save_draft(
                draft_id,
                request.form.get("headline", ""),
                request.form.get("metadata", ""),
                request.form.get("body", ""),
                request.form.get("lens", ""),
            )
        except (ValueError, LookupError) as error:
            flash(str(error), "error")
            return redirect(url_for("draft_detail", draft_id=draft_id), code=303)
        flash("A new version was saved. The previous version remains in history.", "success")
        return redirect(url_for("draft_detail", draft_id=new_id), code=303)

    @app.get("/drafts/<int:draft_id>/export.md")
    def export_draft_markdown(draft_id: int) -> Response:
        try:
            content = service.draft_markdown(draft_id)
            draft = service.get_draft(draft_id)
        except LookupError:
            abort(404)
        filename = _slug_filename(str(draft["headline"]))
        return Response(
            content,
            content_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
        )

    @app.get("/drafts/<int:draft_id>/export.html")
    def export_draft_html(draft_id: int) -> Response:
        draft = service.get_draft(draft_id)
        if not draft:
            abort(404)
        body = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in draft["body"].split("\n\n"))
        lens = ""
        if draft["lens"]:
            lens = f"<h2>Open-Source Lens</h2><p>{html.escape(draft['lens'])}</p>"
        sources = "".join(f"<li>{html.escape(source)}</li>" for source in draft["sources"])
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(draft['headline'])}</title>
<style>body{{font:17px/1.65 system-ui;max-width:760px;margin:7vh auto;padding:0 24px;color:#17201d}}h1{{font:700 42px/1.08 Georgia,serif}}.meta{{color:#61706a}}h2{{margin-top:2.4rem}}</style>
</head><body><article><h1>{html.escape(draft['headline'])}</h1><p class="meta">{html.escape(draft['metadata'])}</p>{body}{lens}<h2>Sources</h2><ul>{sources}</ul></article></body></html>"""
        filename = _slug_filename(str(draft["headline"]))
        return Response(
            document,
            content_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}.html"'},
        )

    @app.get("/sources")
    def sources() -> str:
        return render_template("sources.html", page="sources", data=service.sources())

    @app.post("/sources/<source_id>/toggle")
    def toggle_source(source_id: str) -> Response:
        try:
            enabled = service.toggle_source(source_id)
        except LookupError:
            abort(404)
        flash(f"Source {'enabled' if enabled else 'disabled'} locally.", "success")
        return redirect(url_for("sources"), code=303)

    @app.get("/schedule")
    def schedule() -> str:
        return render_template("schedule.html", page="schedule", data=service.schedule_status())

    @app.post("/schedule/<action>")
    def schedule_action(action: str) -> Response:
        try:
            result = service.schedule_action(action)
        except ValueError as error:
            flash(str(error), "error")
        else:
            flash(f"Schedule action saved: {result.replace('_', ' ')}.", "success")
        return redirect(url_for("schedule"), code=303)

    @app.post("/schedule/extended-catchup")
    def extended_catchup() -> Response:
        try:
            result = service.queue_extended_catchup(
                request.form.get("start_date", ""),
                request.form.get("end_date", ""),
            )
        except ValueError as error:
            flash(str(error), "error")
        else:
            flash(f"Extended catch-up: {result.replace('_', ' ')}.", "success")
        return redirect(url_for("schedule"), code=303)

    @app.get("/settings")
    def settings() -> str:
        return render_template("settings.html", page="settings", data=service.settings())

    @app.post("/settings/purge-operations")
    def purge_operations() -> Response:
        try:
            count = service.purge_operations(
                request.form.get("confirmation", ""),
                category_selected=request.form.get("category") == "operations",
            )
        except ValueError as error:
            flash(str(error), "error")
        else:
            flash(f"Purged {count} local diagnostic event{'s' if count != 1 else ''}.", "success")
        return redirect(url_for("settings"), code=303)

    @app.get("/settings/diagnostics.json")
    def diagnostics_export() -> Response:
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now().isoformat(),
            "schedule": service.schedule_status(),
            "events": database.query(
                "SELECT level, event_type, message, created_at FROM diagnostic_event ORDER BY created_at DESC"
            ),
        }
        response = jsonify(payload)
        response.headers["Content-Disposition"] = 'attachment; filename="news-wire-diagnostics.json"'
        return response

    @app.post("/stop")
    def stop() -> Response:
        callback = app.config.get("STOP_CALLBACK")
        if callback:
            callback()
        return render_template("stopped.html"), 200

    return app
