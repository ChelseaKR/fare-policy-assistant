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
    web_handler._ANSWER_CACHE.clear()


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
        # Distinct questions so each is a cache miss that counts against budget.
        for i in range(web_handler.REQUESTS_PER_MINUTE):
            assert _post(f"What is fare number {i} on MST?")["statusCode"] == 200
        resp = _post("One more distinct fare question on MST?")
        assert resp["statusCode"] == 429

    def test_budget_does_not_count_page_loads(self):
        for _ in range(web_handler.REQUESTS_PER_MINUTE * 2):
            web_handler.handler(_event(method="GET", path="/"))
        assert _post("Do youth ride free on Yolobus?")["statusCode"] == 200


class TestCache:
    def test_repeated_question_is_cached_and_bypasses_budget(self):
        first = _post("Do youth ride free on Yolobus?")
        assert first["statusCode"] == 200
        # Exhaust the budget with distinct questions ...
        for i in range(web_handler.REQUESTS_PER_MINUTE):
            _post(f"distinct budget filler {i}?")
        # ... a brand-new question is now throttled ...
        assert _post("a fresh uncached question?")["statusCode"] == 429
        # ... but the already-cached one still answers, free.
        again = _post("Do youth ride free on Yolobus?")
        assert again["statusCode"] == 200
        assert again["body"] == first["body"]

    def test_cache_is_case_insensitive(self):
        a = _post("How much is the senior fare on SBMTD?")
        b = _post("how much is the SENIOR fare on sbmtd?")
        assert a["body"] == b["body"]
        assert len(web_handler._ANSWER_CACHE) == 1

    def test_cache_evicts_past_bound(self, monkeypatch):
        monkeypatch.setattr(web_handler, "ANSWER_CACHE_SIZE", 3)
        for i in range(5):
            _post(f"unique question {i}?")
        assert len(web_handler._ANSWER_CACHE) <= 3


class TestMultiTurn:
    def test_history_parsed_and_capped(self):
        raw = [{"q": f"q{i}", "a": f"a{i}"} for i in range(5)]
        out = web_handler._parse_history(raw)
        assert len(out) == web_handler.MAX_HISTORY_TURNS
        assert out[-1] == ("q4", "a4")

    def test_history_ignores_malformed(self):
        assert web_handler._parse_history("nope") == []
        assert web_handler._parse_history([{"q": "only q"}, {"q": 1, "a": 2}]) == []

    def test_history_distinguishes_cache_entries(self):
        web_handler.handler(_event(body={"question": "What is the fare?", "history": []}))
        web_handler.handler(_event(body={
            "question": "What is the fare?",
            "history": [{"q": "on MST?", "a": "yes"}],
        }))
        # Same question, different history → two distinct cache entries.
        assert len(web_handler._ANSWER_CACHE) == 2


class TestFeedback:
    def _fb(self, body):
        return web_handler.handler({
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/api/feedback",
            "body": json.dumps(body) if body is not None else None,
        })

    def test_valid_feedback_accepted(self):
        resp = self._fb({"verdict": "up", "kind": "answered", "language": "en"})
        assert resp["statusCode"] == 200

    def test_invalid_verdict_rejected(self):
        assert self._fb({"verdict": "maybe"})["statusCode"] == 400
        assert self._fb({})["statusCode"] == 400

    def test_feedback_logs_no_content(self, capsys):
        # Even if a client sends question/answer text, the handler must not log it.
        self._fb({"verdict": "down", "kind": "answered", "language": "es",
                  "question": "SECRET-Q", "answer": "SECRET-A"})
        out = capsys.readouterr().out
        assert "SECRET-Q" not in out and "SECRET-A" not in out
        assert '"feedback": "down"' in out

    def test_feedback_get_405(self):
        resp = web_handler.handler({
            "requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/feedback", "body": None,
        })
        assert resp["statusCode"] == 405
