"""Content-keyed cache for eval-runner model calls (FIX-12).

`evals/runner.py` re-pays every answer and every judge call on every run, even
when nothing that affects a case changed. This module wraps a `Model` so
identical calls are served from disk instead of the network.

The cache key is the rendered `(provider, model id, system prompt, user
prompt, max_tokens, temperature)` tuple, hashed. That is deliberately more
precise than hashing "prompt version + corpus version + question" separately:
the rendered system/user text already *is* the prompt version, the corpus
version (passages are interpolated into it), the retrieval config (which
passages got retrieved), and the question/turns — so any change to any of
those inputs changes the rendered text and therefore the key. No extra
bookkeeping is needed to keep the key in sync with what actually varies.

Caching assumes the pipeline is deterministic at temperature 0. The model
card notes Bedrock is *not perfectly* deterministic, so:
  * every run summary records whether the cache was enabled and its hit rate
    (`summary["cache"]`), so a suspiciously-fast full run is self-explaining;
  * `--no-cache` disables it outright — use it for FIX-04 variance-measurement
    runs, where repeated identical calls must actually hit the network.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from assistant.models import Completion, Model


def _digest(parts: list[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def completion_key(
    *,
    kind: str,
    provider: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Content key for a single model call. `kind` ("answer" or a judge name)
    only namespaces the two on-disk maps; it does not need to appear in the
    hashed content since the two are stored separately."""
    return _digest([provider, model, system, user, str(max_tokens), f"{temperature:.4f}"])


class EvalCache:
    """On-disk content-keyed cache: two JSON maps under `evals/cache/`,
    `answers.json` and `judges.json`, loaded once and flushed with `save()`.

    A lock guards the in-memory dicts only (not the underlying model call), so
    concurrent cache misses under bounded-concurrency execution still run in
    parallel; a duplicate miss on the same key just costs one extra call, not
    a stale answer (the calls are assumed deterministic).
    """

    def __init__(self, cache_dir: Path, *, enabled: bool = True):
        self.enabled = enabled
        self.dir = cache_dir
        self._lock = threading.Lock()
        self.answer_hits = 0
        self.answer_misses = 0
        self.judge_hits = 0
        self.judge_misses = 0
        self._answers: dict[str, dict] = self._load(self.dir / "answers.json") if enabled else {}
        self._judges: dict[str, dict] = self._load(self.dir / "judges.json") if enabled else {}

    @staticmethod
    def _load(path: Path) -> dict[str, dict]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _get(self, store: dict, key: str, *, is_answer: bool) -> dict | None:
        if not self.enabled:
            return None
        with self._lock:
            hit = store.get(key)
            if is_answer:
                self.answer_hits += int(hit is not None)
                self.answer_misses += int(hit is None)
            else:
                self.judge_hits += int(hit is not None)
                self.judge_misses += int(hit is None)
            return hit

    def get_answer(self, key: str) -> dict | None:
        return self._get(self._answers, key, is_answer=True)

    def put_answer(self, key: str, record: dict) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._answers[key] = record

    def get_judge(self, key: str) -> dict | None:
        return self._get(self._judges, key, is_answer=False)

    def put_judge(self, key: str, record: dict) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._judges[key] = record

    def save(self) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            (self.dir / "answers.json").write_text(
                json.dumps(self._answers, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (self.dir / "judges.json").write_text(
                json.dumps(self._judges, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def stats(self) -> dict:
        a_total = self.answer_hits + self.answer_misses
        j_total = self.judge_hits + self.judge_misses
        return {
            "enabled": self.enabled,
            "answer_hits": self.answer_hits,
            "answer_calls": a_total,
            "answer_hit_rate": round(100 * self.answer_hits / a_total, 1) if a_total else 0.0,
            "judge_hits": self.judge_hits,
            "judge_calls": j_total,
            "judge_hit_rate": round(100 * self.judge_hits / j_total, 1) if j_total else 0.0,
        }


class CachingModel:
    """`Model`-shaped wrapper that serves `complete()` from an `EvalCache`
    when the exact `(provider, model, system, user, max_tokens, temperature)`
    tuple has been seen before, and records a miss otherwise."""

    def __init__(self, inner: Model, cache: EvalCache, *, provider: str, kind: str):
        self._inner = inner
        self._cache = cache
        self._provider = provider
        # "answer" uses the answer-cache namespace; anything else (a judge
        # name) uses the judge-cache namespace.
        self._is_answer = kind == "answer"

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        model = getattr(self._inner, "model", "")
        key = completion_key(
            kind="answer" if self._is_answer else "judge",
            provider=self._provider,
            model=model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        get, put = (
            (self._cache.get_answer, self._cache.put_answer)
            if self._is_answer
            else (self._cache.get_judge, self._cache.put_judge)
        )
        hit = get(key)
        if hit is not None:
            return Completion(**hit)
        completion = self._inner.complete(system, user, max_tokens, temperature)
        put(
            key,
            {
                "text": completion.text,
                "model": completion.model,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
            },
        )
        return completion


def case_content_key(
    *,
    case_id: str,
    question_or_turns: str,
    expected_behavior: str,
    provider: str,
    answer_model: str,
    judge_model: str,
    corpus_version: str,
    prompt_versions: dict[str, str],
    run_judges: bool,
) -> str:
    """Whole-case content key used by `--since`: identifies whether a case's
    inputs are unchanged since a prior run, so that run's record can be
    reused wholesale (no answer call, no judge call, no re-scoring)."""
    return _digest(
        [
            case_id,
            question_or_turns,
            expected_behavior,
            provider,
            answer_model,
            judge_model,
            corpus_version,
            json.dumps(prompt_versions, sort_keys=True),
            str(run_judges),
        ]
    )
