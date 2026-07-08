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

    def test_live_region_present_for_answer_status(self):
        # New answers and status are announced through a polite live region
        # (persona research F-8); lock it so a refactor cannot drop it.
        body = web_handler.handler(_event(method="GET", path="/"))["body"]
        assert 'role="status"' in body
        assert 'aria-live="polite"' in body

    def test_display_settings_controls_present(self):
        # Text-size and high-contrast controls for low-vision and older riders
        # (persona research F-2). They are labeled and toggle via aria-pressed.
        body = web_handler.handler(_event(method="GET", path="/"))["body"]
        assert 'aria-label="Display settings"' in body
        for control_id in ("tsize-normal", "tsize-large", "tsize-xlarge", "contrast"):
            assert f'id="{control_id}"' in body
        assert "aria-pressed" in body


class TestOfflineReference:
    def test_offline_page_served(self):
        resp = web_handler.handler(_event(method="GET", path="/offline"))
        assert resp["statusCode"] == 200
        assert "text/html" in resp["headers"]["content-type"]
        body = resp["body"]
        # Built from the committed corpus: every agency and the as-of framing.
        for agency_full in ("Monterey-Salinas Transit", "Humboldt Transit Authority"):
            assert agency_full in body
        assert "published as of" in body
        assert "Reference implementation" in body
        # Citable sources are resolvable links, not internal doc ids.
        assert "https://" in body and "[doc:" not in body

    def test_offline_page_passes_structural_a11y(self):
        from web.a11y import check_html

        body = web_handler.handler(_event(method="GET", path="/offline"))["body"]
        assert check_html(body) == []


class TestVersion:
    def _version(self):
        return web_handler.handler(_event(method="GET", path="/version"))

    def test_version_reports_corpus_identity(self):
        resp = self._version()
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["corpus_version"]) == 12
        assert data["as_of"]
        assert set(data["agencies"]) >= {"MST", "Yolobus", "HTA"}
        assert data["documents"] >= 5

    def test_version_reports_pin_match(self, monkeypatch):
        actual = json.loads(self._version()["body"])["corpus_version"]
        monkeypatch.setenv("FPA_PINNED_CORPUS_VERSION", actual)
        data = json.loads(self._version()["body"])
        assert data["pinned"] == actual
        assert data["matches_pin"] is True

    def test_version_flags_pin_mismatch(self, monkeypatch, capsys):
        monkeypatch.setenv("FPA_PINNED_CORPUS_VERSION", "deadbeefcafe")
        data = json.loads(self._version()["body"])
        assert data["matches_pin"] is False
        assert "corpus_version_mismatch" in capsys.readouterr().out


class TestEmbedWidget:
    def _embed(self):
        return web_handler.handler(_event(method="GET", path="/embed"))

    def test_embed_served(self):
        resp = self._embed()
        assert resp["statusCode"] == 200
        assert "text/html" in resp["headers"]["content-type"]
        body = resp["body"]
        assert "embedded widget" in body
        # The limits travel with the embed.
        assert "does not decide your eligibility" in body
        assert "Reference implementation" in body

    def test_embed_is_frameable_main_page_is_not(self):
        embed = self._embed()["headers"]
        # The embed route drops the DENY and names ancestors in CSP instead.
        assert "x-frame-options" not in {k.lower() for k in embed}
        assert "frame-ancestors" in embed["content-security-policy"]
        # The main page is still not frameable: embedding did not loosen it.
        main = web_handler.handler(_event(method="GET", path="/"))["headers"]
        assert main["x-frame-options"] == "DENY"
        assert "frame-ancestors" not in main["content-security-policy"]

    def test_embed_defaults_to_same_origin_framing(self, monkeypatch):
        monkeypatch.delenv("FPA_EMBED_ANCESTORS", raising=False)
        csp = self._embed()["headers"]["content-security-policy"]
        assert "frame-ancestors 'self'" in csp

    def test_embed_ancestor_allowlist_is_configurable(self, monkeypatch):
        monkeypatch.setenv("FPA_EMBED_ANCESTORS", "https://sbmtd.gov https://mst.org")
        csp = self._embed()["headers"]["content-security-policy"]
        assert "frame-ancestors https://sbmtd.gov https://mst.org" in csp

    def test_embed_passes_structural_a11y(self):
        from web.a11y import check_html

        assert check_html(self._embed()["body"]) == []


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

    def test_oversized_body_rejected_before_parse_413(self):
        event = _event()
        event["body"] = "{" + "x" * (web_handler.MAX_BODY_BYTES + 1)
        resp = web_handler.handler(event)
        assert resp["statusCode"] == 413


class TestAnswers:
    def test_answer_carries_citations_and_as_of(self):
        resp = _post("Do youth ride free on Yolobus?")
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["kind"] == "answered"
        assert data["as_of_date"]
        assert data["citations"], "an answered response must cite sources"
        assert {"agency", "title", "url", "fetch_date"} <= set(data["citations"][0])
        # Graded confidence signal for integrators/staff (persona research F-16).
        assert data["confidence"] in {"medium", "high"}
        # The answer is tied to a corpus version (persona research R2-6).
        assert len(data["corpus_version"]) == 12

    def test_answered_response_carries_valid_structured_contract(self):
        # EXP-04: the typed payload rides alongside `answer`, validated
        # against docs/answer-contract.schema.json before it is ever sent.
        from assistant.contract import validate_answer_contract

        resp = _post("Do youth ride free on Yolobus?")
        data = json.loads(resp["body"])
        assert data["structured"] is not None, "the mock model's answer should parse cleanly"
        assert validate_answer_contract(data["structured"]) == []
        assert data["structured"]["kind"] == "answered"
        assert data["structured"]["citations"]

    def test_refusal_response_structured_is_null_or_valid(self):
        from assistant.contract import validate_answer_contract

        resp = _post("My SSN is 123-45-6789, do I get the senior pass?")
        data = json.loads(resp["body"])
        if data["structured"] is not None:
            assert validate_answer_contract(data["structured"]) == []

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
        web_handler.handler(
            _event(
                body={
                    "question": "What is the fare?",
                    "history": [{"q": "on MST?", "a": "yes"}],
                }
            )
        )
        # Same question, different history → two distinct cache entries.
        assert len(web_handler._ANSWER_CACHE) == 2


class TestFeedback:
    def _fb(self, body):
        return web_handler.handler(
            {
                "requestContext": {"http": {"method": "POST"}},
                "rawPath": "/api/feedback",
                "body": json.dumps(body) if body is not None else None,
            }
        )

    def test_valid_feedback_accepted(self):
        resp = self._fb({"verdict": "up", "kind": "answered", "language": "en"})
        assert resp["statusCode"] == 200

    def test_invalid_verdict_rejected(self):
        assert self._fb({"verdict": "maybe"})["statusCode"] == 400
        assert self._fb({})["statusCode"] == 400

    def test_feedback_logs_no_content(self, capsys):
        # Even if a client sends question/answer text, the handler must not log it.
        self._fb(
            {
                "verdict": "down",
                "kind": "answered",
                "language": "es",
                "question": "SECRET-Q",
                "answer": "SECRET-A",
            }
        )
        out = capsys.readouterr().out
        assert "SECRET-Q" not in out and "SECRET-A" not in out
        assert '"feedback": "down"' in out

    def test_feedback_get_405(self):
        resp = web_handler.handler(
            {
                "requestContext": {"http": {"method": "GET"}},
                "rawPath": "/api/feedback",
                "body": None,
            }
        )
        assert resp["statusCode"] == 405
