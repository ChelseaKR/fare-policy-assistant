"""The Lambda handler: routing, validation, and the privacy/budget guards.

These tests run the real handler against the committed corpus with the mock
model (FPA_PROVIDER=mock), so they exercise the full pipeline offline.
"""

from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace

import pytest

from assistant.answer import AnswerResult, Citation
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


def _log_records(caplog, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


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

    def test_no_route_allows_unsafe_inline(self):
        # The CSP hashes inline blocks instead of blanket-allowing them; no
        # response may fall back to 'unsafe-inline' (FIX-10).
        for path in (
            "/",
            "/offline",
            "/guide",
            "/embed",
            "/version",
            "/api/ask",
            "/api/feedback",
        ):
            resp = web_handler.handler(_event(method="GET", path=path))
            csp = resp["headers"].get("content-security-policy", "")
            assert "unsafe-inline" not in csp, f"{path} CSP allows unsafe-inline: {csp}"

    @pytest.mark.parametrize("path", ["/", "/offline", "/guide", "/embed"])
    def test_inline_block_hashes_appear_in_csp(self, path):
        # Drift guard: recompute the sha256 of every inline <script>/<style>
        # block from the *served* body and assert each token is in the CSP. If
        # markup and policy ever drift apart, the browser would refuse the block
        # and this fails first.
        from web.csp import script_hashes, style_hashes

        resp = web_handler.handler(_event(method="GET", path=path))
        body = resp["body"]
        csp = resp["headers"]["content-security-policy"]
        tokens = script_hashes(body) + style_hashes(body)
        assert tokens, f"{path} served no inline blocks to hash"
        for token in tokens:
            assert token in csp, f"{path} CSP is missing {token}"

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


class TestGuidedFareFinder:
    def test_guide_page_served(self):
        resp = web_handler.handler(_event(method="GET", path="/guide"))
        assert resp["statusCode"] == 200
        assert "text/html" in resp["headers"]["content-type"]
        body = resp["body"]
        for agency_full in ("Monterey-Salinas Transit", "Humboldt Transit Authority"):
            assert agency_full in body
        assert "published as of" in body
        assert "Reference implementation" in body
        assert "https://" in body and "[doc:" not in body

    def test_guide_page_passes_structural_a11y(self):
        from web.a11y import check_html

        body = web_handler.handler(_event(method="GET", path="/guide"))["body"]
        assert check_html(body) == []

    def test_guide_page_has_no_input_fields(self):
        # EXP-07's excellence bar: zero input fields, even though a form-like
        # walkthrough invites collecting rider attributes.
        body = web_handler.handler(_event(method="GET", path="/guide"))["body"]
        for tag in ("<input", "<textarea", "<select"):
            assert tag not in body

    def test_guide_page_never_claims_to_decide_eligibility(self):
        body = web_handler.handler(_event(method="GET", path="/guide"))["body"]
        assert "does not decide whether you qualify" in body

    def test_guide_page_reachable_from_index(self):
        body = web_handler.handler(_event(method="GET", path="/"))["body"]
        assert 'href="/guide"' in body


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

    def test_version_flags_pin_mismatch(self, monkeypatch, caplog):
        monkeypatch.setenv("FPA_PINNED_CORPUS_VERSION", "deadbeefcafe")
        with caplog.at_level(logging.WARNING, logger="fare_assistant"):
            data = json.loads(self._version()["body"])
        assert data["matches_pin"] is False
        record = _log_records(caplog, "corpus_version_mismatch")[-1]
        assert record.serving_corpus_version == data["corpus_version"]
        assert record.pinned_corpus_version == "deadbeefcafe"

    def test_version_lists_known_retained_versions(self):
        # EXP-05: the currently served corpus_version is itself a retained
        # version once `make ingest` has archived it.
        data = json.loads(self._version()["body"])
        assert data["corpus_version"] in data["known_versions"]

    def test_version_discloses_operator_disabled_documents(self, monkeypatch):
        monkeypatch.setenv("FPA_DISABLED_DOC_IDS", "yolobus-fares, sbmtd-farechange")
        data = json.loads(self._version()["body"])
        assert data["disabled_documents"] == [
            "sbmtd-farechange",
            "yolobus-fares",
        ]

    def test_disabled_documents_are_removed_from_offline_and_guide(self, monkeypatch):
        marker = "All fares are effective July 1, 2025"
        monkeypatch.delenv("FPA_DISABLED_DOC_IDS", raising=False)
        web_handler._OFFLINE_HTML = None
        web_handler._GUIDE_HTML = None
        for path in ("/offline", "/guide"):
            warm_body = web_handler.handler(_event(method="GET", path=path))["body"]
            assert marker in warm_body

        # Changing containment policy in a warm process must invalidate both
        # rendered-page caches and remove the disabled material immediately.
        monkeypatch.setenv("FPA_DISABLED_DOC_IDS", "yolobus-fares")
        for path in ("/offline", "/guide"):
            contained_body = web_handler.handler(_event(method="GET", path=path))["body"]
            assert marker not in contained_body
            assert (
                f"Corpus version (full) {web_handler._corpus_summary()['corpus_version']}"
                in contained_body
            )
            assert "active page-view version" in contained_body


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
        # RR2: the liability/staleness frame rides above the fold, in both
        # languages, not only in the footer.
        assert "can be out of date" in body
        assert "final eligibility decision" in body
        assert "decisión final de elegibilidad" in body

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

    def test_embed_csp_ends_with_frame_ancestors(self, monkeypatch):
        # Hashing the inline blocks must not disturb the frame-ancestors tail
        # the embed appends (FIX-10 keeps the framing contract intact).
        monkeypatch.setenv("FPA_EMBED_ANCESTORS", "https://sbmtd.gov")
        csp = self._embed()["headers"]["content-security-policy"]
        assert csp.rstrip().endswith("frame-ancestors https://sbmtd.gov")
        assert "unsafe-inline" not in csp

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

    def test_uncertain_taglish_reports_top_language_and_uncertainty(self, monkeypatch):
        def taglish_answer(question, **kwargs):
            return AnswerResult(
                question=question,
                answer=(
                    "Batay sa mga patakaran na inilathala noong 2026-06-12, ang Regular "
                    "Fixed Route Single Ride fare ay $2.00 [doc:mst-fares]."
                ),
                kind="answered",
                as_of_date="2026-06-12",
                citations=[
                    Citation(
                        doc_id="mst-fares",
                        agency="MST",
                        title="Fares",
                        url="https://mst.org/fares/",
                        fetch_date="2026-06-12",
                    )
                ],
            )

        monkeypatch.setattr(web_handler, "answer_question", taglish_answer)
        data = json.loads(_post("Magkano ang pamasahe sa MST?")["body"])
        assert data["language"] == "tl"
        assert data["language_uncertain"] is True
        assert 0 < data["language_confidence"] < 1


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


class TestStructuredObservability:
    def test_model_and_answer_events_share_runtime_owned_correlation(self, monkeypatch, caplog):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_VERSION", "23")
        event = _event(body={"question": "What is the MST senior fare?"})
        event["requestContext"]["requestId"] = "UNTRUSTED-EVENT-ID"
        event["requestContext"]["http"]["sourceIp"] = "192.0.2.44"
        event["headers"] = {
            "user-agent": "SECRET-USER-AGENT",
            "x-request-id": "UNTRUSTED-HEADER-ID",
        }
        context = SimpleNamespace(aws_request_id="AWS-RUNTIME-REQUEST-ID")

        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = web_handler.handler(event, context)

        assert response["statusCode"] == 200
        records = _log_records(caplog, "genai_call") + _log_records(caplog, "answer_request")
        assert {record.runtime_request_id for record in records} == {"AWS-RUNTIME-REQUEST-ID"}
        assert {record.function_version for record in records} == {"23"}
        serialized = repr([vars(record) for record in records])
        for forbidden in (
            "What is the MST senior fare?",
            "UNTRUSTED-EVENT-ID",
            "192.0.2.44",
            "SECRET-USER-AGENT",
            "UNTRUSTED-HEADER-ID",
        ):
            assert forbidden not in serialized

    def test_cache_hit_has_terminal_duration(self, caplog):
        question = "Do youth ride free on Yolobus?"
        _post(question)

        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = _post(question)

        assert response["statusCode"] == 200
        record = _log_records(caplog, "answer_request")[-1]
        assert record.levelno == logging.INFO
        assert record.cache == "hit"
        assert record.model_called is False
        assert record.completion_recorded is False
        assert record.request_duration_ms >= 0
        assert record.duration_ms == record.request_duration_ms

    def test_rate_limit_has_terminal_observation(self, caplog):
        now = time.monotonic()
        web_handler._RECENT.extend([now] * web_handler.REQUESTS_PER_MINUTE)

        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = _post("A distinct request that should be rate limited?")

        assert response["statusCode"] == 429
        record = _log_records(caplog, "answer_request")[-1]
        assert record.levelno == logging.INFO
        assert record.kind == "rate_limited"
        assert record.status_code == 429
        assert record.cache == "miss"
        assert record.model_called is False
        assert record.completion_recorded is False
        assert record.request_duration_ms >= 0
        assert record.duration_ms == record.request_duration_ms

    def test_direct_health_marker_forces_paid_path_but_body_and_header_do_not(self, caplog):
        question = "What is the MST senior fare?"
        first = _post(question)
        assert first["statusCode"] == 200
        assert len(web_handler._ANSWER_CACHE) == 1

        disguised = _event(
            body={
                "question": question,
                web_handler._DIRECT_HEALTH_FIELD: web_handler._DIRECT_HEALTH_VALUE,
            }
        )
        disguised["headers"] = {web_handler._DIRECT_HEALTH_FIELD: web_handler._DIRECT_HEALTH_VALUE}
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            disguised_response = web_handler.handler(disguised)
        assert disguised_response["statusCode"] == 200
        assert not _log_records(caplog, "genai_call")
        disguised_record = _log_records(caplog, "answer_request")[-1]
        assert disguised_record.cache == "hit"
        assert disguised_record.direct_health is False

        caplog.clear()
        now = time.monotonic()
        web_handler._RECENT.extend([now] * web_handler.REQUESTS_PER_MINUTE)
        direct = _event(body={"question": question})
        direct[web_handler._DIRECT_HEALTH_FIELD] = web_handler._DIRECT_HEALTH_VALUE
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            direct_response = web_handler.handler(
                direct,
                SimpleNamespace(aws_request_id="DIRECT-HEALTH-REQUEST-ID"),
            )

        assert direct_response["statusCode"] == 200
        assert _log_records(caplog, "genai_call")
        direct_record = _log_records(caplog, "answer_request")[-1]
        assert direct_record.cache == "bypass"
        assert direct_record.direct_health is True
        assert direct_record.model_called is True
        model_record = _log_records(caplog, "genai_call")[-1]
        assert direct_record.runtime_request_id == model_record.runtime_request_id
        assert direct_record.runtime_request_id == "DIRECT-HEALTH-REQUEST-ID"
        assert len(web_handler._ANSWER_CACHE) == 1

    def test_handler_error_is_class_only_without_exception_or_request_content(
        self, monkeypatch, caplog
    ):
        def fail(_event):
            raise RuntimeError("SECRET-EXCEPTION-MESSAGE")

        monkeypatch.setattr(web_handler, "_ask", fail)
        event = _event(body={"question": "SECRET-QUESTION"})
        context = SimpleNamespace(aws_request_id="AWS-RUNTIME-ERROR-ID")
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = web_handler.handler(event, context)

        assert response["statusCode"] == 500
        record = _log_records(caplog, "handler_error")[-1]
        assert record.levelno == logging.ERROR
        assert record.error_type == "RuntimeError"
        assert record.route == "api_ask"
        assert record.runtime_request_id == "AWS-RUNTIME-ERROR-ID"
        assert record.exc_info is None
        serialized = repr(vars(record))
        assert "SECRET-EXCEPTION-MESSAGE" not in serialized
        assert "SECRET-QUESTION" not in serialized


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

    def test_source_containment_change_cannot_reuse_warm_cached_answer(self, monkeypatch):
        question = "How much is the local fare on Yolobus?"
        first = json.loads(_post(question)["body"])
        assert first["kind"] == "answered"

        key_before = web_handler._cache_key(question, [])
        monkeypatch.setenv("FPA_DISABLED_DOC_IDS", "yolobus-fares")
        key_after = web_handler._cache_key(question, [])
        assert key_after != key_before

        contained = json.loads(_post(question)["body"])
        assert contained["kind"] == "refused_no_support"
        assert contained != first

    def test_cache_keys_are_process_local_hmac_digests_not_plaintext(self):
        question = "How much is the unique-marker-7QX senior fare on SBMTD?"
        _post(question)

        assert len(web_handler._ANSWER_CACHE) == 1
        key = next(iter(web_handler._ANSWER_CACHE))
        assert len(key) == 64
        assert set(key) <= set("0123456789abcdef")
        assert question.casefold() not in key
        assert "unique-marker-7qx" not in key
        assert key == web_handler._cache_key(question, [])

        history = [("prior-marker-8VZ?", "answer-marker-4RM")]
        history_key = web_handler._cache_key(question, history)
        assert history_key != key
        assert all(marker not in history_key for marker in ("prior-marker", "answer-marker"))

    def test_pii_refusal_precedes_history_parse_and_cache_access(self, monkeypatch):
        def must_not_run(*args, **kwargs):
            raise AssertionError("guarded input reached history, cache, or answer pipeline")

        monkeypatch.setattr(web_handler, "_parse_history", must_not_run)
        monkeypatch.setattr(web_handler, "_cache_get", must_not_run)
        monkeypatch.setattr(web_handler, "answer_question", must_not_run)
        response = web_handler.handler(
            _event(
                body={
                    "question": "My SSN is 123-45-6789; what is the fare?",
                    "history": [{"q": "prior question", "a": "prior answer"}],
                }
            )
        )

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["kind"] == "refused_input"
        assert not web_handler._ANSWER_CACHE

    def test_pii_in_history_is_refused_before_parse_cache_or_model(self, monkeypatch):
        def must_not_run(*args, **kwargs):
            raise AssertionError("guarded history reached parser, cache, or answer pipeline")

        monkeypatch.setattr(web_handler, "_parse_history", must_not_run)
        monkeypatch.setattr(web_handler, "_cache_get", must_not_run)
        monkeypatch.setattr(web_handler, "answer_question", must_not_run)
        response = web_handler.handler(
            _event(
                body={
                    "question": "What is the MST fare?",
                    "history": [
                        {
                            "q": "My email is rider-private@example.com",
                            "a": "Please do not share personal information.",
                        }
                    ],
                }
            )
        )

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["kind"] == "refused_input"
        assert not web_handler._ANSWER_CACHE

    @pytest.mark.parametrize("kind", ["refused_no_support", "answered_guarded"])
    def test_refused_or_guarded_results_are_not_cached(self, monkeypatch, caplog, kind):
        calls = 0

        def guarded_answer(question, **kwargs):
            nonlocal calls
            calls += 1
            return AnswerResult(
                question=question,
                answer="Please contact the transit agency.",
                kind=kind,
                model="bedrock:test" if kind == "answered_guarded" else "",
            )

        monkeypatch.setattr(web_handler, "answer_question", guarded_answer)
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            _post("A safe but unsupported fare-policy question?")
            _post("A safe but unsupported fare-policy question?")

        assert calls == 2
        assert not web_handler._ANSWER_CACHE
        records = _log_records(caplog, "answer_request")
        assert [record.model_called for record in records[-2:]] == [
            kind == "answered_guarded",
            kind == "answered_guarded",
        ]

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

    def test_history_guard_allows_public_agency_phone_in_prior_answer(self):
        checked = web_handler._request_input_check(
            "Where can I apply?",
            [
                {
                    "q": "How do I contact HTA?",
                    "a": "Call Humboldt Transit Authority at 707-443-0826.",
                }
            ],
        )
        assert checked.ok

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

    def test_delimiter_characters_cannot_collide_in_cache_key(self):
        question = "What is the fare?"
        web_handler.handler(
            _event(body={"question": question, "history": [{"q": "a>b", "a": "c"}]})
        )
        web_handler.handler(
            _event(body={"question": question, "history": [{"q": "a", "a": "b>c"}]})
        )
        # The old ``q>a`` / ``|`` join serialized both histories identically.
        assert len(web_handler._ANSWER_CACHE) == 2


class TestHistoryHmac:
    """Optional forged-history hardening (FPA_HISTORY_HMAC_KEY). Off by default;
    when set, only turns this server signed survive _parse_history, and /api/ask
    returns the signature so the client can echo it back."""

    def test_key_unset_accepts_unsigned_history(self, monkeypatch):
        # Default behavior: no key, any well-formed turn is kept as context.
        monkeypatch.delenv("FPA_HISTORY_HMAC_KEY", raising=False)
        out = web_handler._parse_history([{"q": "on MST?", "a": "The fare is $2."}])
        assert out == [("on MST?", "The fare is $2.")]

    def test_key_unset_response_omits_sig(self, monkeypatch):
        monkeypatch.delenv("FPA_HISTORY_HMAC_KEY", raising=False)
        data = json.loads(_post("Do youth ride free on Yolobus?")["body"])
        assert "sig" not in data

    def test_key_set_drops_unsigned_and_tampered_turns(self, monkeypatch):
        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "test-secret")
        good = web_handler._sign_turn("on MST?", "The fare is $2.")
        raw = [
            {"q": "on MST?", "a": "The fare is $2."},  # unsigned → dropped
            {"q": "on MST?", "a": "The fare is $2.", "sig": "0" * 64},  # wrong sig → dropped
            {"q": "on MST?", "a": "The fare is $2.", "sig": good},  # valid → kept
        ]
        out = web_handler._parse_history(raw)
        assert out == [("on MST?", "The fare is $2.")]

    def test_key_set_drops_turn_whose_answer_was_edited(self, monkeypatch):
        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "test-secret")
        sig = web_handler._sign_turn("on MST?", "The fare is $2.")
        # Same signature, but the client rewrote the answer → verification fails.
        out = web_handler._parse_history(
            [{"q": "on MST?", "a": "Veterans ride free everywhere.", "sig": sig}]
        )
        assert out == []

    def test_key_set_response_includes_verifiable_sig(self, monkeypatch):
        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "test-secret")
        resp = _post("Do youth ride free on Yolobus?")
        data = json.loads(resp["body"])
        assert "sig" in data
        # The returned sig is exactly what _parse_history will require on the
        # round trip, so echoing {q, a, sig} back is accepted.
        assert data["sig"] == web_handler._sign_turn(
            "Do youth ride free on Yolobus?", data["answer"]
        )
        echoed = web_handler._parse_history(
            [{"q": "Do youth ride free on Yolobus?", "a": data["answer"], "sig": data["sig"]}]
        )
        assert echoed and echoed[0][0] == "Do youth ride free on Yolobus?"

    def test_sign_turn_has_unambiguous_field_boundaries(self, monkeypatch):
        # Structured signing material prevents delimiter ambiguity:
        # ("x|y","z") must not collide with ("x","y|z").
        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "test-secret")
        assert web_handler._sign_turn("x|y", "z") != web_handler._sign_turn("x", "y|z")

    def test_signed_turn_is_invalid_after_source_containment_change(self, monkeypatch):
        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "test-secret")
        monkeypatch.delenv("FPA_DISABLED_DOC_IDS", raising=False)
        question = "How much is a local fare?"
        answer = "The local fare is $2.00."
        old_sig = web_handler._sign_turn(question, answer)

        monkeypatch.setenv("FPA_DISABLED_DOC_IDS", "yolobus-fares")

        assert old_sig != web_handler._sign_turn(question, answer)
        assert web_handler._parse_history([{"q": question, "a": answer, "sig": old_sig}]) == []

    def test_refusal_is_not_signed_for_follow_up_history(self, monkeypatch):
        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "test-secret")
        monkeypatch.setenv("FPA_DISABLED_DOC_IDS", "yolobus-fares")

        data = json.loads(_post("How much is the local fare on Yolobus?")["body"])

        assert data["kind"] == "refused_no_support"
        assert "sig" not in data

    def test_cache_hit_is_resigned_after_history_key_rotation(self, monkeypatch):
        question = "Do youth ride free on Yolobus?"
        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "old-secret")
        first = json.loads(_post(question)["body"])

        monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", "new-secret")
        cached = json.loads(_post(question)["body"])

        assert cached["answer"] == first["answer"]
        assert cached["sig"] != first["sig"]
        assert cached["sig"] == web_handler._sign_turn(question, cached["answer"])
        assert web_handler._ANSWER_CACHE
        assert all("sig" not in payload for payload in web_handler._ANSWER_CACHE.values())


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

    def test_feedback_logs_no_content(self, caplog):
        # Even if a client sends question/answer text, the handler must not log it.
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            self._fb(
                {
                    "verdict": "down",
                    "kind": "answered",
                    "language": "es",
                    "question": "SECRET-Q",
                    "answer": "SECRET-A",
                }
            )
        record = _log_records(caplog, "feedback")[-1]
        assert record.verdict == "down"
        assert record.kind == "answered"
        assert record.language == "es"
        assert "SECRET-Q" not in repr(vars(record))
        assert "SECRET-A" not in repr(vars(record))

    def test_feedback_does_not_log_free_form_classification_fields(self, caplog):
        with caplog.at_level(logging.INFO, logger="fare_assistant"):
            response = self._fb(
                {
                    "verdict": "up",
                    "kind": {"question": "SECRET-KIND"},
                    "language": ["SECRET-LANGUAGE"],
                }
            )
        assert response["statusCode"] == 200
        record = _log_records(caplog, "feedback")[-1]
        assert record.kind is None
        assert record.language is None
        assert "SECRET" not in repr(vars(record))

    def test_feedback_get_405(self):
        resp = web_handler.handler(
            {
                "requestContext": {"http": {"method": "GET"}},
                "rawPath": "/api/feedback",
                "body": None,
            }
        )
        assert resp["statusCode"] == 405
