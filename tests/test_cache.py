"""Tests for the content-keyed answer/judge cache (evals/cache.py, FIX-12)."""

from __future__ import annotations

import json

import pytest

from assistant.models import Completion
from evals import cache as cache_module
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
        case_semantics_version="a" * 64,
        run_context_version="b" * 64,
        run_judges=True,
        replicates=1,
    )
    key = case_content_key(**base)
    assert key == case_content_key(**base)
    assert case_content_key(**{**base, "case_semantics_version": "c" * 64}) != key
    assert case_content_key(**{**base, "run_context_version": "d" * 64}) != key
    assert case_content_key(**{**base, "run_judges": False}) != key
    assert case_content_key(**{**base, "replicates": 2}) != key


@pytest.mark.parametrize("replicates", [0, -1, True, 1.5])
def test_case_content_key_rejects_invalid_replicates(replicates):
    with pytest.raises(ValueError, match="positive integer"):
        case_content_key(
            case_semantics_version="a" * 64,
            run_context_version="b" * 64,
            run_judges=True,
            replicates=replicates,
        )


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


# ── refresh mode (ADR 0022: the weekly cold CI run) ───────────────────────────


def _entry(text: str) -> dict:
    return {"text": text, "model": "m", "input_tokens": 0, "output_tokens": 0}


def test_refresh_reads_nothing_but_rewrites_what_it_measures(tmp_path):
    warm = EvalCache(tmp_path)
    warm.put_answer("k", _entry("stale"))
    warm.save()

    cold = EvalCache(tmp_path, refresh=True)
    assert cold.get_answer("k") is None  # every lookup misses, so the model is really called
    assert cold.stats()["answer_hits"] == 0
    cold.put_answer("k", _entry("fresh"))
    cold.save()

    # The point of refresh over --no-cache: the store now agrees with the
    # scoreboard the cold run published, so the next cached run cannot
    # republish the answers this run just contradicted.
    assert EvalCache(tmp_path).get_answer("k")["text"] == "fresh"


def test_refresh_preserves_entries_the_run_did_not_touch(tmp_path):
    warm = EvalCache(tmp_path)
    warm.put_answer("kept", _entry("old"))
    warm.put_answer("redone", _entry("old"))
    warm.save()

    cold = EvalCache(tmp_path, refresh=True)
    cold.put_answer("redone", _entry("new"))
    cold.save()

    reloaded = EvalCache(tmp_path)
    assert reloaded.get_answer("kept")["text"] == "old"
    assert reloaded.get_answer("redone")["text"] == "new"


def test_refresh_is_meaningless_without_a_cache_to_write_to(tmp_path):
    assert EvalCache(tmp_path, enabled=False, refresh=True).refresh is False


def test_stats_distinguishes_a_refresh_miss_from_a_cold_cache(tmp_path):
    assert EvalCache(tmp_path).stats()["refresh"] is False
    assert EvalCache(tmp_path, refresh=True).stats()["refresh"] is True


def test_refreshed_caching_model_calls_inner_every_time_and_stores_the_result(tmp_path):
    inner = _FakeModel()
    cache = EvalCache(tmp_path, refresh=True)
    wrapped = CachingModel(inner, cache, provider="mock", kind="answer")

    wrapped.complete("sys", "question one", 100, 0.0)
    wrapped.complete("sys", "question one", 100, 0.0)

    assert inner.calls == 2
    cache.save()
    assert len(json.loads((tmp_path / "answers.json").read_text())) == 1


# ── bounded growth (the store is persisted across CI runs) ────────────────────


def test_save_trims_to_the_most_recently_used_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "MAX_ENTRIES_PER_STORE", 3)
    cache = EvalCache(tmp_path)
    for i in range(5):
        cache.put_answer(f"k{i}", _entry(str(i)))
    cache.save()

    assert set(json.loads((tmp_path / "answers.json").read_text())) == {"k2", "k3", "k4"}


def test_a_served_entry_counts_as_recent_and_survives_the_trim(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "MAX_ENTRIES_PER_STORE", 2)
    cache = EvalCache(tmp_path)
    cache.put_answer("old", _entry("old"))
    cache.put_answer("mid", _entry("mid"))
    cache.get_answer("old")  # a hit is evidence the entry is still in use
    cache.put_answer("new", _entry("new"))
    cache.save()

    assert set(json.loads((tmp_path / "answers.json").read_text())) == {"old", "new"}


def test_rewriting_an_entry_also_marks_it_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "MAX_ENTRIES_PER_STORE", 2)
    cache = EvalCache(tmp_path)
    cache.put_answer("a", _entry("a"))
    cache.put_answer("b", _entry("b"))
    cache.put_answer("a", _entry("a2"))  # a refresh run rewriting what it re-measured
    cache.put_answer("c", _entry("c"))
    cache.save()

    stored = json.loads((tmp_path / "answers.json").read_text())
    assert set(stored) == {"a", "c"}
    assert stored["a"]["text"] == "a2"


def test_trim_leaves_a_store_under_the_cap_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "MAX_ENTRIES_PER_STORE", 10)
    cache = EvalCache(tmp_path)
    for i in range(4):
        cache.put_judge(f"j{i}", _entry(str(i)))
    cache.save()

    assert len(json.loads((tmp_path / "judges.json").read_text())) == 4
