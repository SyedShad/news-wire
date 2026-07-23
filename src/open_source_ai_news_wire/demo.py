"""Explicit demo data for local dashboard evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .storage import Database


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def seed_demo_data(database: Database, *, force: bool = False) -> bool:
    """Seed fictional records. Returns False when data already exists."""
    existing = database.one("SELECT COUNT(*) AS count FROM story_cluster")
    if existing and int(existing["count"]) and not force:
        return False

    now = datetime.now(UTC).replace(microsecond=0)
    stories = [
        {
            "id": "story-demo-runtime-001",
            "slug": "transparent-inference-runtime-update",
            "headline": "Transparent inference runtime closes a critical supply-chain gap",
            "summary": "A fictional open runtime release adds signed builds, reproducible manifests, and an independent audit trail for production inference.",
            "lane": "Open Ecosystem News",
            "openness_class": "open source",
            "status": "candidate",
            "priority": "Urgent",
            "priority_score": 88,
            "importance_score": 60,
            "freshness": "Breaking",
            "first_public_at": _iso(now - timedelta(minutes=46)),
            "detected_at": _iso(now - timedelta(minutes=31)),
            "opportunity_strength": "Strong",
            "relevance_bridge": "Reproducible builds and public audit artifacts make the security claim independently inspectable.",
            "counterargument": "Open build metadata does not guarantee secure downstream deployment or timely patching.",
            "watch_expires_at": None,
            "watch_status": None,
            "material_update": 1,
            "material_updated_at": _iso(now - timedelta(minutes=31)),
            "ingestion_context": "scheduled",
        },
        {
            "id": "story-demo-policy-002",
            "slug": "model-transparency-consultation",
            "headline": "AI safety regulator opens consultation on model transparency records",
            "summary": "A fictional public consultation asks whether advanced-model providers should publish structured capability, incident, and evaluation records.",
            "lane": "Open-Source Lens Opportunity",
            "openness_class": "closed/proprietary",
            "status": "candidate",
            "priority": "High",
            "priority_score": 74,
            "importance_score": 57,
            "freshness": "Fresh",
            "first_public_at": _iso(now - timedelta(hours=3, minutes=12)),
            "detected_at": _iso(now - timedelta(hours=2, minutes=53)),
            "opportunity_strength": "Moderate",
            "relevance_bridge": "Public, machine-readable evaluation and incident records could make regulatory review more reproducible across model providers.",
            "counterargument": "Mandatory publication can expose sensitive security details and may require tiered disclosure.",
            "watch_expires_at": None,
            "watch_status": None,
            "material_update": 0,
            "material_updated_at": None,
            "ingestion_context": "scheduled",
        },
        {
            "id": "story-demo-watch-003",
            "slug": "multimodal-benchmark-signal",
            "headline": "Research group signals an imminent multimodal benchmark release",
            "summary": "A fictional conference schedule and repository placeholder point to a near-term benchmark release, but the artifact is not public yet.",
            "lane": "AGI Development",
            "openness_class": "source-available",
            "status": "watch",
            "priority": "High potential",
            "priority_score": 67,
            "importance_score": 53,
            "freshness": "Breaking",
            "first_public_at": _iso(now - timedelta(hours=1, minutes=24)),
            "detected_at": _iso(now - timedelta(hours=1, minutes=8)),
            "opportunity_strength": None,
            "relevance_bridge": "",
            "counterargument": "",
            "watch_expires_at": _iso(now + timedelta(hours=22, minutes=36)),
            "watch_status": "Active",
            "material_update": 0,
            "material_updated_at": None,
            "ingestion_context": "scheduled",
        },
        {
            "id": "story-demo-eval-004",
            "slug": "long-horizon-planning-evaluation",
            "headline": "New evaluation maps long-horizon planning failures across agent systems",
            "summary": "A fictional peer-reviewed evaluation reports where current agent systems lose state, misuse tools, and fail to recover over extended tasks.",
            "lane": "AGI Development",
            "openness_class": "open source",
            "status": "draft_ready",
            "priority": "Standard",
            "priority_score": 58,
            "importance_score": 50,
            "freshness": "Fresh",
            "first_public_at": _iso(now - timedelta(hours=4, minutes=48)),
            "detected_at": _iso(now - timedelta(hours=4, minutes=22)),
            "opportunity_strength": None,
            "relevance_bridge": "",
            "counterargument": "",
            "watch_expires_at": None,
            "watch_status": None,
            "material_update": 0,
            "material_updated_at": None,
            "ingestion_context": "scheduled",
        },
        {
            "id": "story-demo-catchup-005",
            "slug": "public-compute-disclosure",
            "headline": "Public filing adds detail on compute capacity for AI research",
            "summary": "A fictional filing discovered during recovery provides a retrospective view of planned research-compute expansion.",
            "lane": "Open Ecosystem News",
            "openness_class": "closed/proprietary",
            "status": "archived",
            "priority": "Standard",
            "priority_score": 43,
            "importance_score": 43,
            "freshness": "Catch-Up",
            "first_public_at": _iso(now - timedelta(hours=19)),
            "detected_at": _iso(now - timedelta(hours=2)),
            "opportunity_strength": "Weak",
            "relevance_bridge": "Public reporting may improve ecosystem planning, but the filing does not establish an open-access commitment.",
            "counterargument": "Disclosure of aggregate capacity is not equivalent to open compute access.",
            "watch_expires_at": None,
            "watch_status": None,
            "material_update": 0,
            "material_updated_at": None,
            "ingestion_context": "recovery",
        },
    ]

    sources = [
        ("official-labs", "Official AI organizations", "Official AI organizations", "Event", "https://example.invalid/official-ai", "healthy", 0, 8, "12 monitored organizations"),
        ("github-releases", "GitHub releases", "Open development ecosystems", "Event", "https://github.com", "healthy", 0, 5, "Conditional release feeds"),
        ("model-hubs", "Public model hubs", "Open development ecosystems", "Event", "https://huggingface.co", "healthy", 0, 11, "Models, datasets, and organization posts"),
        ("research-feeds", "Research feeds", "Research", "Event", "https://arxiv.org", "healthy", 0, 18, "AI, ML, language, vision, and robotics"),
        ("conference-programs", "Conference programs", "Research", "Event", "https://example.invalid/conferences", "healthy", 0, 22, "Programs, proceedings, and workshops"),
        ("government-records", "Government and regulatory records", "Government and law", "Event", "https://example.invalid/public-records", "healthy", 0, 31, "Consultations, standards, filings, and decisions"),
        ("security-advisories", "Safety and security advisories", "Safety and security", "Event", "https://example.invalid/advisories", "healthy", 0, 14, "Vendor and public vulnerability notices"),
        ("specialist-reporting", "Specialist reporting", "Independent reporting", "Reporting", "https://example.invalid/reporting", "degraded", 2, 76, "One feed is returning malformed dates"),
        ("huggingnews", "HuggingNews", "Aggregation", "Discovery", "https://huggingnews.com", "healthy", 0, 7, "Discovery only unless original work is present"),
        ("hacker-news", "Hacker News", "Aggregation", "Discovery", "https://news.ycombinator.com", "healthy", 0, 4, "Public link and discussion signals"),
        ("public-social", "Public social signals", "Public social signals", "Discovery", "https://example.invalid/public-social", "healthy", 0, 16, "No account, cookie, or personal session"),
        ("public-newsletters", "Public newsletter archives", "Newsletters", "Discovery", "https://example.invalid/newsletters", "paused", 0, 0, "Locally disabled while endpoints are reviewed"),
    ]

    with database.transaction() as connection:
        if force:
            for table in (
                "evidence_source_claim", "evidence_source", "candidate", "evidence_link", "claim", "source_item", "review_action", "draft",
                "discovery_lead", "momentum_snapshot", "work_item", "alert", "story_cluster", "source_registry", "scan_run",
                "usage_ledger", "diagnostic_event", "app_state",
            ):
                connection.execute(f"DELETE FROM {table}")

        connection.executemany(
            """
            INSERT INTO story_cluster(
                id, slug, headline, summary, lane, openness_class, status, priority,
                priority_score, freshness, first_public_at, detected_at,
                opportunity_strength, relevance_bridge, counterargument,
                watch_expires_at, watch_status, material_update, created_at, updated_at,
                importance_score, importance_json, material_updated_at, ingestion_context
            ) VALUES(
                :id, :slug, :headline, :summary, :lane, :openness_class, :status, :priority,
                :priority_score, :freshness, :first_public_at, :detected_at,
                :opportunity_strength, :relevance_bridge, :counterargument,
                :watch_expires_at, :watch_status, :material_update, :detected_at, :detected_at,
                :importance_score, '{}', :material_updated_at, :ingestion_context
            )
            """,
            stories,
        )
        checked_at = _iso(now - timedelta(minutes=6))
        connection.executemany(
            """
            INSERT INTO source_registry(
                id, name, family, monitoring_role, url, enabled, health,
                failure_streak, cursor, lag_minutes, last_checked_at, last_success_at, detail
            ) VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (*row[:5], row[5], row[6], f"cursor:{row[0]}:demo", row[7], checked_at, checked_at if row[5] == "healthy" else _iso(now - timedelta(hours=1)), row[8])
                for row in sources
            ],
        )

        source_rows = [
            ("story-demo-runtime-001", "Demo Runtime Project", "Event", "Signed builds and reproducible manifests are now available", "https://example.invalid/runtime/release", _iso(now - timedelta(minutes=46)), "en", "supports", "The release publishes signed artifacts, a reproducible manifest, and an audit report."),
            ("story-demo-runtime-001", "Demo Security Review", "Reporting", "Independent review confirms reproducible release metadata", "https://example.invalid/security/review", _iso(now - timedelta(minutes=29)), "en", "supports", "The reviewer reproduced the published build metadata while noting deployment risks remain."),
            ("story-demo-policy-002", "Demo AI Safety Office", "Event", "Consultation on advanced-model transparency records", "https://example.invalid/regulator/consultation", _iso(now - timedelta(hours=3, minutes=12)), "en", "supports", "The consultation asks for structured capability, incident, and evaluation disclosures."),
            ("story-demo-policy-002", "Demo Policy Desk", "Reporting", "Regulator considers tiered model disclosure", "https://example.invalid/policy/report", _iso(now - timedelta(hours=2, minutes=41)), "en", "supports", "Independent reporting confirms the consultation and identifies security concerns around full disclosure."),
            ("story-demo-watch-003", "Demo Conference Program", "Discovery", "Upcoming multimodal evaluation session", "https://example.invalid/conference/session", _iso(now - timedelta(hours=1, minutes=24)), "en", "trace", "The session listing names a benchmark but links no public artifact."),
            ("story-demo-watch-003", "Demo Research Repository", "Discovery", "Reserved repository for benchmark materials", "https://example.invalid/research/repository", _iso(now - timedelta(hours=1, minutes=10)), "en", "trace", "A public placeholder exists but contains no release or evidence package."),
            ("story-demo-eval-004", "Demo Research Proceedings", "Event", "Long-horizon agent planning evaluation", "https://example.invalid/research/paper", _iso(now - timedelta(hours=4, minutes=48)), "en", "supports", "The paper publishes tasks, failure categories, code, and evaluation results."),
            ("story-demo-eval-004", "Demo Evaluation Review", "Reporting", "Evaluation highlights state and recovery failures", "https://example.invalid/review/evaluation", _iso(now - timedelta(hours=4, minutes=3)), "en", "supports", "The review independently examines the released tasks and limitations."),
            ("story-demo-catchup-005", "Demo Public Filing Register", "Event", "Compute expansion disclosure", "https://example.invalid/filing/compute", _iso(now - timedelta(hours=19)), "en", "supports", "The filing describes planned capacity without promising public access."),
        ]
        connection.executemany(
            """
            INSERT INTO source_item(
                story_id, source_name, source_role, title, url, published_at,
                language, verification_status, passage
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            source_rows,
        )

        claims = [
            ("story-demo-runtime-001", "The fictional release includes signed builds and reproducible manifests.", "verified", "stable"),
            ("story-demo-runtime-001", "An independent reviewer reproduced the release metadata.", "verified", "stable"),
            ("story-demo-policy-002", "The fictional regulator opened a public model-transparency consultation.", "verified", "stable"),
            ("story-demo-policy-002", "The consultation considers capability, incident, and evaluation records.", "verified", "volatile"),
            ("story-demo-watch-003", "A public multimodal benchmark release is imminent.", "unverified", "volatile"),
            ("story-demo-eval-004", "The fictional evaluation groups long-horizon failures into state, tool, and recovery categories.", "verified", "stable"),
            ("story-demo-catchup-005", "The fictional filing discloses planned AI research-compute capacity.", "verified", "stable"),
        ]
        connection.executemany(
            "INSERT INTO claim(story_id, text, status, volatility) VALUES(?, ?, ?, ?)",
            claims,
        )
        all_claims = connection.execute("SELECT id, story_id FROM claim ORDER BY id").fetchall()
        for claim in all_claims:
            item = connection.execute(
                "SELECT id FROM source_item WHERE story_id = ? ORDER BY id LIMIT 1",
                (claim["story_id"],),
            ).fetchone()
            if item:
                relationship = "traces" if claim["story_id"] == "story-demo-watch-003" else "supports"
                connection.execute(
                    "INSERT INTO evidence_link(claim_id, source_item_id, relationship) VALUES(?, ?, ?)",
                    (claim["id"], item["id"], relationship),
                )

        connection.executemany(
            """
            INSERT INTO candidate(
                story_id, evidence_gate, importance_gate, score, score_json, qualified_at
            ) VALUES(?, ?, ?, ?, '{}', ?)
            """,
            [
                ("story-demo-runtime-001", 1, 1, 94, _iso(now - timedelta(minutes=29))),
                ("story-demo-policy-002", 1, 1, 82, _iso(now - timedelta(hours=2, minutes=39))),
                ("story-demo-watch-003", 0, 1, 74, None),
                ("story-demo-eval-004", 1, 1, 68, _iso(now - timedelta(hours=4))),
                ("story-demo-catchup-005", 1, 1, 51, _iso(now - timedelta(hours=18))),
            ],
        )

        connection.executemany(
            """
            INSERT INTO alert(story_id, kind, severity, title, body, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [
                ("story-demo-runtime-001", "candidate", "urgent", "Urgent verified candidate", "A material open-ecosystem release passed evidence and importance gates.", _iso(now - timedelta(minutes=28))),
                ("story-demo-policy-002", "candidate", "high", "High-priority policy candidate", "The consultation creates a defensible transparency and auditability question.", _iso(now - timedelta(hours=2, minutes=38))),
                ("story-demo-watch-003", "watch", "watch", "High-potential signal under watch", "Credible public traces exist, but the underlying benchmark is not available.", _iso(now - timedelta(hours=1, minutes=2))),
                ("story-demo-eval-004", "correction", "high", "Draft evidence needs review", "A fictional source clarification requires human review before any revision is requested.", _iso(now - timedelta(minutes=19))),
                ("story-demo-catchup-005", "catch_up", "standard", "Recovery item grouped for review", "A retrospective filing kept its original public time and was grouped into the catch-up queue.", _iso(now - timedelta(minutes=17))),
                (None, "health", "standard", "One source is degraded", "Specialist reporting has two consecutive parse failures; other source families remain healthy.", _iso(now - timedelta(minutes=12))),
            ],
        )

        connection.execute(
            """
            INSERT INTO draft(
                story_id, mode, status, version, headline, metadata, body, lens,
                sources_json, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "story-demo-eval-004", "Neutral News Brief", "Current", 1,
                "New evaluation maps long-horizon planning failures across agent systems",
                "AGI Development · Fresh · Verified from one primary source and one independent review",
                "A new fictional evaluation maps how agent systems fail during extended tasks. The released suite separates state loss, tool misuse, and failed recovery, giving researchers a more precise view of where long-running systems break down.\n\nThe work publishes its task definitions and code, while the authors caution that benchmark performance does not directly predict behavior in open-ended deployments.",
                "",
                Database.json(["Demo Research Proceedings", "Demo Evaluation Review"]),
                _iso(now - timedelta(hours=1, minutes=40)),
                _iso(now - timedelta(hours=1, minutes=40)),
            ),
        )
        connection.executemany(
            """
            INSERT INTO scan_run(
                trigger_type, started_at, finished_at, result,
                source_success_count, source_failure_count, discovered_count, details
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("calendar", _iso(now - timedelta(minutes=36)), _iso(now - timedelta(minutes=34)), "degraded", 11, 1, 2, "One reporting feed returned malformed dates."),
                ("calendar", _iso(now - timedelta(hours=1, minutes=6)), _iso(now - timedelta(hours=1, minutes=4)), "success", 12, 0, 1, "Scout completed within budget."),
                ("wake-recovery", _iso(now - timedelta(hours=2, minutes=6)), _iso(now - timedelta(hours=2)), "success", 12, 0, 2, "Recovered a 7-hour coverage gap."),
            ],
        )
        connection.executemany(
            """
            INSERT INTO usage_ledger(category, operation, effort_units, model, result, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [
                ("background", "candidate synthesis", 2, "account default", "accepted", _iso(now - timedelta(hours=3))),
                ("background", "routine triage", 1, "account default", "accepted", _iso(now - timedelta(hours=5))),
                ("draft", "neutral news brief", 2, "account default", "accepted", _iso(now - timedelta(hours=1, minutes=40))),
            ],
        )
        state = [
            ("demo_mode", "true"),
            ("schedule_status", "paused"),
            ("schedule_installed", "false"),
            ("last_scan_at", _iso(now - timedelta(minutes=34))),
            ("next_scan_at", _iso(now + timedelta(minutes=26))),
            ("background_unit_limit", "8"),
            ("urgent_reserve_limit", "2"),
            ("assistance_enabled", "false"),
            ("shadow_mode", "true"),
        ]
        connection.executemany(
            "INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)",
            [(key, value, _iso(now)) for key, value in state],
        )
        connection.execute(
            "INSERT INTO diagnostic_event(level, event_type, message, created_at) VALUES(?, ?, ?, ?)",
            ("info", "demo.seeded", "Fictional demo records were initialized for dashboard evaluation.", _iso(now)),
        )
    return True
