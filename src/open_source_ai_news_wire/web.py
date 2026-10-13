"""Secured loopback-only Flask dashboard."""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
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
from .images import ImageBriefs, layout_preview
from .services import DashboardService
from .runner import Worker
from .scheduler import LaunchAgentManager
from .storage import Database
from .generation import GenerationService


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
    operational_controls: bool = False,
    draft_callback: Callable[[int], None] | None = None,
    test_config: dict[str, Any] | None = None,
) -> Flask:
    paths = resolve_runtime_paths(data_root)
    database = Database(paths)
    database.initialize()
    scheduler = (
        LaunchAgentManager(
            database,
            launcher=Path.home() / ".local" / "bin" / "open-source-ai-news-wire",
        )
        if operational_controls
        else None
    )

    def run_in_background() -> None:
        threading.Thread(
            target=lambda: Worker(database).run(trigger="manual"),
            name="wire-manual-scan",
            daemon=True,
        ).start()

    def dispatch_draft_in_background(work_item_id: int) -> None:
        def generate() -> None:
            try:
                from .assistance import run_assistance_work
                from .research import run_content_pipeline

                work = database.one(
                    "SELECT kind FROM work_item WHERE id = ?", (work_item_id,)
                )
                if work and work["kind"] == "content":
                    run_content_pipeline(database, work_item_id)
                else:
                    run_assistance_work(database, work_item_id)
            except Exception:
                # The assistance layer records a safe, user-visible terminal or
                # retry state. Never let a daemon thread failure stop Flask.
                return

        threading.Thread(
            target=generate,
            name=f"wire-draft-{work_item_id}",
            daemon=True,
        ).start()

    service = DashboardService(
        database,
        scheduler=scheduler,
        run_callback=run_in_background if operational_controls else None,
        draft_callback=(
            draft_callback
            if draft_callback is not None
            else dispatch_draft_in_background if operational_controls else None
        ),
    )
    generation = GenerationService(database)

    def dispatch_generation() -> None:
        if operational_controls:
            threading.Thread(target=app.config["GENERATION_SERVICE"].run_pending, name="wire-generation", daemon=True).start()

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
        GENERATION_SERVICE=generation,
        STOP_CALLBACK=None,
        TOUCH_CALLBACK=None,
    )
    if test_config:
        app.config.update(test_config)

    # A dashboard restart resumes durable queued work and detects interrupted
    # workers. Existing results are never replaced by recovery.
    if operational_controls:
        dispatch_generation()

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
        unread = service.operational_unread_count()
        schedule = service.schedule_status()
        assistance_notice: dict[str, str] | None = None
        return {
            "csrf_token": session["csrf_token"],
            "unread_alerts": unread,
            "demo_mode": database.get_state("demo_mode", "false") == "true",
            "schedule_state": schedule["status"],
            "assistance_notice": assistance_notice,
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
        next_path = request.args.get("next", "")
        if re.fullmatch(r"/(stories|articles)/[A-Za-z0-9_-]+", next_path):
            return redirect(next_path, code=303)
        return redirect(url_for("overview"), code=303)

    @app.get("/locked")
    def locked() -> Response:
        return Response(render_template("locked.html"), status=401, content_type="text/html; charset=utf-8")

    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok", "scope": "loopback", "version": 1})

    @app.get("/status.json")
    def live_status() -> Response:
        unread = service.operational_unread_count()
        queued = database.one(
            "SELECT COUNT(*) AS count FROM generation_job WHERE status IN ('queued', 'running')"
        ) or {"count": 0}
        schedule = service.schedule_status()
        return jsonify(
            {
                "schema_version": 1,
                "unread_alerts": unread,
                "queued_work": int(queued["count"] or 0),
                "schedule_status": schedule["status"],
            }
        )

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return send_file(Path(app.static_folder) / "favicon.svg", mimetype="image/svg+xml")

    @app.get("/")
    def overview() -> str:
        return render_template("overview.html", page="overview", data=service.article_overview())

    @app.get("/inbox")
    def inbox() -> str:
        lane = request.args.get("lane", "")
        search = request.args.get("search", "")
        article_page = service.list_articles(search=search, lane=lane, cursor=request.args.get("cursor", ""))
        return render_template(
            "inbox.html", page="inbox", articles=article_page["articles"],
            article_total=article_page["total"], next_cursor=article_page["next_cursor"],
            selected_lane=lane, search=search,
        )

    @app.get("/operations")
    def operations() -> str:
        return render_template("operations.html", page="operations", notices=service.list_inbox_notices())

    @app.post("/inbox/read-alerts")
    def read_alerts() -> Response:
        service.mark_operational_notices_read()
        flash("Operational notices marked as read.", "success")
        return redirect(url_for("operations"), code=303)

    @app.get("/articles/<article_id>")
    def article_detail(article_id: str) -> str:
        article = service.get_article(article_id)
        if not article:
            abort(404)
        manager = app.config["GENERATION_SERVICE"]
        return render_template("article.html", page="inbox", article=article,
                               catalog=manager.catalog(), jobs=manager.list_jobs(article_id))

    @app.get("/generation/catalog.json")
    def generation_catalog() -> Response:
        return jsonify(app.config["GENERATION_SERVICE"].catalog(force_reload=request.args.get("refresh") == "1"))

    @app.get("/articles/<article_id>/generation.json")
    def generation_jobs(article_id: str) -> Response:
        if not service.get_article(article_id):
            abort(404)
        return jsonify({"jobs": app.config["GENERATION_SERVICE"].list_jobs(article_id)})

    @app.post("/articles/<article_id>/generate")
    def generate_article(article_id: str) -> Response:
        try:
            raw_skills = request.form.get("skill_ids", "[]")
            skills = json.loads(raw_skills)
            app.config["GENERATION_SERVICE"].enqueue(
                article_id, request.form.get("task_type", ""), request.form.get("model", ""),
                skills, request.form.get("customizations", ""), request.form.get("request_id", ""),
            )
            dispatch_generation()
            flash("Generation queued.", "success")
        except LookupError:
            abort(404)
        except (ValueError, RuntimeError) as error:
            flash(str(error), "error")
        return redirect(url_for("article_detail", article_id=article_id), code=303)

    @app.post("/generation/<job_id>/<action>")
    def generation_action(job_id: str, action: str) -> Response:
        manager = app.config["GENERATION_SERVICE"]
        job = manager.get_job(job_id)
        if not job:
            abort(404)
        try:
            if action == "cancel":
                manager.cancel(job_id)
            elif action == "retry":
                manager.retry(job_id)
                dispatch_generation()
            elif action == "input":
                answers = {key.removeprefix("answer_"): value for key, value in request.form.items() if key.startswith("answer_")}
                if not answers:
                    answers = json.loads(request.form.get("answers", "{}"))
                manager.respond(job_id, answers)
                dispatch_generation()
            elif action == "save":
                manager.save_text(job_id, request.form.get("text", ""))
            else:
                abort(404)
        except (ValueError, RuntimeError) as error:
            flash(str(error), "error")
        return redirect(url_for("article_detail", article_id=job["article_id"]), code=303)

    @app.get("/generation/<job_id>/text.md")
    def generation_text(job_id: str) -> Response:
        job = app.config["GENERATION_SERVICE"].get_job(job_id)
        if not job:
            abort(404)
        response = Response(job.get("output_text") or "", mimetype="text/markdown")
        response.headers["Content-Disposition"] = 'attachment; filename="news-wire-content.md"'
        return response

    @app.get("/generation/<job_id>/artifacts/<artifact_id>")
    def generation_artifact(job_id: str, artifact_id: str) -> Response:
        try:
            path = app.config["GENERATION_SERVICE"].artifact_path(job_id, artifact_id)
        except (ValueError, LookupError):
            abort(404)
        job = app.config["GENERATION_SERVICE"].get_job(job_id)
        artifact = next((item for item in job.get("artifacts", []) if item["id"] == artifact_id), None)
        if not artifact:
            abort(404)
        inline = artifact.get("mime_type") in {"image/png", "image/jpeg", "image/webp", "image/gif"}
        return send_file(path, mimetype=artifact.get("mime_type", "application/octet-stream"),
                         as_attachment=request.args.get("download") == "1" or not inline,
                         download_name=artifact["name"])

    @app.get("/stories/<story_id>")
    def story_detail(story_id: str) -> str:
        article = database.one("SELECT id FROM fetched_article WHERE story_id = ? ORDER BY first_fetched_at DESC LIMIT 1", (story_id,))
        if article:
            return redirect(url_for("article_detail", article_id=article["id"]), code=303)
        story = service.get_story(story_id)
        if not story:
            abort(404)
        return render_template(
            "story_history.html",
            page="inbox",
            story=story,
            auto_open_draft=request.args.get("draft_started") == "1",
        )

    @app.post("/stories/<story_id>/evidence/inspect")
    def inspect_story_evidence(story_id: str) -> Response:
        abort(410, description='Editorial evidence review is retired. Open a fetched article to generate content.')

    @app.post("/stories/<story_id>/evidence/<int:evidence_id>/confirm")
    def confirm_story_evidence(story_id: str, evidence_id: int) -> Response:
        abort(410, description='Editorial evidence approval is retired.')

    @app.post("/stories/<story_id>/evidence/<int:evidence_id>/exclude")
    def exclude_story_evidence(story_id: str, evidence_id: int) -> Response:
        abort(410, description='Editorial evidence review is retired.')

    @app.post("/stories/<story_id>/qualify")
    def qualify_story(story_id: str) -> Response:
        if not database.one("SELECT 1 FROM story_cluster WHERE id = ?", (story_id,)):
            abort(404)
        abort(
            410,
            description="Qualification is obsolete in v0.4.0. Every active story can use Create content.",
        )

    @app.post("/stories/<story_id>/images/prepare")
    def prepare_story_image(story_id: str) -> Response:
        abort(410, description='Image brief preparation is retired. Use Generate graphics on the fetched article.')

    @app.get("/images/<brief_id>")
    def image_brief(brief_id: str) -> str:
        try:
            brief = ImageBriefs(database).get(brief_id)
        except LookupError:
            abort(404)
        return render_template("image_brief.html", page="inbox", brief=brief)

    @app.get("/images/<brief_id>/layout.html")
    def image_layout(brief_id: str) -> Response:
        try:
            brief = ImageBriefs(database).get(brief_id)
        except LookupError:
            abort(404)
        response = Response(layout_preview(brief), mimetype="text/html")
        response.headers["Content-Disposition"] = 'attachment; filename="image-layout-preview.html"'
        return response

    @app.post("/images/<brief_id>/approve")
    def approve_image(brief_id: str) -> Response:
        abort(410, description='Image brief approval is retired. Use Generate graphics on the fetched article.')

    @app.post("/stories/<story_id>/content")
    def create_story_content(story_id: str) -> Response:
        abort(410, description='Use the fetched article generation workspace with your selected model and skills.')

    @app.post("/stories/<story_id>/review")
    def review_story(story_id: str) -> Response:
        action = request.form.get("action", "")
        if action not in {"archive", "withdraw"}:
            abort(
                410,
                description="Content approvals are obsolete in v0.4.0. Use Create content.",
            )
        reason = request.form.get("reason", "")
        confirmation_version = request.form.get("confirmation_version", "")
        try:
            status = service.review(
                story_id,
                action,
                reason,
                confirmation_version=confirmation_version,
            )
        except (ValueError, LookupError) as error:
            flash(str(error), "error")
        else:
            labels = {
                "archived": "Story archived. Evidence and history were preserved.",
                "withdrawn": "Story withdrawn. Its audit history was preserved.",
            }
            flash(labels.get(status, "Review action saved."), "success")
        destination = url_for("story_detail", story_id=story_id)
        return redirect(destination, code=303)

    @app.get("/stories/<story_id>/draft-status.json")
    def story_draft_status(story_id: str) -> Response:
        if not database.one("SELECT 1 AS present FROM story_cluster WHERE id = ?", (story_id,)):
            abort(404)
        status = service.draft_status(story_id)
        if not status:
            return jsonify({"schema_version": 1, "status": "not_requested", "active": False})
        payload = {"schema_version": 2, **status}
        payload["draft_url"] = (
            url_for("draft_detail", draft_id=status["draft_id"])
            if status.get("draft_id")
            else None
        )
        return jsonify(payload)

    @app.post("/stories/<story_id>/draft/retry")
    def retry_story_draft(story_id: str) -> Response:
        abort(410, description='Use the fetched article generation workspace to create a new version.')

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
                request.form.get("suggested_flair", ""),
                request.form.get("subreddit_reminder", "Verify rules before posting"),
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
        try:
            document = service.draft_html(draft_id)
            draft = service.get_draft(draft_id)
        except LookupError:
            abort(404)
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
