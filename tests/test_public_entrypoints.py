"""Regression guards for the two deliberately separate public entrypoints.

The static custom domain is the evidence hub. The API Gateway origin is the
rider assistant. Mixing them up turns "Try the assistant" links into a report
page (or sends evidence readers straight into a paid serving path), so every
public orientation document must name and label both roles explicitly.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from assistant import config

EVIDENCE_HUB_URL = "https://evals.chelseakr.com/"
ASSISTANT_URL = "https://fare.chelseakr.com/"

README = config.REPO_ROOT / "README.md"
EVIDENCE_INDEX = config.REPO_ROOT / "docs" / "pages" / "index.html"
DEMO_SCRIPT = config.REPO_ROOT / "docs" / "DEMO-SCRIPT.md"
SMOKE_SCRIPT = config.REPO_ROOT / "scripts" / "smoke-production.sh"


def _normalized(path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _assert_labeled_near(text: str, url: str, labels: tuple[str, ...]) -> None:
    """Require one role label within a small window around the exact URL."""
    positions = [match.start() for match in re.finditer(re.escape(url), text)]
    assert positions, f"{url} is missing"
    for position in positions:
        window = text[max(0, position - 180) : position + len(url) + 180].casefold()
        if any(label.casefold() in window for label in labels):
            return
    raise AssertionError(f"{url} is present but not labeled as one of {labels}")


class TestPublicOrientationDocuments:
    def test_readme_distinguishes_evidence_hub_from_rider_assistant(self):
        text = _normalized(README)
        _assert_labeled_near(text, EVIDENCE_HUB_URL, ("evidence", "evaluation"))
        _assert_labeled_near(text, ASSISTANT_URL, ("assistant", "rider"))

        live_demo = re.search(r"## Live demo(?P<section>.*?)(?:## |\Z)", text)
        assert live_demo, "README must retain a Live demo section"
        section = live_demo.group("section")
        assert ASSISTANT_URL in section
        assert not re.search(rf"Try it at\s+<{re.escape(EVIDENCE_HUB_URL)}>", section)

    def test_evidence_home_labels_links_by_destination(self):
        soup = BeautifulSoup(EVIDENCE_INDEX.read_text(encoding="utf-8"), "html.parser")
        links = [(link.get("href"), " ".join(link.stripped_strings)) for link in soup.find_all("a")]

        evidence_labels = [label for href, label in links if href == EVIDENCE_HUB_URL]
        assistant_labels = [label for href, label in links if href == ASSISTANT_URL]
        assert evidence_labels, "evidence home must link to its canonical evidence URL"
        assert assistant_labels, "evidence home must link to the live rider assistant"
        assert any(re.search(r"evidence|evaluation", label, re.I) for label in evidence_labels)
        assert any(re.search(r"assistant|rider", label, re.I) for label in assistant_labels)
        assert not any(re.search(r"assistant|live demo", label, re.I) for label in evidence_labels)

    def test_demo_script_opens_both_surfaces_with_their_real_roles(self):
        text = _normalized(DEMO_SCRIPT)
        _assert_labeled_near(text, EVIDENCE_HUB_URL, ("evidence", "evaluation"))
        _assert_labeled_near(text, ASSISTANT_URL, ("assistant", "rider", "chat"))
        assert not re.search(
            rf"live demo\s*\(`{re.escape(EVIDENCE_HUB_URL)}`\)",
            text,
            re.I,
        )


def test_smoke_script_defaults_match_documented_production_entrypoints():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert f'DEFAULT_ASSISTANT_BASE_URL="{ASSISTANT_URL.rstrip("/")}"' in text
    assert f'DEFAULT_EVIDENCE_BASE_URL="{EVIDENCE_HUB_URL.rstrip("/")}"' in text
