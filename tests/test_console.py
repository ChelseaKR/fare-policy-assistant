"""Agency operator console (EXP-09): auth, version history/diff, immutable
live-release status, and the eval-report passthrough.

AWS reads are exercised against a fake Lambda client (`_client_factory`), never
real AWS. Configuration POSTs are held fail-closed until an approved promotion
workflow exists.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests import test_promotion_evidence as evidence_fixtures
from tests.conftest import make_chunk
from web import console

AUTH = {"authorization": "Bearer test-token"}
SOURCE = evidence_fixtures._SOURCE_REVISION
CONFIG = evidence_fixtures._CONFIG_VERSION
CONTENT = evidence_fixtures._CONTENT_VERSION
SNAPSHOT = evidence_fixtures._SNAPSHOT_VERSION
RELEASE = evidence_fixtures._RELEASE_VERSION
CORPUS = evidence_fixtures._CORPUS_VERSION
ARTIFACT = evidence_fixtures._ARTIFACT_SHA256
NOW = datetime(2026, 7, 30, 21, 0, tzinfo=UTC)


def _runtime_payload(**changes):
    payload = {
        "identity_status": "verified",
        "function_version": "11",
        "source_revision": SOURCE,
        "config_version": CONFIG,
        "content_version": CONTENT,
        "snapshot_version": SNAPSHOT,
        "release_version": RELEASE,
        "corpus_version": CORPUS,
        "artifact_code_sha256": ARTIFACT,
        "as_of": "2026-07-30",
    }
    payload.update(changes)
    return payload


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
    def __init__(self, env: dict | None = None, *, version: str = "11"):
        default_env = {
            "FPA_PROVIDER": "bedrock",
            "FPA_SOURCE_REVISION": SOURCE,
            "FPA_CONFIG_VERSION": CONFIG,
            "FPA_PINNED_CONTENT_VERSION": CONTENT,
            "FPA_PINNED_SNAPSHOT_VERSION": SNAPSHOT,
            "FPA_RELEASE_VERSION": RELEASE,
            "FPA_PINNED_CORPUS_VERSION": CORPUS,
            "FPA_ARTIFACT_CODE_SHA256": ARTIFACT,
            "FPA_DISABLED_DOC_IDS": "yolobus-fares",
        }
        self.env = dict(default_env if env is None else env)
        self.version = version
        self.code_sha256 = ARTIFACT
        self.aliases: list[dict] = []
        self.calls: list[tuple] = []

    def get_alias(self, FunctionName, Name):  # noqa: N803 (boto3 shape)
        self.calls.append(("get_alias", FunctionName, Name))
        if self.aliases:
            return self.aliases.pop(0)
        return {"FunctionVersion": self.version, "RevisionId": "alias-revision"}

    def get_function_configuration(  # noqa: N803 (boto3 shape)
        self, FunctionName, Qualifier=None
    ):
        self.calls.append(("get_function_configuration", FunctionName, Qualifier))
        return {
            "Version": self.version,
            "CodeSha256": self.code_sha256,
            "Environment": {"Variables": dict(self.env)},
        }


class FakeHTTPResponse:
    def __init__(self, payload=None, *, status_code=200, json_error=None):
        self.payload = _runtime_payload() if payload is None else payload
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeHTTPClient:
    def __init__(self):
        self.response = FakeHTTPResponse()
        self.error: Exception | None = None
        self.calls: list[str] = []
        self.closed = False

    def get(self, url):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch, tmp_path):
    client = FakeLambdaClient()
    http_client = FakeHTTPClient()
    promoted = evidence_fixtures._write_evidence(tmp_path / "evals" / "promoted")
    monkeypatch.setattr(console, "_client_factory", lambda: client)
    monkeypatch.setattr(console, "_http_client_factory", lambda: http_client)
    monkeypatch.setattr(console, "_clock", lambda: NOW)
    monkeypatch.setattr(console, "PROMOTED_EVAL_MAX_AGE_SECONDS", 24 * 60 * 60)
    monkeypatch.setattr(console, "PROMOTED_EVAL_SUMMARY_PATH", promoted.summary_path)
    monkeypatch.setattr(console, "PROMOTED_EVAL_RESULTS_PATH", promoted.results_path)
    monkeypatch.setattr(console, "PROMOTED_EVAL_ATTESTATION_PATH", promoted.promotion_path)
    monkeypatch.setattr(
        console,
        "corpus_summary",
        lambda: {
            "corpus_version": CORPUS,
            "content_version": CONTENT,
            "as_of": "2026-07-30",
            "agencies": ["MST", "SBMTD"],
            "documents": 2,
            "chunks": 3,
        },
    )
    monkeypatch.setenv("FPA_RIDER_FUNCTION_NAME", "fare-policy-assistant-demo")
    monkeypatch.setenv("FPA_RIDER_BASE_URL", "https://fare.example")
    client.http = http_client
    client.promoted = promoted
    yield client
    monkeypatch.setattr(console, "_client_factory", None)
    monkeypatch.setattr(console, "_http_client_factory", None)


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

    def test_console_clears_old_status_and_score_content_before_requests(self):
        assert 'body.textContent = "Checking AWS' in console.CONSOLE_HTML
        assert 'evalBody.textContent = "Loading promoted evaluation' in console.CONSOLE_HTML
        assert "Mismatches:" in console.CONSOLE_HTML
        assert '"+ s.passed +' not in console.CONSOLE_HTML
        assert '"+ s.total +' not in console.CONSOLE_HTML

    def test_unknown_route_404(self):
        resp = console.console_handler(_event(path="/console/api/nope"))
        assert resp["statusCode"] == 404


class TestStatus:
    def test_status_requires_all_observations_and_promoted_evidence(self, fake_client):
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["state"] == "coherent"
        assert data["status"] == "verified"
        assert data["mismatches"] == []
        assert len(data["available_corpus"]["corpus_version"]) == 12
        assert data["live"]["alias"] == "live"
        assert data["live"]["function_version"] == "11"
        assert data["live"]["release_version"] == RELEASE
        assert data["live"]["artifact_code_sha256"] == ARTIFACT
        assert data["live"]["pinned_corpus_version"] == CORPUS
        assert data["live"]["disabled_documents"] == ["yolobus-fares"]
        assert data["live"]["embed_ancestors"] == "'self'"
        assert data["evaluation"]["fresh"] is True
        assert fake_client.calls == [
            ("get_alias", "fare-policy-assistant-demo", "live"),
            ("get_function_configuration", "fare-policy-assistant-demo", "11"),
            ("get_alias", "fare-policy-assistant-demo", "live"),
        ]
        assert fake_client.http.calls == ["https://fare.example/version"]
        assert fake_client.http.closed is True

    def test_status_fails_if_config_does_not_match_alias(self, monkeypatch, fake_client):
        def changed_config(FunctionName, Qualifier=None):  # noqa: N803
            return {
                "Version": "5",
                "CodeSha256": ARTIFACT,
                "Environment": {"Variables": dict(fake_client.env)},
            }

        monkeypatch.setattr(fake_client, "get_function_configuration", changed_config)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["aws.function_version"]

    def test_status_fails_closed_for_weighted_live_alias(self, monkeypatch, fake_client):
        def weighted_alias(FunctionName, Name):  # noqa: N803
            return {
                "FunctionVersion": "11",
                "RoutingConfig": {"AdditionalVersionWeights": {"3": 0.1}},
            }

        monkeypatch.setattr(fake_client, "get_alias", weighted_alias)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["alias.routing_weights"]

    def test_status_fails_if_alias_changes_after_runtime_observation(self, fake_client):
        fake_client.aliases = [
            {"FunctionVersion": "11", "RevisionId": "before"},
            {"FunctionVersion": "5", "RevisionId": "after"},
        ]
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert "alias.function_version" in json.loads(resp["body"])["mismatches"]

    def test_status_fails_if_alias_revision_changes_during_observation(self, fake_client):
        fake_client.aliases = [
            {"FunctionVersion": "11", "RevisionId": "before"},
            {"FunctionVersion": "11", "RevisionId": "after"},
        ]
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["alias.revision"]

    def test_status_fails_if_final_alias_becomes_weighted(self, fake_client):
        fake_client.aliases = [
            {"FunctionVersion": "11", "RevisionId": "same"},
            {
                "FunctionVersion": "11",
                "RevisionId": "same",
                "RoutingConfig": {"AdditionalVersionWeights": {"3": 0.01}},
            },
        ]
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["alias.routing_weights"]

    @pytest.mark.parametrize("version", ["", "0", "$LATEST", "4.0", 4])
    def test_status_rejects_non_numeric_immutable_alias_versions(self, fake_client, version):
        fake_client.aliases = [{"FunctionVersion": version, "RevisionId": "revision"}]
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["alias.function_version"]

    @pytest.mark.parametrize(
        ("environment_name", "bad_value", "mismatch"),
        [
            ("FPA_SOURCE_REVISION", None, "aws.source_revision"),
            ("FPA_SOURCE_REVISION", "a" * 39, "aws.source_revision"),
            ("FPA_CONFIG_VERSION", "B" * 64, "aws.config_version"),
            ("FPA_PINNED_CONTENT_VERSION", "", "aws.content_version"),
            ("FPA_PINNED_SNAPSHOT_VERSION", "d" * 63, "aws.snapshot_version"),
            ("FPA_RELEASE_VERSION", "not-a-digest", "aws.release_version"),
            ("FPA_PINNED_CORPUS_VERSION", "a" * 13, "aws.corpus_version"),
            ("FPA_ARTIFACT_CODE_SHA256", "not-base64", "aws.artifact_code_sha256"),
        ],
    )
    def test_status_rejects_missing_or_malformed_aws_identity(
        self, fake_client, environment_name, bad_value, mismatch
    ):
        if bad_value is None:
            fake_client.env.pop(environment_name)
        else:
            fake_client.env[environment_name] = bad_value
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert mismatch in json.loads(resp["body"])["mismatches"]

    def test_status_requires_aws_code_digest_to_match_environment(self, fake_client):
        fake_client.code_sha256 = "B" * 43 + "="
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["aws.CodeSha256"]

    @pytest.mark.parametrize(
        ("field", "value", "mismatch"),
        [
            ("identity_status", "invalid", "runtime.identity_status"),
            ("function_version", "5", "runtime.function_version"),
            ("source_revision", "f" * 40, "runtime.source_revision"),
            ("config_version", "f" * 64, "runtime.config_version"),
            ("content_version", "f" * 64, "runtime.content_version"),
            ("snapshot_version", "f" * 64, "runtime.snapshot_version"),
            ("release_version", "f" * 64, "runtime.release_version"),
            ("corpus_version", "deadbeefcafe", "runtime.corpus_version"),
            ("artifact_code_sha256", "B" * 43 + "=", "runtime.artifact_code_sha256"),
        ],
    )
    def test_status_rejects_every_runtime_identity_mismatch(
        self, fake_client, field, value, mismatch
    ):
        fake_client.http.response = FakeHTTPResponse(_runtime_payload(**{field: value}))
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert mismatch in json.loads(resp["body"])["mismatches"]

    def test_status_rejects_runtime_non_json(self, fake_client):
        fake_client.http.response = FakeHTTPResponse(json_error=ValueError("HTML"))
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["runtime.json"]

    def test_status_rejects_runtime_non_200(self, fake_client):
        fake_client.http.response = FakeHTTPResponse(status_code=503)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["runtime.http_status"]

    def test_status_rejects_runtime_network_failure(self, fake_client):
        fake_client.http.error = TimeoutError("offline")
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["runtime.unreachable"]

    def test_status_requires_https_rider_base_url(self, monkeypatch, fake_client):
        monkeypatch.setenv("FPA_RIDER_BASE_URL", "http://fare.example")
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["runtime.base_url"]


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
        assert 'function:$RIDER_FN:*"' in live_statement
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


class TestPromotedEvidence:
    def test_missing_promoted_receipt_is_invalid_not_latest_run_fallback(
        self, fake_client, tmp_path
    ):
        fake_client.promoted.summary_path.unlink()
        ignored = tmp_path / "evals" / "runs" / "99991231T235959Z"
        ignored.mkdir(parents=True)
        (ignored / "summary.json").write_text(json.dumps({"run_at": "ignored"}), encoding="utf-8")

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 409
        data = json.loads(resp["body"])
        assert data["state"] == "invalid"
        assert data["status"] == "invalid"
        assert data["mismatches"] == ["summary.file"]

    @pytest.mark.parametrize(
        ("attribute", "expected"),
        [
            ("summary_path", "summary.sha256"),
            ("results_path", "results.sha256"),
            ("promotion_path", "promotion.canonical"),
        ],
    )
    def test_tampered_exact_evidence_is_invalid(self, fake_client, attribute, expected):
        path = getattr(fake_client.promoted, attribute)
        path.write_bytes(path.read_bytes() + b" ")

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["mismatches"] == [expected]

    @pytest.mark.parametrize(
        "field",
        [
            "source_revision",
            "config_version",
            "content_version",
            "snapshot_version",
            "release_version",
            "corpus_version",
            "artifact_code_sha256",
            "function_version",
        ],
    )
    def test_evidence_must_match_every_live_runtime_field(self, fake_client, field):
        live = _runtime_payload()
        live[field] = "different"
        available = {
            "content_version": live["content_version"],
            "corpus_version": live["corpus_version"],
        }

        with pytest.raises(console.PromotedEvalError) as caught:
            console.promoted_eval_status(live, available)

        assert caught.value.mismatches == (f"evaluation.runtime_release.{field}",)

    @pytest.mark.parametrize("field", ["content_version", "corpus_version"])
    def test_packaged_catalog_must_match_live_release(self, fake_client, field):
        available = {"content_version": CONTENT, "corpus_version": CORPUS}
        available[field] = "different"

        with pytest.raises(console.PromotedEvalError) as caught:
            console.promoted_eval_status(_runtime_payload(), available)

        assert caught.value.mismatches == (f"catalog.{field}",)

    def test_matching_evidence_returns_only_sanitized_projection(self, fake_client):
        resp = console.console_handler(_event(path="/console/api/eval-report"))

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["run_id"] == evidence_fixtures._RUN_ID
        assert data["runtime_release"]["release_version"] == RELEASE
        assert data["total"] == {"passed": 2, "total": 3, "pass_rate": 66.7}
        serialized = json.dumps(data)
        for forbidden in (
            "must never be returned",
            '"question":',
            '"rationale":',
            '"retrieved_passages":',
        ):
            assert forbidden not in serialized

    def test_matching_but_old_promoted_evidence_is_warning(self, fake_client, monkeypatch):
        monkeypatch.setattr(console, "PROMOTED_EVAL_MAX_AGE_SECONDS", 60)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 409
        data = json.loads(resp["body"])
        assert data["state"] == "warning"
        assert data["status"] == "stale"
        assert data["mismatches"] == ["evaluation.age"]
        assert data["evaluation"]["fresh"] is False

    def test_freshness_clock_and_budget_are_injectable(self, fake_client, monkeypatch):
        boundary_seconds = int(
            (evidence_fixtures._PROMOTED_AT - evidence_fixtures._RUN_AT).total_seconds()
        )
        monkeypatch.setattr(
            console,
            "_clock",
            lambda: evidence_fixtures._PROMOTED_AT,
        )
        monkeypatch.setattr(console, "PROMOTED_EVAL_MAX_AGE_SECONDS", boundary_seconds)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 200
        evaluation = json.loads(resp["body"])["evaluation"]
        assert evaluation["age_seconds"] == boundary_seconds
        assert evaluation["max_age_seconds"] == boundary_seconds

    def test_catalog_load_failure_is_503(self, fake_client, monkeypatch):
        def unavailable():
            raise OSError("not packaged")

        monkeypatch.setattr(console, "corpus_summary", unavailable)
        resp = console.console_handler(_event(path="/console/api/status"))
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["catalog.unavailable"]


class TestEvalReport:
    def test_no_promoted_receipt_returns_409(self, monkeypatch, tmp_path, fake_client):
        monkeypatch.setattr(console, "PROMOTED_EVAL_SUMMARY_PATH", tmp_path / "does-not-exist.json")
        resp = console.console_handler(_event(path="/console/api/eval-report"))
        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["mismatches"] == ["summary.file"]

    def test_fixed_trio_is_returned_not_lexically_latest_run(self, tmp_path, fake_client):
        ignored = tmp_path / "runs" / "99991231T235959Z"
        ignored.mkdir(parents=True)
        (ignored / "summary.json").write_text(json.dumps({"run_at": "ignored"}), encoding="utf-8")

        resp = console.console_handler(_event(path="/console/api/eval-report"))

        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["run_id"] == evidence_fixtures._RUN_ID

    def test_eval_report_and_status_share_the_exact_verifier(self, fake_client, monkeypatch):
        real_verify = console.verify_promotion_evidence
        calls = []

        def recorded(**kwargs):
            calls.append(kwargs)
            return real_verify(**kwargs)

        monkeypatch.setattr(console, "verify_promotion_evidence", recorded)
        assert console.console_handler(_event(path="/console/api/status"))["statusCode"] == 200
        assert console.console_handler(_event(path="/console/api/eval-report"))["statusCode"] == 200
        assert len(calls) == 2
        assert calls[0]["summary_path"] == calls[1]["summary_path"]
        assert calls[0]["results_path"] == calls[1]["results_path"]
        assert calls[0]["promotion_path"] == calls[1]["promotion_path"]


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


class TestDependencyWiring:
    def test_clock_returns_utc_time(self):
        observed = console._clock()

        assert observed.tzinfo is UTC

    def test_default_ssm_client_uses_configured_region(self, monkeypatch):
        calls = []

        class FakeSSM:
            def get_parameter(self, **kwargs):
                assert kwargs == {
                    "Name": "/fare-policy-assistant/token",
                    "WithDecryption": True,
                }
                return {"Parameter": {"Value": "parameter-token"}}

        module = type("FakeBoto3Module", (), {})()

        def client(service, *, region_name):
            calls.append((service, region_name))
            return FakeSSM()

        module.client = client
        monkeypatch.setitem(console.sys.modules, "boto3", module)
        monkeypatch.setattr(console, "_ssm_client_factory", None)
        monkeypatch.setenv("AWS_REGION", "us-test-1")
        monkeypatch.setenv(
            "FPA_CONSOLE_TOKEN_PARAMETER_NAME",
            "/fare-policy-assistant/token",
        )
        console._reset_console_token_for_tests()

        assert console._console_token() == "parameter-token"
        assert calls == [("ssm", "us-test-1")]

    def test_default_lambda_client_uses_configured_region(self, monkeypatch):
        sentinel = object()
        calls = []
        module = type("FakeBoto3Module", (), {})()

        def client(service, *, region_name):
            calls.append((service, region_name))
            return sentinel

        module.client = client
        monkeypatch.setitem(console.sys.modules, "boto3", module)
        monkeypatch.setattr(console, "_client_factory", None)
        monkeypatch.setenv("AWS_REGION", "us-test-1")

        assert console._lambda_client() is sentinel
        assert calls == [("lambda", "us-test-1")]

    def test_default_http_client_is_fail_closed_and_does_not_follow_redirects(self, monkeypatch):
        sentinel = object()
        calls = []
        module = type("FakeHttpxModule", (), {})()

        def client(**kwargs):
            calls.append(kwargs)
            return sentinel

        module.Client = client
        monkeypatch.setitem(console.sys.modules, "httpx", module)
        monkeypatch.setattr(console, "_http_client_factory", None)

        assert console._http_client() is sentinel
        assert calls == [
            {
                "timeout": 5.0,
                "follow_redirects": False,
                "trust_env": False,
            }
        ]


class TestLiveObservationFailures:
    def test_status_requires_a_rider_function_name(self, monkeypatch, fake_client):
        monkeypatch.delenv("FPA_RIDER_FUNCTION_NAME")

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["aws.function_name"]

    def test_status_requires_a_nonempty_rider_base_url(self, monkeypatch, fake_client):
        monkeypatch.setenv("FPA_RIDER_BASE_URL", "   ")

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["runtime.base_url"]

    @pytest.mark.parametrize(
        ("alias", "mismatch"),
        [
            (None, "alias.initial"),
            (
                {"FunctionVersion": "11", "RoutingConfig": []},
                "alias.routing_weights",
            ),
            (
                {"FunctionVersion": "11", "RevisionId": ""},
                "alias.initial",
            ),
        ],
    )
    def test_status_rejects_malformed_alias_observations(self, fake_client, alias, mismatch):
        fake_client.aliases = [alias]

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == [mismatch]

    @pytest.mark.parametrize(
        ("current", "mismatch"),
        [
            (None, "aws.configuration"),
            (
                {
                    "Version": "11",
                    "Environment": [],
                },
                "aws.environment",
            ),
            (
                {
                    "Version": "11",
                    "Environment": {"Variables": []},
                },
                "aws.environment",
            ),
        ],
    )
    def test_status_rejects_malformed_function_configuration(
        self, monkeypatch, fake_client, current, mismatch
    ):
        monkeypatch.setattr(
            fake_client,
            "get_function_configuration",
            lambda **kwargs: current,
        )

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == [mismatch]

    def test_status_rejects_whitespace_padded_artifact_digest(self, fake_client):
        fake_client.env["FPA_ARTIFACT_CODE_SHA256"] = f" {ARTIFACT}"

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["aws.artifact_code_sha256"]

    def test_status_rejects_a_different_valid_aws_code_digest(self, fake_client):
        fake_client.code_sha256 = console.base64.b64encode(b"x" * 32).decode("ascii")

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["aws.CodeSha256"]

    def test_status_rejects_non_object_runtime_json(self, fake_client):
        fake_client.http.response = FakeHTTPResponse([])

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["runtime.json"]

    def test_runtime_client_without_close_method_is_supported(self, monkeypatch, fake_client):
        class NoCloseHTTPClient:
            def get(self, url):
                assert url == "https://fare.example/version"
                return FakeHTTPResponse()

        monkeypatch.setattr(
            console,
            "_http_client_factory",
            NoCloseHTTPClient,
        )

        assert console.console_handler(_event(path="/console/api/status"))["statusCode"] == 200

    def test_status_maps_lambda_client_creation_failure(self, monkeypatch, fake_client):
        def unavailable():
            raise RuntimeError("credential chain unavailable")

        monkeypatch.setattr(console, "_client_factory", unavailable)

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["aws.client"]

    def test_status_maps_initial_alias_read_failure(self, monkeypatch, fake_client):
        def unavailable(**kwargs):
            raise RuntimeError("AWS unavailable")

        monkeypatch.setattr(fake_client, "get_alias", unavailable)

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["alias.initial"]

    def test_status_maps_function_configuration_read_failure(self, monkeypatch, fake_client):
        def unavailable(**kwargs):
            raise RuntimeError("AWS unavailable")

        monkeypatch.setattr(
            fake_client,
            "get_function_configuration",
            unavailable,
        )

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["aws.configuration"]

    def test_status_maps_final_alias_read_failure(self, monkeypatch, fake_client):
        calls = 0

        def alias_then_failure(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("AWS unavailable")
            return {"FunctionVersion": "11", "RevisionId": "same"}

        monkeypatch.setattr(fake_client, "get_alias", alias_then_failure)

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == ["alias.final"]

    @pytest.mark.parametrize(
        ("environment_name", "value", "mismatch"),
        [
            ("FPA_DISABLED_DOC_IDS", [], "aws.disabled_documents"),
            ("FPA_EMBED_ANCESTORS", "", "aws.embed_ancestors"),
        ],
    )
    def test_status_rejects_unsafe_display_configuration(
        self, fake_client, environment_name, value, mismatch
    ):
        fake_client.env[environment_name] = value

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["mismatches"] == [mismatch]

    def test_status_returns_only_safe_mismatch_metadata_for_a_coherent_newer_runtime(
        self, monkeypatch, fake_client
    ):
        newer_content = "f" * 64
        fake_client.env["FPA_PINNED_CONTENT_VERSION"] = newer_content
        fake_client.http.response = FakeHTTPResponse(
            _runtime_payload(content_version=newer_content)
        )

        resp = console.console_handler(_event(path="/console/api/status"))

        assert resp["statusCode"] == 409
        data = json.loads(resp["body"])
        assert data["mismatches"] == [
            "evaluation.runtime_release.content_version",
            "catalog.content_version",
        ]
        serialized = json.dumps(data)
        assert '"question":' not in serialized
        assert '"answer":' not in serialized
        assert '"rationale":' not in serialized


class TestRequestAndResponseBranches:
    def test_read_body_decodes_base64_json(self):
        payload = {"corpus_version": "deadbeefcafe"}
        encoded = console.base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

        assert (
            console._read_body(
                {
                    "body": encoded,
                    "isBase64Encoded": True,
                }
            )
            == payload
        )

    def test_read_body_rejects_oversized_payload(self):
        event = {"body": "x" * (console.MAX_BODY_BYTES + 1)}

        with pytest.raises(ValueError, match="request too large"):
            console._read_body(event)

    def test_read_body_accepts_an_empty_payload(self):
        assert console._read_body({"body": "  "}) == {}

    def test_versions_maps_history_runtime_failure(self, monkeypatch):
        def unavailable():
            raise RuntimeError("git unavailable")

        monkeypatch.setattr(console, "list_versions", unavailable)

        resp = console.console_handler(_event(path="/console/api/versions"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"]) == {"error": "Version history unavailable: git unavailable"}

    def test_diff_maps_history_runtime_failure(self, monkeypatch):
        def unavailable(from_ref, to_ref):
            raise RuntimeError("git unavailable")

        monkeypatch.setattr(console, "version_diff", unavailable)

        resp = console.console_handler(
            _event(
                path="/console/api/diff",
                qs={"from": "aaaaaaaaaaaa", "to": "bbbbbbbbbbbb"},
            )
        )

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"]) == {"error": "Version history unavailable: git unavailable"}

    def test_diff_unknown_source_version_is_404(self, monkeypatch):
        monkeypatch.setattr(
            console,
            "_load_version_history",
            lambda: [{"commit": "known", "corpus_version": "bbbbbbbbbbbb"}],
        )

        resp = console.console_handler(
            _event(
                path="/console/api/diff",
                qs={"from": "unknown", "to": "bbbbbbbbbbbb"},
            )
        )

        assert resp["statusCode"] == 404
        assert json.loads(resp["body"]) == {"error": "unknown version: unknown"}

    def test_eval_report_maps_live_identity_failure(self, monkeypatch, fake_client):
        monkeypatch.delenv("FPA_RIDER_FUNCTION_NAME")

        resp = console.console_handler(_event(path="/console/api/eval-report"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"]) == {
            "state": "invalid",
            "status": "invalid",
            "error": "Live rider identity observations are incoherent.",
            "mismatches": ["aws.function_name"],
        }

    def test_eval_report_maps_catalog_failure(self, monkeypatch, fake_client):
        def unavailable():
            raise OSError("catalog is not packaged")

        monkeypatch.setattr(console, "corpus_summary", unavailable)

        resp = console.console_handler(_event(path="/console/api/eval-report"))

        assert resp["statusCode"] == 503
        assert json.loads(resp["body"]) == {
            "state": "invalid",
            "status": "invalid",
            "error": "The console catalog bundle is unavailable.",
            "mismatches": ["catalog.unavailable"],
        }

    def test_eval_report_rejects_stale_evidence_without_returning_results(
        self, monkeypatch, fake_client
    ):
        monkeypatch.setattr(console, "PROMOTED_EVAL_MAX_AGE_SECONDS", 60)

        resp = console.console_handler(_event(path="/console/api/eval-report"))

        assert resp["statusCode"] == 409
        data = json.loads(resp["body"])
        assert data == {
            "state": "warning",
            "status": "stale",
            "error": (
                "The promoted evaluation matches live, but is older than its freshness budget."
            ),
            "mismatches": ["evaluation.age"],
        }
        serialized = json.dumps(data)
        assert '"question":' not in serialized
        assert '"answer":' not in serialized
