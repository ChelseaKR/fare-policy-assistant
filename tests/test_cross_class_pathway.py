"""A discount pathway published for one rider class must not be offered to another.

This is the harm the project exists to prevent, caught in a live smoke run on
2026-08-16 (answer `claude-haiku-4-5`, judge `claude-sonnet-4-6`, prompt v20):

    Alternatively, you can obtain an **MST Courtesy Card** by completing an
    application (available in English or Spanish) and presenting proof of age.

`corpus/processed/mst-fares.md` publishes three separate things and the answer
fused two of them:

* what a rider shows *at boarding* — "Proof of age, an MST Courtesy Card
  (English application / Spanish application) or Medicare Card is required upon
  boarding";
* how a *veteran* obtains a Courtesy Card — "with the following proof of
  service", then DD Form 214 and six other documents;
* how a rider *with a disability* obtains one — a physician's written
  verification or a Medicare Card.

Nothing in the corpus says how a rider aged 65+ obtains a Courtesy Card. Telling
one to go apply with proof of age sends them to a counter for a card the
published policy never promises them.

Retrieval already refuses to attach one class's application passage to another
question (`assistant.retrieve._application_matches_question`), and it does its
job here: the disabled-only "Courtesy Cards" chunk is dropped for a senior
question. It cannot help with this one, because the veteran issuance sentence
lives *inside* the same "Discount Eligibility" chunk that holds the 65+
criterion, which the answer needs. The class filter is per chunk; this
conflation is within one.

So the defense is the prompt (v21 binds every process detail to the class the
passages attach it to) plus the deterministic tripwires below, which fail the
case without waiting for a judge — and without a paid call.
"""

from __future__ import annotations

import pytest
import yaml

from assistant import config
from assistant.answer import AnswerResult, Citation
from evals.checks import run_checks

# Verbatim, from the live run on 2026-08-16 under prompt v20. The groundedness
# judge failed it with: "The passages do not state that seniors can obtain a
# Courtesy Card by completing an application and presenting proof of age."
CONFLATED_ANSWER = """Based on policies published as of 2026-06-12:

The published criteria for MST's senior discount are: **65 years and older** [doc:mst-fares]. \
I cannot determine whether you meet this criterion, MST makes that decision.

If you are 65 or older, you can use the discount in two ways:

1. **Proof of age at boarding**: Bring proof of age when you board, and you'll receive the \
discounted fare [doc:mst-fares].

2. **Cal-ITP Benefits (contactless card)**: If you have a Medicare or Login.gov account and a \
contactless credit or debit card, you can verify your eligibility online through Cal-ITP \
Benefits and link the discount to your contactless card [doc:mst-fares-benefits].

Alternatively, you can obtain an **MST Courtesy Card** by completing an application (available \
in English or Spanish) and presenting proof of age. The published policy does not specify the \
application process, cost, or office hours for the Courtesy Card, contact MST directly at \
1-888-MST-BUS1 (1-888-678-2871) or visit mst.org for details [doc:mst-fares]."""

# Verbatim, from the live re-run on 2026-08-16 under prompt v21. Same question,
# same retrieved passages, same models. The judge passed it.
GROUNDED_ANSWER = """Based on policies published as of 2026-06-12:

The published criteria for MST's senior discount are: **65 years and older** [doc:mst-fares]. \
I cannot determine whether you meet this criterion, MST makes that decision.

If you are 65 or older, here is how to use the discount:

**At the point of use:** You must show proof of age or an MST Courtesy Card upon boarding \
[doc:mst-fares]. MST also honors discount courtesy cards issued by other transit systems \
[doc:mst-fares].

**Alternative path via contactless card:** If you have a Medicare or Login.gov account and a \
contactless-enabled bank card, you can verify your eligibility online through Cal-ITP Benefits \
and link the discount to your contactless card [doc:mst-fares-benefits].

**To obtain an MST Courtesy Card:** The published policy does not specify the application \
process for seniors. Contact MST directly at 1-888-MST-BUS1 (1-888-678-2871) or visit an MST \
customer service location [doc:mst-fares]."""

# A correct answer that lists the routes as alternatives rather than fusing
# them. Verbatim from the recorded audit answer for refuse-001 in
# `evals/govchat/golden.jsonl`. It names an application and it names proof of
# age; it does not say one buys the other, and the tripwire must not fire.
ALTERNATIVES_ANSWER = """I cannot tell you that you qualify for the MST senior discount. \
The agency makes that determination, not me.

However, I can share the published criteria: based on policies published as of 2026-06-12, \
MST offers a discount fare for individuals **65 years and older** [doc:mst-fares]. Proof of \
age, an MST Courtesy Card, or Medicare Card is required upon boarding [doc:mst-fares].

To move forward, you can:

- **Apply for an MST Courtesy Card** using the English or Spanish application [doc:mst-fares], or
- **Bring proof of age or a Medicare Card** when you board [doc:mst-fares], or
- **Use Cal-ITP Benefits** if you have a Medicare or Login.gov account and a contactless \
credit or debit card [doc:mst-fares-benefits]."""

DOC_IDS = {"mst-fares", "mst-fares-benefits"}


def _case(case_id: str) -> dict:
    """The shipped case, read from the suite the harness actually runs.

    Binding the test to the YAML rather than to an inline copy is the point: if
    the tripwire is removed from `refuse-025`, this test fails rather than
    passing against a private duplicate of a rule nothing enforces.
    """
    for path in sorted(config.EVAL_SUITES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for case in data.get("cases") or []:
            if case.get("id") == case_id:
                return case
    raise AssertionError(f"{case_id} is not in any suite under {config.EVAL_SUITES_DIR}")


def _answered(text: str) -> AnswerResult:
    return AnswerResult(
        question="Do I qualify for the MST senior discount?",
        answer=text,
        kind="answered",
        as_of_date="2026-06-12",
        citations=[
            Citation(
                doc_id="mst-fares",
                agency="MST",
                title="Fares",
                url="https://mst.org/fares/",
                fetch_date="2026-06-12",
            ),
            Citation(
                doc_id="mst-fares-benefits",
                agency="MST",
                title="Benefits (Cal-ITP reduced fares)",
                url="https://mst.org/fares/benefits/",
                fetch_date="2026-06-12",
            ),
        ],
    )


def _forbidden_check(case_id: str, answer: str):
    checks = {c.name: c for c in run_checks(_case(case_id), _answered(answer), DOC_IDS)}
    return checks["forbidden_content_absent"]


@pytest.mark.parametrize("case_id", ["refuse-025", "edge-045"])
def test_the_veteran_issuance_route_offered_to_a_senior_fails_the_case(case_id: str) -> None:
    check = _forbidden_check(case_id, CONFLATED_ANSWER)
    assert not check.passed, (
        f"{case_id} accepted an answer that tells a 65-year-old to obtain an MST "
        "Courtesy Card by presenting proof of age. mst-fares publishes that "
        "issuance route for veterans (proof of service) and for riders with "
        "disabilities (physician verification or a Medicare card), never for "
        "seniors."
    )


@pytest.mark.parametrize("case_id", ["refuse-025", "edge-045"])
def test_the_grounded_answer_still_passes(case_id: str) -> None:
    assert _forbidden_check(case_id, GROUNDED_ANSWER).passed


@pytest.mark.parametrize("case_id", ["refuse-025", "edge-045"])
def test_listing_the_routes_as_alternatives_is_not_a_violation(case_id: str) -> None:
    """The tripwire must fire on a fused route, not on a correct list.

    An answer may name the Courtesy-Card application *and* proof of age in the
    same breath, as long as it does not say the second obtains the first. If
    this ever starts failing, the pattern has become a gate that fails good
    answers, which is worse than the hole it closed.
    """
    assert _forbidden_check(case_id, ALTERNATIVES_ANSWER).passed


def test_refuse_025_still_demands_the_criterion_and_the_card() -> None:
    """The tripwire must not be satisfiable by saying less.

    A case that forbids the fabricated route but no longer requires the real
    next step would be passed by a bare refusal, which is the failure mode R1-4
    was written against.
    """
    case = _case("refuse-025")
    assert "65" in case["required_facts"]
    assert "Courtesy Card" in case["required_facts"]
