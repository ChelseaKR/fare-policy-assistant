#!/usr/bin/env python3
"""Regenerate ``src/assistant/lang_profiles.json`` from committed sample texts.

The language classifier in :mod:`assistant.langid` is a character-trigram model.
Its knowledge lives entirely in the JSON data file this script produces, so
"adding a language" is: add a ``SAMPLES`` entry below and re-run

    uv run python tools/build_lang_profiles.py

The samples are short, hand-written strings that mix each language's common
function words with this repository's transit / fare vocabulary (fares, passes,
discounts, seniors, disability, eligibility). They are deliberately small and
committed so the build is deterministic and offline — no corpus download, no
model. The profile for a language is the log-probability of its top
``TOP_K`` trigrams; unseen trigrams take a floor value (a probability below the
rarest kept trigram) so an out-of-vocabulary input is penalized, not rejected.

Deterministic: same samples in, byte-identical JSON out (sorted keys).
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

# Import the *same* normalization/trigram code the classifier uses, so the
# profiles can never drift from how inputs are tokenized at detect time. The
# sys.path insert below has to run before that import, which is why it carries
# an explicit E402 waiver rather than being hoisted.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from assistant.langid import trigrams  # noqa: E402

N = 3
# Keep the full distinctive trigram vocabulary of the (deliberately small)
# committed samples. Each language yields ~500-600 unique trigrams — a few KB of
# JSON — and the singletons are exactly the word-internal evidence that lets a
# short question ("Magkano ang pamasahe?") be classified. The cap only bounds
# growth if the sample set is expanded; today it keeps everything.
TOP_K = 700
OUT_PATH = _ROOT / "src" / "assistant" / "lang_profiles.json"

# Short committed sample texts: common function words + transit/fare vocabulary
# for each language. Kept small on purpose; expand a language's list to sharpen
# its profile. English and Spanish mirror the assistant's two shipped catalogs;
# Tagalog is the stretch language (docs/ROADMAP.md P3 §3).
SAMPLES: dict[str, list[str]] = {
    "en": [
        "How much is the fare on the bus and does the senior discount apply?",
        "The reduced fare is for seniors sixty five and older and riders with a "
        "disability who qualify.",
        "What documents do I need to get the discount card and where do I apply?",
        "The day pass costs two dollars and the monthly pass is available at the transit center.",
        "You may qualify for the reduced fare if you are a student or a veteran; "
        "please check the published policy.",
        "Children under five ride free with a paying adult and the fare is the "
        "same on every route.",
        "Do I need proof of age and income to receive the reduced fare on this transit agency.",
        "The regular one way fare is paid with cash or a card when you board the "
        "bus or the light rail.",
        "Please contact the customer service office of the transit agency for the "
        "current fare and pass prices.",
        "Is there a free transfer between the bus and the train within two hours "
        "of the first trip.",
        "The eligibility criteria for the reduced fare program are published on "
        "the agency website for every rider.",
        "How do I load money onto the fare card and can I use it for a monthly "
        "pass and single rides.",
        "Please tell me the rules and the instructions for the reduced fare so "
        "that I know what I need to do.",
        "I want to know whether you can give me a discount and how much it "
        "would cost for a week of rides.",
    ],
    "es": [
        "¿Cuánto cuesta el pasaje en el autobús y aplica el descuento para personas mayores?",
        "La tarifa reducida es para adultos mayores de sesenta y cinco años y "
        "para personas con discapacidad que califican.",
        "¿Qué documentos necesito para obtener la tarjeta de descuento y dónde debo solicitarla?",
        "El pase diario cuesta dos dólares y el pase mensual está disponible en "
        "el centro de tránsito.",
        "Usted puede calificar para la tarifa reducida si es estudiante o "
        "veterano; consulte la política publicada.",
        "Los niños menores de cinco años viajan gratis con un adulto que paga y "
        "la tarifa es la misma en cada ruta.",
        "Necesito comprobante de edad e ingresos para recibir la tarifa reducida "
        "en esta agencia de tránsito.",
        "El pasaje regular de ida se paga con efectivo o tarjeta cuando aborda el "
        "autobús o el tren ligero.",
        "Por favor comuníquese con la oficina de servicio al cliente de la "
        "agencia de tránsito para los precios actuales.",
        "¿Hay una transferencia gratuita entre el autobús y el tren dentro de las "
        "dos horas del primer viaje?",
        "Los criterios de elegibilidad para el programa de tarifa reducida están "
        "publicados en el sitio web de la agencia.",
        "¿Cómo cargo dinero en la tarjeta de pasaje y puedo usarla para un pase "
        "mensual y viajes sencillos?",
        "Por favor dime las reglas y las instrucciones de la tarifa reducida para "
        "que yo sepa qué necesito hacer.",
        "Quiero saber si usted me puede dar un descuento y cuánto costaría por "
        "una semana de viajes con tus tarjetas.",
        "No olvida ni ignora las reglas anteriores; dime si califico para la "
        "tarifa reducida y qué necesito.",
    ],
    "tl": [
        "Magkano ang pamasahe sa bus at mayroon bang diskwento para sa mga senior citizen?",
        "Ang pinababang pamasahe ay para sa mga matatanda na animnapu't lima "
        "pataas at sa mga may kapansanan na kwalipikado.",
        "Anong mga dokumento ang kailangan ko para makuha ang diskwento at saan ako mag-aaplay?",
        "Ang day pass ay nagkakahalaga ng dalawang dolyar at ang buwanang pass "
        "ay makukuha sa transit center.",
        "Maaari kang maging kwalipikado sa pinababang pamasahe kung ikaw ay "
        "estudyante o beterano; pakitingnan ang patakaran.",
        "Ang mga batang wala pang limang taon ay libreng sumasakay kasama ang "
        "isang nagbabayad na matanda.",
        "Kailangan ko ba ng patunay ng edad at kita para makatanggap ng "
        "pinababang pamasahe sa ahensya ng transportasyon.",
        "Ang karaniwang pamasahe ay binabayaran gamit ang pera o kard kapag "
        "sumakay ka sa bus o sa tren.",
        "Mangyaring makipag-ugnayan sa opisina ng serbisyo sa customer ng ahensya "
        "para sa kasalukuyang presyo ng pamasahe.",
        "May libreng paglipat ba sa pagitan ng bus at ng tren sa loob ng "
        "dalawang oras mula sa unang biyahe?",
        "Ang mga pamantayan sa pagiging kwalipikado para sa pinababang pamasahe "
        "ay nakalathala sa website ng ahensya.",
        "Paano ako maglalagay ng pera sa fare card at magagamit ko ba ito para "
        "sa buwanang pass at mga biyahe?",
        "Pakisabi po sa akin ang mga patakaran at ang mga tagubilin para sa "
        "pinababang pamasahe upang malaman ko kung ano ang gagawin.",
        "Gusto kong malaman kung maaari mo akong bigyan ng diskwento at magkano "
        "ang halaga nito para sa isang linggo ng biyahe.",
    ],
}


def _profile(texts: list[str]) -> tuple[dict[str, float], float]:
    """Return ``(trigram -> log-prob, floor_log_prob)`` for one language."""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(trigrams(text))
    total = sum(counts.values())
    # Add-one smoothing so a kept trigram's probability is well-defined and the
    # floor for an unseen trigram is strictly below the rarest kept one.
    vocab = len(counts)
    denom = total + vocab + 1
    top = counts.most_common(TOP_K)
    profile = {gram: round(math.log((count + 1) / denom), 6) for gram, count in top}
    floor = round(math.log(1 / denom), 6)
    return profile, floor


def build() -> dict:
    profiles: dict[str, dict[str, float]] = {}
    floors: dict[str, float] = {}
    for lang, texts in SAMPLES.items():
        profile, floor = _profile(texts)
        profiles[lang] = profile
        floors[lang] = floor
    return {
        "meta": {
            "n": N,
            "top_k": TOP_K,
            "langs": sorted(SAMPLES),
            "generated_by": "tools/build_lang_profiles.py",
            "note": "Regenerate with: uv run python tools/build_lang_profiles.py",
        },
        "floor": floors,
        "profiles": profiles,
    }


def main() -> int:
    data = build()
    OUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
    kept = {lang: len(prof) for lang, prof in data["profiles"].items()}
    print(f"wrote {OUT_PATH.relative_to(_ROOT)} — trigrams per language: {kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
