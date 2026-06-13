"""The Lambda handler: routing, validation, and the privacy/budget guards.

These tests run the real handler against the committed corpus with the mock
model (FPA_PROVIDER=mock), so they exercise the full pipeline offline.
"""

from __future__ import annotations

import json

import pytest

from web import handler as web_handler


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("FPA_PROVIDER", "mock")
    web_handler._RECENT.clear()


def _event(method: str = "POST", path: str = "/api/ask", body: dict | None = None) -> dict:
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "body": json.dumps(body) if body is not None else None,
    }


def _post(question: str) -> dict:
    return web_handler.handler(_event(body={"question": question}))


class TestRouting:
    def test_index_served_at_root(self):
        resp = web_handler.handler(_event(method="GET", path="/"))
        assert resp["statusCode"] == 200
        assert "text/html" in resp["headers"]["content-type"]
        assert "Reference implementation" in resp["body"]

    def test_unknown_path_404(self):
        resp = web_handler.handler(_event(method="GET", path="/admin"))
        assert resp["statusCode"] == 404

    def test_get_on_api_405(self):
        resp = web_handler.handler(_event(method="GET", path="/api/ask"))
        assert resp["statusCode"] == 405

    def test_security_headers_present(self):
        resp = web_handler.handler(_event(method="GET", path="/"))
        assert resp["headers"]["x-frame-options"] == "DENY"
        assert "content-security-policy" in resp["headers"]


class TestValidation:
    def test_missing_body_400(self):
        resp = web_handler.handler(_event(body=None))
        assert resp["statusCode"] == 400

    def test_malformed_json_400(self):
        event = _event()
        event["body"] = "not json"
        resp = web_handler.handler(event)
        assert resp["statusCode"] == 400

    def test_non_string_question_400(self):
        resp = web_handler.handler(_event(body={"question": 42}))
        assert resp["statusCode"] == 400

    def test_over_length_question_400(self):
        resp = _post("x" * (web_handler.MAX_QUESTION_CHARS + 1))
        assert resp["statusCode"] == 400


class TestAnswers:
    def test_answer_carries_citations_and_as_of(self):
        resp = _post("Do youth ride free on Yolobus?")
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["kind"] == "answered"
        assert data["as_of_date"]
        assert data["citations"], "an answered response must cite sources"
        assert {"agency", "title", "url", "fetch_date"} <= set(data["citations"][0])

    def test_pii_question_refused_and_never_echoed(self):
        resp = _post("My SSN is 123-45-6789, do I get the senior pass?")
        data = json.loads(resp["body"])
        assert data["kind"] == "refused_input"
        assert "123-45-6789" not in data["answer"]

    def test_spanish_refusal_in_spanish(self):
        resp = _post("Mi número de seguro social es 987-65-4321, ¿califico para el descuento?")
        data = json.loads(resp["body"])
        assert data["kind"] == "refused_input"
        assert data["language"] == "es"


class TestBudget:
    def test_request_budget_returns_429(self):
        for _ in range(web_handler.REQUESTS_PER_MINUTE):
            assert _post("Do youth ride free on Yolobus?")["statusCode"] == 200
        resp = _post("Do youth ride free on Yolobus?")
        assert resp["statusCode"] == 429

    def test_budget_does_not_count_page_loads(self):
        for _ in range(web_handler.REQUESTS_PER_MINUTE * 2):
            web_handler.handler(_event(method="GET", path="/"))
        assert _post("Do youth ride free on Yolobus?")["statusCode"] == 200
