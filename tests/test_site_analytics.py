"""Google Analytics 4 on the evaluation evidence hub (docs/decisions/0033).

Absent with no ID, silent off evals.chelseakr.com and under GPC, DNT or the footer
opt-out, configured exactly as decided everywhere else, admitted by the page's own
Content-Security-Policy through its digest rather than ``'unsafe-inline'``, and on every
page both publishers write.

The loader is run, not grepped: `site_meta.analytics_script()` executes in Node against
stubbed ``window``, ``navigator``, ``document`` and ``localStorage``, because a string
search over a script cannot show what the script does. Every negative control asserts
its sabotage landed (the guard occurred exactly once and is gone from the sabotaged
copy) before asserting the harness caught it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup

from scripts import build_evidence_site as site
from scripts import site_meta
from tests.test_build_evidence_site import _PUBLISH_NOW, _TEMPLATE, _write_manifest

_NODE = shutil.which("node")
MID = "G-Y359BGWN12"
KEY = site_meta.OPT_OUT_STORAGE_KEY

Run = Callable[..., dict[str, Any]]

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>t</title>
</head>
<body><main><h1>t</h1></main></body>
</html>
"""


def _policy(page: str) -> dict[str, list[str]]:
    match = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]*)"', page)
    assert match is not None
    return {
        tokens[0]: tokens[1:]
        for tokens in (part.split() for part in match.group(1).split(";"))
        if tokens
    }


# --- configuration ---


def test_the_committed_id_and_host_are_the_decided_ones() -> None:
    assert site_meta.measurement_id() == MID
    assert site_meta.PRODUCTION_HOST == "evals.chelseakr.com"
    assert site_meta.GA4_DATA_RETENTION == "14 months"
    assert len(set(site_meta.ANALYTICS_DENIED_REGIONS)) == 32


@pytest.mark.parametrize("value", ["UA-12345-1", "G-", "g-y359bgwn12", 'G-X"};alert(1);//'])
def test_a_malformed_id_stops_the_publish(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(site_meta, "GA4_MEASUREMENT_ID", value)
    with pytest.raises(ValueError, match="not a GA4 measurement ID"):
        site_meta.with_analytics(_PAGE)


def test_with_no_id_there_is_no_loader_no_control_and_no_policy_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_meta, "GA4_MEASUREMENT_ID", "")
    page = site_meta.with_analytics(_PAGE)
    assert "<script" not in page and "googletagmanager" not in page
    assert "data-analytics-choice" not in page
    assert _policy(page) == _policy(_PAGE)
    assert "This site runs no analytics and sets no cookies." in page
    privacy = site_meta.privacy_html(card=False)
    assert "Nothing. This site runs no analytics" in privacy
    assert "Google Analytics" not in privacy


# --- the page transform ---


def test_a_page_with_no_script_policy_gains_exactly_what_ga_needs() -> None:
    page = site_meta.with_analytics(_PAGE)
    policy = _policy(page)
    script = site_meta.analytics_script()
    assert policy["script-src"] == [site_meta.script_source(script), site_meta.GTAG_ORIGIN]
    assert policy["connect-src"] == list(site_meta.COLLECTION_ORIGINS)
    assert policy["img-src"] == list(site_meta.COLLECTION_ORIGINS)
    assert policy["default-src"] == ["'none'"]
    assert "'unsafe-inline'" not in policy["script-src"]
    head, _, body = page.partition("</head>")
    assert head.count(f"<script>{script}</script>") == 1
    assert body.count("data-analytics-choice") == 1
    assert body.index("data-analytics-choice") > body.index("</main>")


def test_an_existing_directive_is_extended_never_replaced() -> None:
    page = _PAGE.replace("default-src 'none';", "default-src 'none'; img-src 'self';")
    policy = _policy(site_meta.with_analytics(page))
    assert policy["img-src"] == ["'self'", *site_meta.COLLECTION_ORIGINS]


@pytest.mark.parametrize("page", ["<html><body></body></html>", "<html><head></head></html>"])
def test_a_page_it_cannot_place_both_halves_on_is_refused(page: str) -> None:
    with pytest.raises(ValueError, match="no </head> or no </body>"):
        site_meta.with_analytics(page)


# --- the dispatch publisher's output ---


@pytest.fixture
def rendered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The same fixed clock tests/test_build_evidence_site.py renders under, so the
    # fixture evidence is inside its freshness budget.
    monkeypatch.setattr(site, "_utc_now", lambda: _PUBLISH_NOW)
    return site.render_evidence_site(
        manifest_path=_write_manifest(tmp_path / "public.json"),
        template_path=_TEMPLATE,
        output_dir=tmp_path / "site",
    )


def test_every_page_the_dispatch_publisher_writes_carries_ga_and_the_control(
    rendered: Path,
) -> None:
    pages = sorted(path.name for path in rendered.glob("*.html"))
    assert pages == ["index.html", "privacy.html", "report.html"]
    script = site_meta.analytics_script()
    for name in pages:
        page = (rendered / name).read_text(encoding="utf-8")
        head, _, body = page.partition("</head>")
        assert head.count(f"<script>{script}</script>") == 1, name
        assert body.count("data-analytics-choice") == 1, name
        assert 'href="privacy.html"' in body, name
        policy = _policy(page)
        assert site_meta.script_source(script) in policy["script-src"], name
        assert "'unsafe-inline'" not in policy["script-src"], name
        # gtag.js is only ever appended by the guarded loader, never a static tag.
        assert BeautifulSoup(page, "html.parser").find_all("script", src=True) == [], name


def test_the_data_files_carry_no_ga(rendered: Path) -> None:
    for name in ("public-evidence.json", "release.json", "robots.txt", "sitemap.xml"):
        text = (rendered / name).read_text(encoding="utf-8")
        assert "googletagmanager" not in text and MID not in text, name


def test_the_privacy_page_describes_ga(rendered: Path) -> None:
    text = " ".join(
        BeautifulSoup((rendered / "privacy.html").read_text(encoding="utf-8"), "html.parser")
        .get_text(" ")
        .split()
    )
    for fact in (
        "Google Analytics 4",
        "Global Privacy Control",
        "Do Not Track",
        "Opt out of analytics",
        "Opt back in",
        KEY,
        "_ga",
        "two years",
        "cookieless",
        "Switzerland",
        "keeps it for 14 months",
        "Google signals and ad personalization are off",
    ):
        assert fact in text, fact
    for fact in ("Suiza", "14 meses", "sin cookies", "desactivar la analítica"):
        assert fact in text, fact


# --- the loader, run in Node ---

HARNESS = r"""
const fs = require("fs");
const sc = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const code = fs.readFileSync(process.argv[3], "utf8");
const appended = [];
const listeners = {};
const data = Object.assign({}, sc.storage || {});
const blocked = () => { throw new Error("storage blocked"); };
const storage = sc.storageThrows
  ? { getItem: blocked, setItem: blocked, removeItem: blocked }
  : {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null),
      setItem: (k, v) => { data[k] = String(v); },
      removeItem: (k) => { delete data[k]; },
    };
const button = {
  hidden: true,
  textContent: "Opt out of analytics",
  onclick: null,
  addEventListener(t, f) { if (t === "click") this.onclick = f; },
};
const status = { textContent: "" };
const box = { hidden: true, querySelector: (s) => (s === "button" ? button : status) };
const document = {
  referrer: sc.referrer || "",
  head: { appendChild: (e) => appended.push(e) },
  createElement: (tag) => ({ tagName: tag, async: false, src: "" }),
  addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
  querySelector: (s) => (s === "[data-analytics-choice]" ? box : null),
};
const navigator = Object.assign({}, sc.navigator || {});
const url = new URL(sc.url || "https://evals.chelseakr.com/");
const window = {
  location: {
    hostname: url.hostname, origin: url.origin, pathname: url.pathname, search: url.search,
  },
};
if (sc.windowDoNotTrack !== undefined) window.doNotTrack = sc.windowDoNotTrack;
Object.defineProperty(window, "localStorage", {
  get() { if (sc.storageGetterThrows) throw new Error("denied"); return storage; },
});
new Function("window", "navigator", "document", code)(window, navigator, document);
const snap = () => ({
  box: !box.hidden, button: !button.hidden, text: button.textContent, status: status.textContent,
  stored: Object.assign({}, data), disabled: window[sc.gaDisable] === true,
});
const states = [];
if (sc.domReady) {
  (listeners.DOMContentLoaded || []).forEach((f) => f());
  states.push(snap());
  for (let i = 0; i < (sc.clicks || 0); i++) { button.onclick(); states.push(snap()); }
}
const plain = (x) => (x instanceof Date ? "<date>" : x);
process.stdout.write(JSON.stringify({
  dataLayer: window.dataLayer ? window.dataLayer.map((a) => Array.from(a).map(plain)) : null,
  appended: appended.map((e) => ({ tag: e.tagName, async: e.async, src: e.src })),
  states,
}));
"""


@pytest.fixture
def run(tmp_path: Path) -> Iterator[Run]:
    if _NODE is None:
        pytest.fail("Node is required to run the GA4 loader's behavior tests")
    node = _NODE
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    counter = iter(range(1000))

    def _run(source: str | None = None, **scenario: Any) -> dict[str, Any]:
        index = next(counter)
        script = tmp_path / f"loader-{index}.js"
        script.write_text(
            site_meta.analytics_script() if source is None else source, encoding="utf-8"
        )
        scenario.setdefault("gaDisable", f"ga-disable-{MID}")
        spec = tmp_path / f"scenario-{index}.json"
        spec.write_text(json.dumps(scenario), encoding="utf-8")
        done = subprocess.run(
            [node, str(harness), str(spec), str(script)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        result: dict[str, Any] = json.loads(done.stdout)
        return result

    yield _run


def _loaded(result: dict[str, Any]) -> bool:
    return result["dataLayer"] is not None or bool(result["appended"])


NOTHING_LOADS: dict[str, dict[str, Any]] = {
    "another host": {"url": "https://chelseakr.github.io/fare-policy-assistant/"},
    "localhost": {"url": "http://localhost:8000/"},
    "127.0.0.1": {"url": "http://127.0.0.1:8000/report.html"},
    "GPC": {"navigator": {"globalPrivacyControl": True}},
    "navigator.doNotTrack": {"navigator": {"doNotTrack": "1"}},
    "window.doNotTrack": {"windowDoNotTrack": "1"},
    "navigator.msDoNotTrack": {"navigator": {"msDoNotTrack": "1"}},
    'doNotTrack "yes"': {"navigator": {"doNotTrack": "yes"}},
    "opted out": {"storage": {KEY: "1"}},
}


@pytest.mark.parametrize("case", sorted(NOTHING_LOADS))
def test_nothing_loads_off_host_under_gpc_or_dnt_or_opted_out(run: Run, case: str) -> None:
    result = run(**NOTHING_LOADS[case])
    assert result["dataLayer"] is None
    assert result["appended"] == []


def test_on_the_production_host_ga_loads_with_the_decided_configuration(run: Run) -> None:
    result = run(
        url="https://evals.chelseakr.com/report.html?utm_source=news&x=1#suites",
        referrer="https://www.example.org/some/path?q=who",
    )
    assert result["appended"] == [
        {
            "tag": "script",
            "async": True,
            "src": f"https://www.googletagmanager.com/gtag/js?id={MID}",
        }
    ]
    denied_ads = {"ad_storage": "denied", "ad_user_data": "denied", "ad_personalization": "denied"}
    regions = list(site_meta.ANALYTICS_DENIED_REGIONS)
    assert result["dataLayer"] == [
        ["consent", "default", {**denied_ads, "analytics_storage": "denied", "region": regions}],
        ["consent", "default", {**denied_ads, "analytics_storage": "granted"}],
        ["js", "<date>"],
        [
            "config",
            MID,
            {
                "allow_google_signals": False,
                "allow_ad_personalization_signals": False,
                "page_location": "https://evals.chelseakr.com/report.html?utm_source=news",
                "page_referrer": "https://www.example.org/",
            },
        ],
    ]


@pytest.mark.parametrize(
    "scenario",
    [
        {"storage": {KEY: "0"}},
        {"storage": {"some-other-site:analytics-opt-out": "1"}},
        {"storageThrows": True},
        {"storageGetterThrows": True},
        {"navigator": {"doNotTrack": "0", "globalPrivacyControl": False}},
    ],
)
def test_anything_short_of_a_real_signal_or_opt_out_still_loads(
    run: Run, scenario: dict[str, Any]
) -> None:
    assert _loaded(run(**scenario))


def test_the_footer_control_opts_out_and_back_in_and_is_remembered(run: Run) -> None:
    first, out, back = run(domReady=True, clicks=2)["states"]
    assert first["box"] and first["button"] and first["text"] == "Opt out of analytics"
    assert out["text"] == "Opt back in"
    assert out["stored"] == {KEY: "1"}
    assert out["disabled"] is True
    assert out["status"].startswith("Opted out.")
    assert back["text"] == "Opt out of analytics"
    assert back["stored"] == {}
    later = run(domReady=True, storage={KEY: "1"})
    assert not _loaded(later)
    assert later["states"][0]["text"] == "Opt back in"


@pytest.mark.parametrize(
    ("scenario", "starts"),
    [
        ({"navigator": {"globalPrivacyControl": True}}, "Analytics is off"),
        ({"storageThrows": True}, "This browser is blocking site storage"),
    ],
)
def test_under_a_signal_or_blocked_storage_the_button_is_hidden_and_says_why(
    run: Run, scenario: dict[str, Any], starts: str
) -> None:
    state = run(domReady=True, **scenario)["states"][0]
    assert state["box"] is True and state["button"] is False
    assert state["status"].startswith(starts)


SABOTAGE: dict[str, tuple[str, dict[str, Any]]] = {
    "hostname": (
        '  if (w.location.hostname !== "evals.chelseakr.com") return;\n',
        {"url": "http://127.0.0.1:8000/"},
    ),
    "GPC": (
        "  if (n.globalPrivacyControl === true) return;\n",
        {"navigator": {"globalPrivacyControl": True}},
    ),
    "DNT": ('  if (dnt === "1" || dnt === "yes") return;\n', {"navigator": {"doNotTrack": "1"}}),
    "opt-out": ("  if (optedOut()) return;\n", {"storage": {KEY: "1"}}),
}


@pytest.mark.parametrize("guard", sorted(SABOTAGE))
def test_negative_control_removing_a_guard_is_caught(run: Run, guard: str) -> None:
    line, scenario = SABOTAGE[guard]
    source = site_meta.analytics_script()
    assert source.count(line) == 1, f"the {guard} guard is not in the loader to remove"
    broken = source.replace(line, "", 1)
    assert broken != source and line not in broken  # the sabotage landed
    assert not _loaded(run(**scenario)), "the intact loader should load nothing here"
    assert _loaded(run(broken, **scenario)), f"removing the {guard} guard went unnoticed"


def test_negative_control_a_digest_for_other_bytes_is_caught() -> None:
    """The policy must admit the loader actually inlined, not some other text."""
    page = site_meta.with_analytics(_PAGE)
    script = site_meta.analytics_script()
    broken = page.replace(f"<script>{script}</script>", f"<script>{script} </script>", 1)
    assert broken != page  # the sabotage landed
    inline = BeautifulSoup(broken, "html.parser").find("script")
    assert inline is not None and inline.string is not None
    assert site_meta.script_source(inline.string) not in _policy(broken)["script-src"]
