"""Agency operator console (EXP-09): auth, version history/diff, immutable
live-release status, and the eval-report passthrough.

AWS reads are exercised against a fake Lambda client (`_client_factory`), never
real AWS. Configuration POSTs are held fail-closed until an approved promotion
workflow exists.
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
    monkeypatch.delenv("FPA_CONSOLE_TOKEN_PARAMETER_NAME", raising=False)
    console._reset_console_token_for_tests()
    yield
    console._reset_console_token_for_tests()
    monkeypatch.setattr(console, "_ssm_client_factory", None)


def _event(method="GET", path="/console/api/status", headers=None, body=None, qs=None):
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "headers": headers if headers is not None else dict(AUTH),
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": qs,
    }


class FakeLambdaClient:
    def __init__(self, env: dict | None = None, *, version: str = "4"):
        self.env = dict(
            env
            or {
                "FPA_PROVIDER": "bedrock",
                "FPA_PINNED_CORPUS_VERSION": "0938fff0539a",
                "FPA_DISABLED_DOC_IDS": "yolobus-fares",
            }
        )
        self.version = version
        self.calls: list[tuple] = []

    def get_alias(self, FunctionName, Name):  # noqa: N803 (boto3 shape)
        self.calls.append(("get_alias", FunctionName, Name))
        return {"FunctionVersion": self.version, "RevisionId": "alias-revision"}

    def get_function_configuration(  # noqa: N803 (boto3 shape)
        self, FunctionName, Qualifier=None
    ):
        self.calls.append(("get_function_configuration", FunctionName, Qualifier))
        return {"Version": self.version, "Environment": {"Variables": dict(self.env)}}


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
        console._reset_console_token_for_tests()
        resp = console.console_handler(_event())
        assert resp["statusCode"] == 401

    def test_ssm_parameter_token_is_accepted(self, monkeypatch, fake_client):
        class FakeSSM:
            def get_parameter(self, **kwargs):
                assert kwargs == {"Name": "/fare-policy-assistant/token", "WithDecryption": True}
                return {"Parameter": {"Value": "parameter-token"}}

        monkeypatch.delenv("FPA_CONSOLE_TOKEN", raising=False)
        monkeypatch.setenv("FPA_CONSOLE_TOKEN_PARAMETER_NAME", "/fare-policy-assistant/token")
        monkeypatch.setattr(console, "_ssm_client_factory", FakeSSM)
        console._reset_console_token_for_tests()
        event = _event(headers={"authorization": "Bearer parameter-token"})
        assert console.console_handler(event)["statusCode"] == 200

    def test_ssm_failure_fails_closed(self, monkeypatch):
        class BrokenSSM:
            def get_parameter(self, **kwargs):
                raise RuntimeError("unavailable")

        monkeypatch.delenv("FPA_CONSOLE_TOKEN", raising=False)
        monkeypatch.setenv("FPA_CONSOLE_TOKEN_PARAMETER_NAME", "/fare-policy-assistant/token")
        monkeypatch.setattr(console, "_ssm_client_factory", BrokenSSM)
        console._reset_console_token_for_tests()
        assert console.console_handler(_event())["statusCode"] == 401

    def test_missing_header_rejected(self):
        resp = console.console_handler(_event(headers={}))
        assert resp["statusCode"] == 401

    def test_wrong_token_rejected(self):
        resp = console.console_handler(_event(headers={"authorization": "Bearer nope"}))
        assert resp["statusCode"] == 401

    def test_correct_token_accepted(self, fake_client):
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

    def test_console_treats_pass_rate_as_zero_to_one_hundred_percentage(self):
        # Eval reports already store pass_rate on a 0–100 scale. The browser must
        # format that value directly rather than turn 95.2 into 9520%.
        assert "Number(s.pass_rate).toFixed(1)" in console.CONSOLE_HTML
        assert "s.pass_rate * 100" not in console.CONSOLE_HTML
        assert "s.pass_rate*100" not in console.CONSOLE_HTML

    def test_unknown_route_404(self):
        resp = console.console_handler(_event(path="/console/api/nope"))
        assert resp["statusCode"] == 404


class TestStatus:
    def test_status_reads_the_qualified_live_release(self, fake_client):
        resp = console.console_handler(_event(path="/console/api/status"))
        data = json.loads(resp["body"])
        assert len(data["available_corpus"]["corpus_version"]) == 12
        assert data["live"] == {
            "alias": "live",
            "function_version": "4",
            "pinned_corpus_version": "0938fff0539a",
            "disabled_documents": ["yolobus-fares"],
            "embed_ancestors": "'self'",
        }
        assert fake_client.calls == [
            ("get_alias", "fare-policy-assistant-demo", "live"),
            ("get_function_configuration", "fare-policy-assistant-demo", "live"),
        ]

    def test_status_fails_if_alias_changes_during_read(self, monkeypatch, fake_client):
        def changed_config(FunctionName, Qualifier=None):  # noqa: N803
            return {"Version": "5", "Environment": {"Variables": {}}}

        monkeypatch.setattr(fake_client, "get_function_configuration", changed_config)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 500

    def test_status_fails_closed_for_weighted_live_alias(self, monkeypatch, fake_client):
        def weighted_alias(FunctionName, Name):  # noqa: N803
            return {
                "FunctionVersion": "4",
                "RoutingConfig": {"AdditionalVersionWeights": {"3": 0.1}},
            }

        monkeypatch.setattr(fake_client, "get_alias", weighted_alias)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 500


class TestConsoleDeploymentPolicy:
    def test_console_is_read_only_and_bound_to_live_alias(self):
        text = (console.config.REPO_ROOT / "infra" / "deploy-console.sh").read_text(
            encoding="utf-8"
        )
        assert "lambda:GetAlias" in text
        assert "lambda:GetFunctionConfiguration" in text
        assert "lambda:UpdateFunctionConfiguration" not in text
        assert "FPA_RIDER_ALIAS=live" in text
        assert 'function:$RIDER_FN:live"' in text
        live_statement = text.split('"Action": "lambda:GetFunctionConfiguration"', 1)[1].split(
            "}", 1
        )[0]
        assert 'function:$RIDER_FN:live"' in live_statement
        assert 'function:$RIDER_FN"' not in live_statement


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
    def test_pin_is_rejected_until_an_approved_promotion_workflow_exists(self, fake_client):
        resp = console.console_handler(
            _event(
                method="POST",
                path="/console/api/pin",
                body={"corpus_version": "deadbeefcafe"},
            )
        )
        assert resp["statusCode"] == 409
        data = json.loads(resp["body"])
        assert data["code"] == "immutable_release_requires_promotion"
        assert fake_client.calls == []


class TestEmbedConfig:
    def test_get_reads_live_alias_configuration(self, fake_client):
        fake_client.env["FPA_EMBED_ANCESTORS"] = "https://mst.org"
        resp = console.console_handler(_event(path="/console/api/embed-config"))
        assert json.loads(resp["body"])["ancestors"] == "https://mst.org"

    def test_post_is_rejected_until_an_approved_promotion_workflow_exists(self, fake_client):
        resp = console.console_handler(
            _event(
                method="POST",
                path="/console/api/embed-config",
                body={"ancestors": "https://mst.org https://sbmtd.gov"},
            )
        )
        assert resp["statusCode"] == 409
        data = json.loads(resp["body"])
        assert data["code"] == "immutable_release_requires_promotion"
        assert fake_client.calls == []


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
