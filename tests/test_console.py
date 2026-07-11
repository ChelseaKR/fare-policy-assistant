"""Agency operator console (EXP-09): auth, version history/diff, config
actions against the rider Lambda, and the eval-report passthrough.

The AWS-facing actions (pin, embed-config) are exercised against a fake
Lambda client (`_client_factory`), never real AWS, so the suite stays
offline like the rest of the repo's tests.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_chunk
from web import console

AUTH = {"authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("FPA_CONSOLE_TOKEN", "test-token")


def _event(method="GET", path="/console/api/status", headers=None, body=None, qs=None):
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "headers": headers if headers is not None else dict(AUTH),
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": qs,
    }


class FakeLambdaClient:
    def __init__(self, env: dict | None = None):
        self.env = dict(env or {"FPA_PROVIDER": "bedrock"})
        self.updated_with = None

    def get_function_configuration(self, FunctionName):  # noqa: N803 (boto3 shape)
        return {"Environment": {"Variables": dict(self.env)}}

    def update_function_configuration(self, FunctionName, Environment):  # noqa: N803
        self.updated_with = FunctionName
        self.env = dict(Environment["Variables"])
        return {}


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeLambdaClient()
    monkeypatch.setattr(console, "_client_factory", lambda: client)
    monkeypatch.setenv("FPA_RIDER_FUNCTION_NAME", "fare-policy-assistant-demo")
    yield client
    monkeypatch.setattr(console, "_client_factory", None)


class TestAuth:
    def test_missing_token_env_fails_closed(self, monkeypatch):
        monkeypatch.delenv("FPA_CONSOLE_TOKEN", raising=False)
        resp = console.console_handler(_event())
        assert resp["statusCode"] == 401

    def test_missing_header_rejected(self):
        resp = console.console_handler(_event(headers={}))
        assert resp["statusCode"] == 401

    def test_wrong_token_rejected(self):
        resp = console.console_handler(_event(headers={"authorization": "Bearer nope"}))
        assert resp["statusCode"] == 401

    def test_correct_token_accepted(self):
        resp = console.console_handler(_event())
        assert resp["statusCode"] == 200

    def test_console_page_is_public_but_contains_no_operator_data(self):
        resp = console.console_handler(_event(method="GET", path="/console", headers={}))
        assert resp["statusCode"] == 200
        assert "Agency operator console" in resp["body"]
        assert "test-token" not in resp["body"]

    def test_console_api_still_requires_auth_when_page_is_public(self):
        resp = console.console_handler(_event(method="GET", path="/console/api/status", headers={}))
        assert resp["statusCode"] == 401

    def test_console_page_passes_structural_a11y(self):
        from web.a11y import check_html

        assert check_html(console.CONSOLE_HTML) == []

    def test_unknown_route_404(self):
        resp = console.console_handler(_event(path="/console/api/nope"))
        assert resp["statusCode"] == 404


class TestStatus:
    def test_status_reports_corpus_and_pin(self, monkeypatch):
        monkeypatch.delenv("FPA_PINNED_CORPUS_VERSION", raising=False)
        resp = console.console_handler(_event(path="/console/api/status"))
        data = json.loads(resp["body"])
        assert len(data["corpus"]["corpus_version"]) == 12
        assert data["pinned"] is None
        assert data["embed_ancestors"] == "'self'"


class TestVersionsAndDiff:
    @pytest.fixture(autouse=True)
    def history(self, monkeypatch):
        old_chunk = make_chunk(text="Old fare text.")
        new_chunk = make_chunk(text="New fare text, updated.")
        added_chunk = make_chunk(
            chunk_id="mst-fares#new", doc_id="mst-fares-new", text="Brand new program."
        )
        versions = [
            {
                "commit": "aaaaaaaaaaaa",
                "committed_at": "2026-06-01T00:00:00+00:00",
                "corpus_version": "aaaaaaaaaaaa",
                "agencies": ["MST"],
                "documents": 1,
                "chunks": [old_chunk.__dict__],
            },
            {
                "commit": "bbbbbbbbbbbb",
                "committed_at": "2026-07-01T00:00:00+00:00",
                "corpus_version": "bbbbbbbbbbbb",
                "agencies": ["MST"],
                "documents": 2,
                "chunks": [new_chunk.__dict__, added_chunk.__dict__],
            },
        ]
        monkeypatch.setattr(console, "_load_version_history", lambda: versions)

    def test_versions_list_omits_chunk_payload(self):
        resp = console.console_handler(_event(path="/console/api/versions"))
        data = json.loads(resp["body"])
        assert len(data["versions"]) == 2
        assert "chunks" not in data["versions"][0]
        assert data["versions"][0]["corpus_version"] == "aaaaaaaaaaaa"

    def test_diff_by_corpus_version(self):
        resp = console.console_handler(
            _event(
                path="/console/api/diff",
                qs={"from": "aaaaaaaaaaaa", "to": "bbbbbbbbbbbb"},
            )
        )
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["added"] == ["mst-fares-new"]
        assert data["changed"] == ["mst-fares"]
        assert data["removed"] == []

    def test_diff_by_commit(self):
        resp = console.console_handler(
            _event(path="/console/api/diff", qs={"from": "aaaaaaaaaaaa", "to": "bbbbbbbbbbbb"})
        )
        assert resp["statusCode"] == 200

    def test_diff_missing_params_400(self):
        resp = console.console_handler(_event(path="/console/api/diff", qs=None))
        assert resp["statusCode"] == 400

    def test_diff_unknown_version_404(self):
        resp = console.console_handler(
            _event(path="/console/api/diff", qs={"from": "aaaaaaaaaaaa", "to": "doesnotexist"})
        )
        assert resp["statusCode"] == 404


class TestPin:
    def test_pin_updates_rider_env_without_clobbering_other_keys(self, fake_client):
        fake_client.env["FPA_ANSWER_MODEL"] = "keep-me"
        resp = console.console_handler(
            _event(
                method="POST",
                path="/console/api/pin",
                body={"corpus_version": "deadbeefcafe"},
            )
        )
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["pinned"] == "deadbeefcafe"
        assert fake_client.updated_with == "fare-policy-assistant-demo"
        assert fake_client.env["FPA_ANSWER_MODEL"] == "keep-me"
        assert fake_client.env["FPA_PINNED_CORPUS_VERSION"] == "deadbeefcafe"

    def test_pin_rejects_non_hex_version(self, fake_client):
        resp = console.console_handler(
            _event(method="POST", path="/console/api/pin", body={"corpus_version": "not hex!"})
        )
        assert resp["statusCode"] == 400
        assert fake_client.updated_with is None

    def test_pin_missing_field_400(self, fake_client):
        resp = console.console_handler(_event(method="POST", path="/console/api/pin", body={}))
        assert resp["statusCode"] == 400

    def test_pin_without_rider_function_name_500(self, monkeypatch, fake_client):
        monkeypatch.delenv("FPA_RIDER_FUNCTION_NAME", raising=False)
        resp = console.console_handler(
            _event(method="POST", path="/console/api/pin", body={"corpus_version": "abc123"})
        )
        assert resp["statusCode"] == 500


class TestEmbedConfig:
    def test_get_defaults_to_self(self, monkeypatch):
        monkeypatch.delenv("FPA_EMBED_ANCESTORS", raising=False)
        resp = console.console_handler(_event(path="/console/api/embed-config"))
        assert json.loads(resp["body"])["ancestors"] == "'self'"

    def test_post_updates_rider_env(self, fake_client):
        resp = console.console_handler(
            _event(
                method="POST",
                path="/console/api/embed-config",
                body={"ancestors": "https://mst.org https://sbmtd.gov"},
            )
        )
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["ancestors"] == "https://mst.org https://sbmtd.gov"
        assert fake_client.env["FPA_EMBED_ANCESTORS"] == "https://mst.org https://sbmtd.gov"

    def test_post_rejects_non_https_origin(self, fake_client):
        resp = console.console_handler(
            _event(
                method="POST",
                path="/console/api/embed-config",
                body={"ancestors": "http://insecure.example"},
            )
        )
        assert resp["statusCode"] == 400
        assert fake_client.updated_with is None

    def test_post_rejects_empty(self, fake_client):
        resp = console.console_handler(
            _event(method="POST", path="/console/api/embed-config", body={"ancestors": "   "})
        )
        assert resp["statusCode"] == 400


class TestEvalReport:
    def test_no_runs_returns_404(self, monkeypatch, tmp_path):
        monkeypatch.setattr(console.config, "EVAL_RUNS_DIR", tmp_path / "does-not-exist")
        resp = console.console_handler(_event(path="/console/api/eval-report"))
        assert resp["statusCode"] == 404

    def test_latest_run_returned(self, monkeypatch, tmp_path):
        runs = tmp_path / "runs"
        older = runs / "20260101T000000Z"
        newer = runs / "20260201T000000Z"
        older.mkdir(parents=True)
        newer.mkdir(parents=True)
        (older / "summary.json").write_text(json.dumps({"run_at": "old"}), encoding="utf-8")
        (newer / "summary.json").write_text(json.dumps({"run_at": "new"}), encoding="utf-8")
        monkeypatch.setattr(console.config, "EVAL_RUNS_DIR", runs)
        resp = console.console_handler(_event(path="/console/api/eval-report"))
        assert json.loads(resp["body"])["run_at"] == "new"

    def test_skips_run_dir_without_summary(self, monkeypatch, tmp_path):
        runs = tmp_path / "runs"
        incomplete = runs / "20260301T000000Z"
        complete = runs / "20260201T000000Z"
        incomplete.mkdir(parents=True)
        complete.mkdir(parents=True)
        (complete / "summary.json").write_text(json.dumps({"run_at": "complete"}), encoding="utf-8")
        monkeypatch.setattr(console.config, "EVAL_RUNS_DIR", runs)
        resp = console.console_handler(_event(path="/console/api/eval-report"))
        assert json.loads(resp["body"])["run_at"] == "complete"


class TestLoadVersionHistory:
    def test_reads_static_file_when_present(self, monkeypatch, tmp_path):
        path = tmp_path / "version_history.json"
        path.write_text(json.dumps({"versions": [{"commit": "abc", "corpus_version": "abc"}]}))
        monkeypatch.setattr(console, "VERSION_HISTORY_PATH", path)
        assert console._load_version_history() == [{"commit": "abc", "corpus_version": "abc"}]

    def test_falls_back_to_live_git_query_when_file_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(console, "VERSION_HISTORY_PATH", tmp_path / "missing.json")
        called = {}

        def fake_version_history():
            called["ran"] = True
            return [{"commit": "live"}]

        monkeypatch.setattr("assistant.corpus.version_history", fake_version_history)
        assert console._load_version_history() == [{"commit": "live"}]
        assert called.get("ran")


class TestErrorHandling:
    def test_unexpected_exception_is_500_and_logs_no_content(self, monkeypatch, capsys):
        def boom(event):
            raise ValueError("SECRET-detail")

        monkeypatch.setitem(console._ROUTES, ("GET", "/console/api/status"), boom)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 500
        out = capsys.readouterr().out
        assert "SECRET-detail" not in out
        assert "ValueError" in out
