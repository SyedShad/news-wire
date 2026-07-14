from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zizmor.yml"


def test_private_repo_zizmor_uses_free_annotation_mode() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "contents: read" in text
    assert "security-events: write" not in text
    assert "advanced-security: false" in text
    assert "annotations: true" in text
