from __future__ import annotations

import re
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
GUIDE_ROOT = ROOT / "docs" / "guide"
GUIDES = (
    GUIDE_ROOT / "index.md",
    GUIDE_ROOT / "getting-started.md",
    GUIDE_ROOT / "features-and-use-cases.md",
    GUIDE_ROOT / "everyday-workflows.md",
    GUIDE_ROOT / "releases.md",
)
LINK_PATTERN = re.compile(r"(!?)\[([^\]]*)\]\(([^)]+)\)")


def _local_target(document: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().split("#", 1)[0]
    if not target or "://" in target or target.startswith(("mailto:", "#")):
        return None
    return (document.parent / target).resolve()


def _png_dimensions(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()[:24]
    assert payload[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", payload[16:24])


def test_user_guide_is_discoverable_from_readme() -> None:
    readme = README.read_text(encoding="utf-8")
    for guide in GUIDES:
        assert guide.is_file()
        relative = guide.relative_to(ROOT).as_posix()
        assert relative in readme, f"README does not link to {relative}"


def test_user_guide_internal_links_and_images_resolve() -> None:
    documents = (README, *GUIDES)
    for document in documents:
        content = document.read_text(encoding="utf-8")
        for image_marker, label, raw_target in LINK_PATTERN.findall(content):
            target = _local_target(document, raw_target)
            if target is None:
                continue
            assert target.exists(), f"Broken link in {document}: {raw_target}"
            if image_marker:
                assert label.strip(), f"Image in {document} has empty alt text"
                width, height = _png_dimensions(target)
                assert width >= 300 and height >= 300, (
                    f"Screenshot is too small to document its UI: {target}"
                )


def test_public_user_docs_do_not_contain_personal_paths() -> None:
    for document in (README, *GUIDES):
        content = document.read_text(encoding="utf-8")
        assert "/Users/" not in content
        assert "open-source-ai-news-wire-data/" not in content
