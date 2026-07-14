from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_osv_keeps_artifact_without_paid_code_scanning() -> None:
    text = (ROOT / ".github" / "workflows" / "osv-scanner.yml").read_text(
        encoding="utf-8"
    )

    assert "upload-sarif: false" in text
    assert "security-events: write" not in text


def test_dependabot_groups_monthly_updates_to_conserve_ci_minutes() -> None:
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert text.count("interval: monthly") == 3
    assert "interval: weekly" not in text
    assert text.count("patterns:") == 3
