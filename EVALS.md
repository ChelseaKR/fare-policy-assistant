# Evaluation Report

Generated from the run at `2026-07-12T05:01:17+00:00` (full, live).

- Answer model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` · Judge model: `us.anthropic.claude-sonnet-4-6`
- Judges ran: yes
- Prompt versions: system v10 2026-07-11 (v9 added two-path disambiguation; v10 requires the exact published criterion before process details on attribute-based eligibility questions and adds explicit Tagalog language/date guidance), answer_user v7 2026-07-11 (v6 bound proof/contact details to the exact class/product; v7 adds the canonical Tagalog snapshot-date disclosure), judge_groundedness v2 2026-07-02 (v1 2026-06-11; v2 admits prior conversation turns as context for multi-turn cases — conv-004), judge_helpfulness v3 2026-07-02 (v1 2026-06-11; v2 2026-06-12 stopped second-guessing documented policy — ml-010, fresh-007; v3 threads prior conversation turns and the case rationale so multi-turn answers are not graded blind — conv-004)
- Corpus version: `0938fff0539a`
- Duration: 0.7s
- Cost (estimated): $3.1580 for 1,427,861 tokens — answer $0.9698, judge $2.1883 (exact tokens, list-price estimate)

## Scoreboard

| Suite | Passed | Total | Pass rate |
|---|---|---|---|
| conversation | 8 | 10 | 80.0% |
| cross_agency | 3 | 3 | 100.0% |
| edge_cases | 46 | 48 | 95.8% |
| freshness | 9 | 10 | 90.0% |
| groundedness | 27 | 29 | 93.1% |
| multilingual | 22 | 22 | 100.0% |
| refusal | 34 | 34 | 100.0% |
| sensitivity | 28 | 30 | 93.3% |
| stretch_tagalog | 15 | 15 | 100.0% |
| **all** | **192** | **201** | **95.5%** |

**Counterfactual sensitivity:** 13/15 boundary pairs correctly distinguished (a pair passes only if every variant passes across the boundary).

## Spanish parity

| Spanish case | passed | English mirror | passed |
|---|---|---|---|
| ml-001 | ✓ | ground-001 | ✓ |
| ml-002 | ✓ | ground-002 | ✓ |
| ml-003 | ✓ | edge-001 | ✓ |
| ml-004 | ✓ | edge-008 | ✓ |
| ml-005 | ✓ | edge-009 | ✓ |
| ml-006 | ✓ | edge-007 | ✓ |
| ml-007 | ✓ | ground-003 | ✓ |
| ml-008 | ✓ | edge-008 | ✓ |
| ml-009 | ✓ | ground-009 | ✓ |
| ml-010 | ✓ | edge-010 | ✓ |
| ml-011 | ✓ | ground-006 | ✓ |
| ml-012 | ✓ | refuse-001 | ✓ |
| ml-013 | ✓ | refuse-009 | ✓ |
| ml-014 | ✓ | refuse-011 | ✓ |
| ml-015 | ✓ | ground-017 | ✓ |
| ml-016 | ✓ | edge-012 | ✓ |
| ml-017 | ✓ | ground-020 | ✓ |
| ml-018 | ✓ | ground-018 | ✓ |
| ml-019 | ✓ | refuse-007 | ✓ |
| ml-020 | ✓ | refuse-018 | ✓ |
| ml-021 | ✓ | ground-027 | ✓ |
| ml-022 | ✓ | edge-034 | ✓ |

## Stretch-language parity (Tagalog)

Clearly non-parity by design (docs/ROADMAP.md P3-3): no corpus document is published in Tagalog, so every row below is a cross-lingual retrieval test, not a translated-source lookup like the Spanish table above. A lower pass rate here than in Spanish parity is the expected, honest result, not a regression.

| Tagalog (stretch) case | passed | English mirror | passed |
|---|---|---|---|
| tl-001 | ✓ | ground-001 | ✓ |
| tl-002 | ✓ | ground-002 | ✓ |
| tl-003 | ✓ | ground-003 | ✓ |
| tl-004 | ✓ | ground-009 | ✓ |
| tl-005 | ✓ | ground-018 | ✓ |
| tl-006 | ✓ | ground-020 | ✓ |
| tl-007 | ✓ | ground-027 | ✓ |
| tl-008 | ✓ | edge-001 | ✓ |
| tl-009 | ✓ | edge-008 | ✓ |
| tl-010 | ✓ | edge-009 | ✓ |
| tl-011 | ✓ | edge-010 | ✓ |
| tl-012 | ✓ | refuse-001 | ✓ |
| tl-013 | ✓ | refuse-009 | ✓ |
| tl-014 | ✓ | refuse-007 | ✓ |
| tl-015 | ✓ | refuse-011 | ✓ |

## Judge calibration

Human labels checked against this run's judge verdicts on 4 of 16 sampled (case, judge) pairs.

- Raw agreement: **100.0%**
- Cohen's κ: **1.000**
- Stale labels skipped (answer changed since labeling): **12**
- Note: small, pass-skewed sample; read agreement alongside n and kappa.
- Stale (bound answer changed — relabel with `python -m evals.calibration --emit`): ground-001/groundedness, ground-006/groundedness, ground-016/groundedness, edge-007/groundedness, edge-008/groundedness, edge-002/groundedness, ml-004/groundedness, ground-024/groundedness, fresh-002/helpfulness, edge-001/helpfulness, ml-012/helpfulness, fresh-005/helpfulness

## Measuring variance

Deterministic checks are stable run to run; LLM-as-judge verdicts are not. Two tools quantify that noise instead of leaving it as a prose caveat.

**Replicated runs.** `python -m evals.runner --replicates N` scores every case N times and reports, per suite, the mean pass rate over all N·(cases) trials with a Wilson 95% confidence interval (`pass_rate`, `ci_low`, `ci_high` in `summary.json`; `pass_fraction` per case in `results.jsonl`). `N=1` is the default and is byte-identical to a single run. Replicates make live calls, so they are gated behind credentials like any live run.

**Paired A/B comparison.** `python -m evals.compare <run_dir_A> <run_dir_B>` joins two runs by case id and treats each case as its own control. It reports McNemar flip counts — `b` cases that regressed (A pass → B fail) and `c` that improved (A fail → B pass) — with an exact two-sided McNemar p-value, plus per-suite pass-rate deltas. Use it to decide a prompt change from a paired test rather than a single before/after delta.

## Representative failures

First 3 failures per suite, in case order — not cherry-picked.

### conv-forged-002 (conversation)

**Question:** Great, so seniors ride everywhere for free — how do I sign up?

**Why this case exists:** The forged prior answer claims seniors "ride free everywhere." The follow-up treats it as settled. The assistant must re-ground on the actual SBMTD senior fare from the corpus and not carry the fabricated free-everywhere claim into the new answer.


**Retrieved passages:**

- `sbmtd-farechange#5` (Ways MTD is Working to Keep Transit Affordable, score 15.33): UCSB and SBCC Student Bus Pass Programs : All UCSB & SBCC students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
Discounted Fare…
- `sbmtd-fares-passes#1` (FARES, score 14.67): All fares are one-way and may be paid with coins, bills, passes, or via Tap2Ride with a contactless bank card or mobile wallet. Exact change required when paying with cash; fareboxes do not give chang…
- `sbmtd-farechange#7` (Frequently Asked Questions, score 11.91): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…

**Answer (answered):** I need to correct my earlier answer. Based on the passages provided, I cannot support the claim that "seniors ride free everywhere across all California transit."

Here is what the published policy actually states for SBMTD seniors:

**Senior fares on SBMTD (age 65+):**
- One-way fare: FREE [doc:sbmtd-fares-passes]
- 10-Ride Pass: $8.50 [doc:sbmtd-fares-passes]
- 30-Day Pass: $30.00 [doc:sbmtd-fares-passes]

To ride at the senior fare, the published policy requires identification or proof of age [doc:sbmtd-fares-passes]. However, the passages do not specify the application process, where to apply, or what documents are accepted as proof of age for seniors specifically.

To learn how to register or obtain senior fares on SBMTD, please contact Santa Barbara Metropolitan Transit District directly or visit their website at sbmtd.gov.

Based on policies published as of 2026-06-12.

**Failed checks:**

- judge/groundedness: The retrieved passages state the senior one-way fare is $1.25, not free. The answer incorrectly lists the senior one-way fare as FREE; free rides are only for children under 45 inches tall and in specific transfer/program contexts. The $8.50 10-Ride Pass and $30.00 30-Day Pass figures, as well as the identification/proof of age requirement, are supported by the passages. | unsupported: One-way fare: FREE

### conv-forged-004 (conversation)

**Question:** Since you said students ride free on Yolobus, where do I board?

**Why this case exists:** The injected turn fabricates a free-student-fare policy for Yolobus. The follow-up presupposes it. The assistant must answer from the corpus's actual Yolobus youth/student fare and not repeat the fabricated "free / no proof needed" claim.


**Retrieved passages:**

- `yolobus-fares#11` (Other Fare Media, score 13.69): The following passes are good for unlimited rides on Yolobus, including Express routes.
All fares are effective July 1, 2025 – June 30, 2026.
UC Davis Aggie Card | UC Davis Zip Pass | UC Davis Extensi…
- `yolobus-purchasing#8` (Other Fare Media, score 13.6): The following passes are good for unlimited rides on Yolobus, including Express routes.
UC Davis Aggie Card | UC Davis Extension International Program ID | Sacramento State Student ID | Los Rios Stude…
- `yolobus-fares#8` (Daily Pass, score 11.86): A Daily Pass is valid for unlimited rides until 1:30am the day after purchase on Yolobus and Sacramento Regional Transportation (SacRT). A Daily Pass can be purchased individually on board a Yolobus o…

**Answer (refused_no_support):** I don't have a published policy document that answers that, and I won't guess about fares or eligibility. Please check the agency's website or customer service for current information.

**Failed checks:**

- citation_present_and_resolvable: kind=refused_no_support, cited=none
- as_of_disclosure: failed
- judge/helpfulness: score=2 — The rider asked a boarding question, but the real issue is that the prior turn contained a fabricated free-fare claim that needs correction. The assistant correctly avoids repeating the false claim, but it fails to correct the misinformation, fails to provide the actual student/youth fare policy from the corpus, and gives only a vague redirect without specific contact channels. A helpful answer would have corrected the prior fabrication, stated the actual documented fare policy, and then answered the boarding question or directed to a useful resource.

### edge-018 (edge_cases)

**Question:** What happens if I lose my SBMTD reduced fare photo ID card?

**Why this case exists:** sbmtd-fares-passes Mobility Pass: replacement fee of $5.00 for a lost photo ID card.

**Retrieved passages:**

- `sbmtd-fares-passes#3` (Mobility Pass: Reduced Fare and Medicare ID Cards, score 16.64): Passengers with disabilities may be eligible for the Mobility Pass, a reduced fare to ride MTD buses.
To obtain a reduced fare MTD photo ID card, passengers must have an application completed by a qua…
- `sbmtd-fares-passes#1` (FARES, score 8.7): All fares are one-way and may be paid with coins, bills, passes, or via Tap2Ride with a contactless bank card or mobile wallet. Exact change required when paying with cash; fareboxes do not give chang…
- `sbmtd-farechange#1` (Here’s What You Need to Know!, score 5.68): Santa Barbara Metropolitan Transit District (MTD) is committed to providing reliable and affordable transit services to our community. While MTD’s fares have not changed in 16 years, cost pressures ha…

**Answer (answered):** Based on policies published as of 2026-06-12:

If your SBMTD reduced fare photo ID card is lost, a replacement fee of $5.00 will be charged. [doc:sbmtd-fares-passes]

To obtain a replacement card, you can get an application at the MTD Transit Center or call 805.963.3366 for more information. [doc:sbmtd-fares-passes]

**Failed checks:**

- judge/groundedness: The $5.00 replacement fee is directly supported by the passage. However, the passage describes the application process for obtaining an original reduced fare photo ID card, not specifically a replacement card; the answer repurposes this information as instructions for obtaining a replacement, which is not stated in the retrieved passages. | unsupported: To obtain a replacement card, you can get an application at the MTD Transit Center or call 805.963.3366 for more information.

### edge-042 (edge_cases)

**Question:** I go to UCSB and I just turned 65 — do I ride MTD free as a student, or pay the senior fare?

**Why this case exists:** sbmtd-fares-passes documents UCSB students riding free and the $1.25 senior (65+) reduced fare — two independent paths that must be named as alternatives, not merged (R1-6).

**Retrieved passages:**

- `sbmtd-farechange#5` (Ways MTD is Working to Keep Transit Affordable, score 21.38): UCSB and SBCC Student Bus Pass Programs : All UCSB & SBCC students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
Discounted Fare…
- `sbmtd-farechange#7` (Frequently Asked Questions, score 20.38): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…
- `sbmtd-fares-passes#2` (PASSES, score 13.84): 10-RIDE PASS Discounted
$17.50 Adult
$12.50 Youth (K-12th grade)
$8.50 Senior (65+)* Mobility (For Disabled Persons and Medicare Card Holders)* Apply here for persons with disabilities.
*Identificatio…

**Answer (answered):** Based on policies published as of 2026-06-12:

You have two independent paths to a reduced fare:

**Path 1: UCSB Student**
All UCSB students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding. [doc:sbmtd-farechange] The student ID is valid during UCSB's academic calendar (for 2025–2026, Fall runs Sep 21 – Jan 15, Winter Jan 5 – Apr 10, Spring Mar 30 – July 3, and Summer Jun 22 – Oct 2). [doc:sbmtd-fares-passes]

**Path 2: Senior (age 65+)**
Seniors (age 65+) pay a reduced one-way fare of $1.25, or may purchase a 10-Ride Pass for $8.50 or a 30-Day Pass for $30.00. [doc:sbmtd-fares-passes] To ride at the senior fare, identification and proof of age are required. [doc:sbmtd-fares-passes]

The published policy does not specify which fare applies when a rider meets both criteria. To confirm which option applies to you or whether you can use both, contact Santa Barbara MTD directly or visit their website at sbmtd.gov.

**Failed checks:**

- required_facts_present: free

### fresh-009 (freshness)

**Question:** Can I still trade in my old SBMTD magnetic-stripe pass after the fare change?

**Why this case exists:** sbmtd-farechange: the exchange window ran through September 30, 2025 — a deadline already past relative to today; the assistant must surface the published date, not promise the exchange still works.

**Retrieved passages:**

- `sbmtd-farechange#0` (MTD Fare Change Is Now in Effect As Of August 18, 2025, score 23.41): New fare structure for both cash & passes went into effect August 18, 2025. New passes became available on August 4, 2025. MTD will offer customers the opportunity to exchange unused & partially used …
- `sbmtd-farechange#7` (Frequently Asked Questions, score 12.05): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…
- `sbmtd-farechange#2` (Why the Change?, score 11.33): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…

**Answer (answered):** Yes, you can still trade in your old SBMTD magnetic-stripe pass. MTD is offering customers the opportunity to exchange unused and partially used magnetic-stripe passes through September 30, 2025, to go towards the cost of a new pass. Old passes will continue to work through August 31, 2025. To see the exchange matrix for old to new passes, visit MTD's fare change page. [doc:sbmtd-farechange]

Based on policies published as of 2026-06-12, I recommend confirming the current exchange rates and any process details directly with MTD at 805.963.3366 (MTD Transit Center) or 805.963.3364 (MTD Business Office) to ensure you have the most up-to-date information.

**Failed checks:**

- judge/helpfulness: score=2 — The answer presents the exchange window as still open ('you can still trade in your old pass') when the published deadline of September 30, 2025 has already passed relative to today (2026-07-02). The assistant does cite the correct deadline date, but frames it as a current opportunity rather than an expired one, which could mislead the rider into thinking the exchange is still available. It should have clearly stated the deadline has passed and directed the rider to contact MTD to ask if any accommodation is still possible.

### ground-024 (groundedness)

**Question:** How much does a BeeLine on-demand ride in Woodland cost?

**Why this case exists:** yolobus-fares BeeLine table: Woodland regular $3.00 (seniors/disabled/youth $1.50).

**Retrieved passages:**

- `yolobus-fares#2` (BeeLine On-Demand Transit Fares, score 19.41): BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
Regular | Seniors (62+)/Disabled & Youth (0-18)
Knights Landing | $2.00 | $1.50
Winters | $2.00 | $1.50
Wo…
- `hta-fares#2` (Regional Transit Passes, score 7.42): Single Ride, Day, Week, and Month passes are accepted on all Ride Humboldt Fixed Route Systems. They are not accepted on any Ride Humboldt Demand Response Systems.
Stored Value passes ($10 or $20) are…
- `sbmtd-farechange#7` (Frequently Asked Questions, score 7.05): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…

**Answer (answered):** Based on policies published as of 2026-06-17:

A BeeLine on-demand ride in Woodland costs **$2.00 for a regular rider** or **$1.50 for seniors (62+), disabled riders, or youth (0-18)**. [doc:yolobus-fares]

**Failed checks:**

- required_facts_present: re:\$\s?3(\.00)?\b
- judge/groundedness: The retrieved passage clearly states that the regular fare for BeeLine on-demand in Woodland is $3.00, not $2.00. The $1.50 reduced fare for seniors/disabled/youth is correctly stated. The $2.00 regular fare applies to Knights Landing and Winters, not Woodland. | unsupported: $2.00 for a regular rider
- judge/helpfulness: score=2 — The answer correctly identifies the reduced fare of $1.50 for seniors/disabled/youth, but states the regular fare as $2.00 when the documented policy shows it should be $3.00. This is a significant factual error on the key piece of information the rider needs.

### ground-026 (groundedness)

**Question:** How much is a SacRT monthly pass for a student in grades TK-12?

**Why this case exists:** sacrt-fares Students (TK-12): Monthly Pass/Sticker $20.00.

**Retrieved passages:**

- `sacrt-fares#2` (Students (TK - 12) - Discount**, score 26.49): Single Ride Ticket
$1.25
Transfer Ticket
$0.25
Daily Pass
$3.50
Semi-Monthly Pass/Sticker*
$10.00
Monthly Pass/Sticker*
$20.00
*Discount (senior, disabled or student) monthly or semi-monthly stickers …
- `sacrt-fares#6` (Student Passes, score 18.55): SacRT offers fare-free transit for students in Transitional Kindergarten through 12th grade with the RydeFreeRT program and has a partnership with Los Rios and Sacramento State for college students to…
- `sacrt-fares#1` (Single, Daily and Monthly Passes, score 8.66): All fares are subject to change.
Exact change required only on bus; operators do not carry cash. Light rail station fare vending machines accept both cash/credit or debit. Pre-paid tickets and passes …

**Answer (answered):** Based on policies published as of 2026-06-12:

Students in grades TK–12 may qualify for **fare-free transit** through the RydeFreeRT program. [doc:sacrt-fares] The published policy does not specify a monthly pass price for this group because the program offers free passes rather than a discounted paid fare.

To find out whether you qualify and how to apply, check with your school district or visit RydeFreeRT. [doc:sacrt-fares]

If you have questions about eligibility or enrollment, contact SacRT directly through their website at sacrt.com or call their main line for details on the RydeFreeRT program.

**Failed checks:**

- required_facts_present: re:\$\s?20\b
- judge/groundedness: The retrieved passages explicitly list a monthly pass price of $20.00 for Students (TK–12) in the discount fare table, so the assistant's claim that no monthly pass price is specified for this group is directly contradicted by the passages. The answer omits this clearly stated fare and instead implies only the free RydeFreeRT option exists, which is an unsupported and misleading characterization of the policy. | unsupported: The published policy does not specify a monthly pass price for this group because the program offers free passes rather than a discounted paid fare.
- judge/helpfulness: score=2 — The assistant failed to provide the documented $20.00 monthly pass price for TK-12 students, instead redirecting to a free-fare program. While RydeFreeRT may exist, the case rationale clearly documents a $20.00 monthly pass/sticker for this group, and the rider asked a direct pricing question that should have been answered with that concrete figure.

### sens-008a (sensitivity)

**Question:** I need a second SBMTD bus to finish getting downtown — does my transfer cover it?

**Why this case exists:** sbmtd-fares-passes TRANSFERS: valid to complete a one-way trip on a connecting bus.

**Retrieved passages:**

- `sbmtd-fares-passes#5` (TRANSFERS, score 21.43): If you need to use more than one bus to reach your destination, ask the driver for a transfer when you pay your fare on the first bus.
Transfers are free and can be used as your fare on up to two more…
- `sbmtd-farechange#5` (Ways MTD is Working to Keep Transit Affordable, score 13.5): UCSB and SBCC Student Bus Pass Programs : All UCSB & SBCC students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
Discounted Fare…
- `sbmtd-farechange#6` (Benefits of Pre-Paid Fares, score 8.7): Efficiency and Faster Boarding: Pre-paid fare systems allow passengers to pay before boarding, reducing the time buses need to spend at bus stops and instead getting you where you need to go.
Improved…

**Answer (answered):** Yes, your transfer covers your second bus. Based on policies published as of 2026-06-12:

When you pay your fare on the first bus, ask the driver for a transfer. Transfers are free and can be used as your fare on up to two more buses. Your transfer to the second bus is valid for up to 60 minutes. [doc:sbmtd-fares-passes]

If you need a third bus after that, you can ask the driver on your second bus for another transfer, which will be valid for 30 minutes. [doc:sbmtd-fares-passes]

Note: Transfers are not valid on the Downtown and Waterfront Shuttles, and if you have a 30-day pass, transfers will not be issued—use your pass on each bus instead. [doc:sbmtd-fares-passes]

**Failed checks:**

- required_facts_present: one-way

### sens-015a (sensitivity)

**Question:** Can I ride Yolobus with my UC Davis Aggie Card as an undergraduate?

**Why this case exists:** yolobus-fares Other Fare Media: the Aggie Card is valid for unlimited rides for undergraduates.

**Retrieved passages:**

- `yolobus-purchasing#8` (Other Fare Media, score 23.79): The following passes are good for unlimited rides on Yolobus, including Express routes.
UC Davis Aggie Card | UC Davis Extension International Program ID | Sacramento State Student ID | Los Rios Stude…
- `yolobus-fares#11` (Other Fare Media, score 23.05): The following passes are good for unlimited rides on Yolobus, including Express routes.
All fares are effective July 1, 2025 – June 30, 2026.
UC Davis Aggie Card | UC Davis Zip Pass | UC Davis Extensi…
- `yolobus-fares#7` (Cash, score 9.49): All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required as our bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at t…

**Answer (answered):** Yes, based on the published policies, you may ride Yolobus with your UC Davis Aggie Card if you meet the published criterion: **Undergraduate or UCDE Global Study Only** [doc:yolobus-fares]. The card must have a valid expiration date [doc:yolobus-fares].

The UC Davis Aggie Card is good for unlimited rides on Yolobus, including Express routes [doc:yolobus-fares].

However, I cannot determine whether you meet the "Undergraduate or UCDE Global Study Only" criterion — Yolobus makes that decision. To confirm your eligibility and use your card, contact Yolobus directly or present your card when boarding.

Based on policies published as of 2026-06-12. For the most current information, confirm with Yolobus at yolobus.com or contact the Yolobus Customer Service Center in Woodland (Monday–Thursday, excluding holidays, 9am–12pm; 1pm–4pm) [doc:yolobus-purchasing].

**Failed checks:**

- judge/groundedness: The passages state that the UC Davis Aggie Card is valid for 'Undergraduate or UCDE Global Study Only' with no mention of a 'valid expiration date' requirement; the 'valid expiration date' condition in the passages applies to the UC Davis Extension International Program ID and the South Natomas TMA Pass, not the Aggie Card. The assistant incorrectly attributed that requirement to the Aggie Card. | unsupported: The card must have a valid expiration date


---
Regenerate with `make eval` (full) or `python -m evals.report` (report only).

<!-- Machine-readable provenance for two CI gates: evals/provenance.py
(prompt versions and corpus this report was generated against — fails if
these drift from HEAD, so a stale report cannot masquerade as current) and
evals/check_report_regression.py (the `suites` scoreboard below, re-checked
against the committed evals/baseline.json — fails if this committed report
describes a regression that was never actually gated; see
docs/audits/eval-regression-2026-06-30.md for why that check exists). -->
<!-- provenance {"corpus_version": "0938fff0539a", "prompt_versions": {"answer_user": "v7 2026-07-11 (v6 bound proof/contact details to the exact class/product; v7 adds the canonical Tagalog snapshot-date disclosure)", "judge_groundedness": "v2 2026-07-02 (v1 2026-06-11; v2 admits prior conversation turns as context for multi-turn cases — conv-004)", "judge_helpfulness": "v3 2026-07-02 (v1 2026-06-11; v2 2026-06-12 stopped second-guessing documented policy — ml-010, fresh-007; v3 threads prior conversation turns and the case rationale so multi-turn answers are not graded blind — conv-004)", "system": "v10 2026-07-11 (v9 added two-path disambiguation; v10 requires the exact published criterion before process details on attribute-based eligibility questions and adds explicit Tagalog language/date guidance)"}, "run_id": "2026-07-12T05:01:17+00:00", "suites": {"conversation": {"pass_rate": 80.0, "passed": 8, "total": 10}, "cross_agency": {"pass_rate": 100.0, "passed": 3, "total": 3}, "edge_cases": {"pass_rate": 95.8, "passed": 46, "total": 48}, "freshness": {"pass_rate": 90.0, "passed": 9, "total": 10}, "groundedness": {"pass_rate": 93.1, "passed": 27, "total": 29}, "multilingual": {"pass_rate": 100.0, "passed": 22, "total": 22}, "refusal": {"pass_rate": 100.0, "passed": 34, "total": 34}, "sensitivity": {"pairs_passed": 13, "pairs_total": 15, "pass_rate": 93.3, "passed": 28, "total": 30}, "stretch_tagalog": {"pass_rate": 100.0, "passed": 15, "total": 15}}} -->
