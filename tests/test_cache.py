"""Tests for the content-keyed answer/judge cache (evals/cache.py, FIX-12)."""

from __future__ import annotations

import json

from assistant.models import Completion
from evals.cache import CachingModel, EvalCache, case_content_key, completion_key


class _FakeModel:
    """Counts calls so tests can assert a cache hit skipped the real model."""

    def __init__(self, model: str = "fake-model"):
        self.model = model
        self.calls = 0

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        self.calls += 1
        return Completion(
            text=f"reply to {user}", model=self.model, input_tokens=10, output_tokens=5
        )


# ── completion_key / case_content_key ────────────────────────────────────────


def test_completion_key_is_stable_and_content_sensitive():
    base = dict(
        kind="answer",
        provider="mock",
        model="m",
        system="s",
        user="u",
        max_tokens=10,
        temperature=0.0,
    )
    assert completion_key(**base) == completion_key(**base)
    assert completion_key(**{**base, "user": "different"}) != completion_key(**base)
    assert completion_key(**{**base, "model": "other"}) != completion_key(**base)


def test_completion_key_cannot_collide_across_nul_containing_prompt_boundaries():
    base = dict(
        kind="answer",
        provider="mock",
        model="m",
        system="alpha\0beta",
        user="gamma",
        max_tokens=10,
        temperature=0.0,
    )
    shifted_boundary = {**base, "system": "alpha", "user": "beta\0gamma"}
    assert completion_key(**base) != completion_key(**shifted_boundary)


def test_case_content_key_changes_when_any_input_changes():
    base = dict(
        case_id="c1",
        question_or_turns="q?",
        expected_behavior="answer",
        provider="mock",
        answer_model="a",
        judge_model="j",
        corpus_version="v1",
        prompt_versions={"system": "v1"},
        run_judges=True,
    )
    key = case_content_key(**base)
    assert key == case_content_key(**base)
    assert case_content_key(**{**base, "corpus_version": "v2"}) != key
    assert case_content_key(**{**base, "question_or_turns": "different?"}) != key
    assert case_content_key(**{**base, "run_judges": False}) != key


# ── EvalCache ─────────────────────────────────────────────────────────────────


def test_cache_miss_then_hit_tracks_stats(tmp_path):
    cache = EvalCache(tmp_path)
    assert cache.get_answer("k") is None
    cache.put_answer("k", {"text": "hi", "model": "m", "input_tokens": 1, "output_tokens": 1})
    assert cache.get_answer("k") == {
        "text": "hi",
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 1,
    }
    stats = cache.stats()
    assert stats["answer_hits"] == 1
    assert stats["answer_calls"] == 2  # one miss, one hit
    assert stats["answer_hit_rate"] == 50.0


def test_judge_cache_is_independent_of_answer_cache(tmp_path):
    cache = EvalCache(tmp_path)
    cache.put_answer("k", {"text": "a", "model": "m", "input_tokens": 0, "output_tokens": 0})
    assert cache.get_judge("k") is None  # same key, different namespace
    assert cache.stats()["judge_hits"] == 0
    assert cache.stats()["judge_calls"] == 1


def test_disabled_cache_never_hits_or_persists(tmp_path):
    cache = EvalCache(tmp_path, enabled=False)
    cache.put_answer("k", {"text": "a", "model": "m", "input_tokens": 0, "output_tokens": 0})
    assert cache.get_answer("k") is None
    cache.save()
    assert not (tmp_path / "answers.json").exists()
    assert cache.stats()["enabled"] is False


def test_save_then_reload_round_trips_from_disk(tmp_path):
    cache = EvalCache(tmp_path)
    cache.put_answer("k", {"text": "a", "model": "m", "input_tokens": 1, "output_tokens": 2})
    cache.put_judge("j", {"text": "b", "model": "m", "input_tokens": 3, "output_tokens": 4})
    cache.save()
    assert json.loads((tmp_path / "answers.json").read_text())["k"]["text"] == "a"

    reloaded = EvalCache(tmp_path)
    assert reloaded.get_answer("k")["text"] == "a"
    assert reloaded.get_judge("j")["text"] == "b"


def test_cache_survives_corrupt_on_disk_file(tmp_path):
    (tmp_path / "answers.json").write_text("not json")
    cache = EvalCache(tmp_path)
    assert cache.get_answer("k") is None  # corrupt file treated as empty, not a crash


# ── CachingModel ──────────────────────────────────────────────────────────────


def test_caching_model_serves_second_call_from_cache(tmp_path):
    inner = _FakeModel()
    cache = EvalCache(tmp_path)
    wrapped = CachingModel(inner, cache, provider="mock", kind="answer")

    first = wrapped.complete("sys", "question one", 100, 0.0)
    second = wrapped.complete("sys", "question one", 100, 0.0)

    assert inner.calls == 1  # the real model ran exactly once
    assert first.text == second.text == "reply to question one"
    assert first.input_tokens == 10
    assert second.input_tokens == second.output_tokens == 0
    assert cache.stats()["answer_hits"] == 1


def test_cache_round_trips_cache_bucket_provenance_but_hits_spend_zero(tmp_path):
    class CachedUsageModel(_FakeModel):
        def complete(self, system, user, max_tokens, temperature):
            self.calls += 1
            return Completion(
                text="answer",
                model=self.model,
                input_tokens=100,
                output_tokens=5,
                cache_creation_input_tokens=20,
                cache_read_input_tokens=30,
            )

    cache = EvalCache(tmp_path)
    wrapped = CachingModel(CachedUsageModel(), cache, provider="anthropic", kind="answer")
    first = wrapped.complete("sys", "question", 100, 0.0)
    second = wrapped.complete("sys", "question", 100, 0.0)
    assert (first.cache_creation_input_tokens, first.cache_read_input_tokens) == (20, 30)
    assert (
        second.input_tokens,
        second.cache_creation_input_tokens,
        second.cache_read_input_tokens,
    ) == (
        0,
        0,
        0,
    )


def test_caching_model_misses_on_any_content_change(tmp_path):
    inner = _FakeModel()
    cache = EvalCache(tmp_path)
    wrapped = CachingModel(inner, cache, provider="mock", kind="answer")

    wrapped.complete("sys", "question one", 100, 0.0)
    wrapped.complete("sys", "question two", 100, 0.0)

    assert inner.calls == 2


def test_caching_model_answer_and_judge_kinds_use_separate_namespaces(tmp_path):
    cache = EvalCache(tmp_path)
    answer_model = CachingModel(_FakeModel(), cache, provider="mock", kind="answer")
    judge_model = CachingModel(_FakeModel(), cache, provider="mock", kind="judge")

    answer_model.complete("sys", "same text", 100, 0.0)
    judge_model.complete("sys", "same text", 100, 0.0)  # identical content, different kind

    assert cache.stats()["answer_calls"] == 1
    assert cache.stats()["judge_calls"] == 1


def test_caching_model_disabled_always_calls_inner(tmp_path):
    inner = _FakeModel()
    cache = EvalCache(tmp_path, enabled=False)
    wrapped = CachingModel(inner, cache, provider="mock", kind="answer")

    wrapped.complete("sys", "question one", 100, 0.0)
    wrapped.complete("sys", "question one", 100, 0.0)

    assert inner.calls == 2
