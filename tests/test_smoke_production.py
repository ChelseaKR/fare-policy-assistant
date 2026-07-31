"""Exercise the production smoke script through a deterministic fake transport."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from assistant import config

SMOKE_SCRIPT = config.REPO_ROOT / "scripts" / "smoke-production.sh"


def _install_fake_curl(tmp_path: Path) -> Path:
    """Install a curl-shaped transport so the shell is tested without sockets."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

args = sys.argv[1:]
headers_path = pathlib.Path(args[args.index("--dump-header") + 1])
body_path = pathlib.Path(args[args.index("--output") + 1])
url = args[-1]
payload = args[args.index("--data") + 1] if "--data" in args else ""
disabled_documents = [
    item
    for item in os.environ.get("FAKE_DISABLED_DOC_IDS", "yolobus-fares").split(",")
    if item
]
delay_seconds = float(os.environ.get("FAKE_CURL_DELAY_SECONDS", "0"))
if delay_seconds:
    time.sleep(delay_seconds)

security = [
    "cache-control: no-store",
    "content-security-policy: default-src 'none'; connect-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'self'",
    "referrer-policy: no-referrer",
    "x-content-type-options: nosniff",
]

if url.startswith("http://evidence.test"):
    content_type = "text/html"
    body = "<html><title>Public evaluation evidence</title></html>"
    response_headers = []
elif url.endswith("/version"):
    content_type = "application/json"
    body = json.dumps({
        "corpus_version": "test-corpus",
        "as_of": "2026-07-29",
        "agencies": ["MST"],
        "matches_pin": True,
        "disabled_documents": disabled_documents,
    })
    response_headers = security + ["x-frame-options: DENY"]
elif url.endswith("/api/ask"):
    content_type = "application/json"
    question = json.loads(payload)["question"]
    if "Social Security" in question:
        body = json.dumps({
            "answer": "Please leave personal details out of your question.",
            "kind": "refused_input",
            "citations": [],
        })
    elif "Yolobus" in question:
        if "yolobus-fares" in disabled_documents:
            body = json.dumps({
                "answer": "I do not have current published support for that answer.",
                "kind": "refused_no_support",
                "citations": [],
            })
        else:
            body = json.dumps({
                "answer": "The reviewed source is active.",
                "kind": "answered",
                "citations": [{
                    "agency": "Yolobus",
                    "title": "Fares",
                    "url": "https://yolobus.com/fares/",
                    "fetch_date": "2026-07-29",
                }],
            })
    else:
        body = json.dumps({
            "answer": "Bring published proof.",
            "kind": "answered",
            "corpus_version": "test-corpus",
            "as_of_date": "2026-07-29",
            "citations": [{
                "agency": "MST",
                "title": "Veteran fares",
                "url": "https://mst.org/fares/",
                "fetch_date": "2026-07-29",
            }],
        })
    response_headers = security + ["x-frame-options: DENY"]
else:
    content_type = "text/html"
    markers = {
        "/": "Transit Fare Policy Assistant",
        "/offline": "Offline fare reference",
        "/guide": "Which fare applies to me?",
        "/embed": "Transit fare policy assistant",
    }
    path = "/" + url.split("/", 3)[-1] if url.count("/") >= 3 else "/"
    body = f"<html><title>{markers[path]}</title></html>"
    response_headers = security
    if path != "/embed":
        response_headers.append("x-frame-options: DENY")

headers_path.write_text(
    "\\r\\n".join(["HTTP/1.1 200 OK", f"content-type: {content_type}", *response_headers, "", ""])
)
body_path.write_text(body)
sys.stdout.write("200")
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    return bin_dir


def test_smoke_script_covers_both_public_surfaces_without_network(tmp_path):
    fake_bin = _install_fake_curl(tmp_path)
    result = subprocess.run(
        [
            str(SMOKE_SCRIPT),
            "--evidence-base-url",
            "http://evidence.test",
            "--assistant-base-url",
            "http://assistant.test",
            "--connect-timeout",
            "2",
            "--max-time",
            "5",
            "--allow-legacy-release-identity",
        ],
        cwd=config.REPO_ROOT,
        env={**os.environ, "LC_ALL": "C", "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "smoke: ok: assistant PII refusal" in result.stdout
    assert "smoke: ok: assistant Yolobus containment" in result.stdout
    assert "smoke: ok: assistant safe answer" in result.stdout
    assert result.stdout.rstrip().endswith("smoke: PASS")


def test_smoke_script_rejects_an_invalid_base_url_before_curl():
    result = subprocess.run(
        [str(SMOKE_SCRIPT), "--assistant-base-url", "not-a-url"],
        cwd=config.REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "assistant base URL must be an absolute http(s) URL" in result.stderr


def test_assistant_only_ignores_an_irrelevant_invalid_evidence_url(tmp_path):
    fake_bin = _install_fake_curl(tmp_path)
    result = subprocess.run(
        [
            str(SMOKE_SCRIPT),
            "--assistant-only",
            "--assistant-base-url",
            "http://assistant.test",
            "--allow-legacy-release-identity",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "LC_ALL": "C",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FPA_SMOKE_EVIDENCE_BASE_URL": "not-a-url",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "evidence=" not in result.stdout
    assert result.stdout.rstrip().endswith("smoke: PASS")


def test_explicit_empty_disabled_documents_skips_yolobus_containment(tmp_path):
    fake_bin = _install_fake_curl(tmp_path)
    result = subprocess.run(
        [
            str(SMOKE_SCRIPT),
            "--assistant-only",
            "--assistant-base-url",
            "http://assistant.test",
            "--expected-disabled-docs",
            "",
            "--allow-legacy-release-identity",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "LC_ALL": "C",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_DISABLED_DOC_IDS": "",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Yolobus containment" not in result.stdout
    assert result.stdout.rstrip().endswith("smoke: PASS")


def test_default_disabled_document_requirement_detects_missing_containment(tmp_path):
    fake_bin = _install_fake_curl(tmp_path)
    result = subprocess.run(
        [
            str(SMOKE_SCRIPT),
            "--assistant-only",
            "--assistant-base-url",
            "http://assistant.test",
            "--allow-legacy-release-identity",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "LC_ALL": "C",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_DISABLED_DOC_IDS": "",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "invalid explicit legacy release identity" in result.stderr


def test_deadline_terminates_a_slow_public_request(tmp_path):
    fake_bin = _install_fake_curl(tmp_path)
    deadline = int(time.time()) + 2
    started = time.monotonic()
    result = subprocess.run(
        [
            str(SMOKE_SCRIPT),
            "--assistant-only",
            "--assistant-base-url",
            "http://assistant.test",
            "--deadline-epoch",
            str(deadline),
            "--allow-legacy-release-identity",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "LC_ALL": "C",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_CURL_DELAY_SECONDS": "30",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    elapsed = time.monotonic() - started

    assert result.returncode != 0
    assert elapsed < 5
    assert "operation deadline" in result.stderr
