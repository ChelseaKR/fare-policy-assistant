"""Dependency-free, deterministic character-n-gram language identification.

Replaces the two-regex EN/ES word-count heuristic that used to live in
:mod:`assistant.guards`. That heuristic could not represent *uncertainty* (it
always returned ``"es"`` or ``"en"``), could not be extended without hand-authoring
another word list, and silently misclassified short or code-switched questions.

This module scores an input against committed **character-trigram frequency
profiles** for each supported language (``en`` / ``es`` / ``tl``) and returns a
*confidence* derived from the margin between the best and second-best languages.
When that margin is below a threshold — short, ambiguous, or code-switched text —
the classifier is honest about it: :func:`detect` falls back to English with a
low confidence, and :func:`detect_with_unsure` returns the :data:`UNSURE` sentinel
so a caller can choose to note the uncertainty rather than pretend.

Design constraints (docs/ROADMAP.md P3 §3):

* **Deterministic and offline** — pure standard library, no model download, no
  network, no randomness. The same text always yields the same result.
* **Dependency-free** — only the committed :mod:`json` profile data file
  (``lang_profiles.json``), regenerated from committed sample texts by
  ``tools/build_lang_profiles.py``. Adding a language is a data-file change.
* **Never blocks** — uncertainty is surfaced, not raised. Callers in the answer
  pipeline treat "unsure" as English so a rider is always answered.
"""

from __future__ import annotations

import json
import math
import unicodedata
from functools import lru_cache
from pathlib import Path

#: Sentinel returned by :func:`detect_with_unsure` when no language clears the
#: confidence margin. Not a BCP-47 tag; callers map it to a real language.
UNSURE = "unsure"

#: Language the classifier (and its callers) default to when it is not confident.
#: English is the assistant's source language and universal fallback.
DEFAULT_LANGUAGE = "en"

#: Minimum margin (difference between the top two normalized language
#: probabilities) required to commit to a language. Below this the input is
#: treated as ambiguous / code-switched and the result is "unsure". Tuned on the
#: committed mixed-language test set in ``tests/test_langid.py``.
UNSURE_MARGIN = 0.15

#: Below this many character trigrams the input is too short to classify (e.g.
#: an empty string or a one-word fragment) and is reported as unsure.
_MIN_TRIGRAMS = 2

#: Extra log-score added to Spanish when the text carries a Spanish-only signal
#: (inverted punctuation ``¿¡`` or ``ñ``/accented vowels). A short accented
#: Spanish question is unambiguous to a human; this makes it so to the model too.
_ES_STRONG_BONUS = 1.5
_ES_ONLY_CHARS = frozenset("¿¡ñáéíóúü")

_PROFILE_PATH = Path(__file__).resolve().parent / "lang_profiles.json"


def normalize(text: str) -> str:
    """Fold ``text`` to the lowercase letter/space form the profiles are built on.

    Casefolded; every non-letter run collapses to a single space; the result is
    wrapped in spaces so word-boundary trigrams (``"_th"``, ``"re_"``) are
    captured. Accented Latin letters are preserved (they are signal, especially
    for Spanish) — only combining marks and non-letters are dropped.
    """
    folded = text.casefold()
    out: list[str] = [" "]
    prev_space = True
    for ch in folded:
        if unicodedata.category(ch).startswith("L"):
            out.append(ch)
            prev_space = False
        elif not prev_space:
            out.append(" ")
            prev_space = True
    if not prev_space:
        out.append(" ")
    return "".join(out)


def trigrams(text: str) -> list[str]:
    """Character trigrams of the normalized form of ``text``."""
    norm = normalize(text)
    return [norm[i : i + 3] for i in range(len(norm) - 2)]


@lru_cache(maxsize=1)
def _load_profiles() -> dict:
    with _PROFILE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


class Detection:
    """Result of :func:`classify`: the winning language and how sure we are."""

    __slots__ = ("lang", "confidence", "unsure", "scores")

    def __init__(
        self,
        lang: str,
        confidence: float,
        unsure: bool,
        scores: dict[str, float],
    ) -> None:
        self.lang = lang
        self.confidence = confidence
        self.unsure = unsure
        #: Normalized per-language probabilities (sum to 1), for debugging/tests.
        self.scores = scores

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"Detection(lang={self.lang!r}, confidence={self.confidence:.3f}, "
            f"unsure={self.unsure})"
        )


def classify(text: str) -> Detection:
    """Score ``text`` against every language profile.

    Each language's raw score is the mean log-probability of the input's
    trigrams under that language (unseen trigrams take the language's floor
    value), plus a strong-signal bonus for Spanish-only characters. The raw
    scores are softmax-normalized into a probability distribution; the
    confidence is the margin between the top two. A margin below
    :data:`UNSURE_MARGIN` — or an input too short to have :data:`_MIN_TRIGRAMS`
    trigrams — is reported as ``unsure``.
    """
    data = _load_profiles()
    profiles: dict[str, dict[str, float]] = data["profiles"]
    floors: dict[str, float] = data["floor"]
    langs = list(profiles)

    grams = trigrams(text)
    if len(grams) < _MIN_TRIGRAMS:
        # Too little signal to trust any language; default to English, unsure.
        uniform = {lang: 1.0 / len(langs) for lang in langs}
        return Detection(DEFAULT_LANGUAGE, 0.0, True, uniform)

    raw: dict[str, float] = {}
    for lang in langs:
        profile = profiles[lang]
        floor = floors[lang]
        total = 0.0
        for gram in grams:
            total += profile.get(gram, floor)
        raw[lang] = total / len(grams)

    if "es" in raw and any(ch in _ES_ONLY_CHARS for ch in text.casefold()):
        raw["es"] += _ES_STRONG_BONUS

    # Softmax over the (length-independent) mean log-scores. Shifting by the max
    # keeps exp() well-conditioned and does not change the distribution.
    top = max(raw.values())
    exps = {lang: math.exp(score - top) for lang, score in raw.items()}
    denom = sum(exps.values())
    probs = {lang: value / denom for lang, value in exps.items()}

    ranked = sorted(probs.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    best_lang, best_p = ranked[0]
    second_p = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_p - second_p
    unsure = margin < UNSURE_MARGIN
    return Detection(best_lang, margin, unsure, probs)


def detect(text: str) -> tuple[str, float]:
    """Return ``(lang, confidence)`` with ``confidence`` in ``[0, 1]``.

    When the classifier is not confident (short/ambiguous/code-switched input),
    the language falls back to :data:`DEFAULT_LANGUAGE` and the returned
    confidence is low — the caller gets an honest number, never a crash.
    """
    result = classify(text)
    if result.unsure:
        return DEFAULT_LANGUAGE, result.confidence
    return result.lang, result.confidence


def detect_with_unsure(text: str) -> tuple[str, float]:
    """Like :func:`detect`, but returns the :data:`UNSURE` sentinel when unsure.

    For callers that want to *act* on the uncertainty (e.g. tell the rider "I
    wasn't sure of your language, answering in English") rather than silently
    defaulting.
    """
    result = classify(text)
    if result.unsure:
        return UNSURE, result.confidence
    return result.lang, result.confidence
