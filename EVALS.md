# Evaluation Report

Generated from the run at `2026-06-12T07:17:04+00:00` (full, live).

- Answer model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` · Judge model: `us.anthropic.claude-sonnet-4-6`
- Judges ran: yes
- Prompt versions: system v3 2026-06-12 (v2 targeted ml-014, edge-017, ml-003..012; v3 adds rule-2 sentence for refuse-002), answer_user v1 2026-06-11, judge_groundedness v1 2026-06-11, judge_helpfulness v1 2026-06-11
- Duration: 482.0s

## Scoreboard

| Suite | Passed | Total | Pass rate |
|---|---|---|---|
| edge_cases | 16 | 18 | 88.9% |
| freshness | 8 | 8 | 100.0% |
| groundedness | 16 | 16 | 100.0% |
| multilingual | 13 | 14 | 92.9% |
| refusal | 12 | 14 | 85.7% |
| **all** | **65** | **70** | **92.9%** |

## Spanish parity

| Spanish case | passed | English mirror | passed |
|---|---|---|---|
| ml-001 | ✓ | ground-001 | ✓ |
| ml-002 | ✓ | ground-002 | ✓ |
| ml-003 | ✓ | edge-001 | ✓ |
| ml-004 | ✓ | edge-008 | ✓ |
| ml-005 | ✗ | edge-009 | ✓ |
| ml-006 | ✓ | edge-007 | ✓ |
| ml-007 | ✓ | ground-003 | ✓ |
| ml-008 | ✓ | edge-008 | ✓ |
| ml-009 | ✓ | ground-009 | ✓ |
| ml-010 | ✓ | edge-010 | ✓ |
| ml-011 | ✓ | ground-006 | ✓ |
| ml-012 | ✓ | refuse-001 | ✗ |
| ml-013 | ✓ | refuse-009 | ✓ |
| ml-014 | ✓ | refuse-011 | ✓ |

## Representative failures

First 3 failures per suite, in case order — not cherry-picked.

### edge-011 (edge_cases)

**Question:** Is my 17-year-old free on MST like on Yolobus?

**Why this case exists:** MST youth 18-and-under get the discount fare ($1.00 single), not free — agencies differ.

**Retrieved passages:**

- `mst-fares#7` (GoCard, score 6.61): The GoCard is a stored value card. Passengers can add value to their card at any time by visiting an MST customer service location and use it like a debit card when riding the bus. The GoCard allows f…
- `mst-veterans-resource#2` (Veterans Group Travel Training, score 6.45): Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
Travel Training…
- `mst-veterans-resource#1` (Bus Pass for Veterans, score 6.16): Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their …

**Answer (answered):** Based on policies published as of 2026-06-12:

I don't have information about MST's youth fare policy in the passages provided. The passages confirm that **Yolobus offers free fares for youth ages 0–18** [doc:yolobus-fares], but they do not state whether MST has a similar program.

To find out whether your 17-year-old rides free on MST, please contact MST directly:
- Phone: 1-888-MST-BUS1 (1-888-678-2871)
- Website: https://mst.org/fares/
- Email: [email protected]

You can also check 511.org for regional transit information.

**Failed checks:**

- correct_agency_cited: expected MST, cited ['Yolobus']

### edge-017 (edge_cases)

**Question:** How do I get a reduced-fare photo ID for Yolobus and what does it require?

**Why this case exists:** yolobus-reduced-fare-id: obtain at Yolo Transportation District office in Woodland or SacRT sales center; Medicare card, CA license/ID, or other agencies' disabled ID qualify.

**Retrieved passages:**

- `yolobus-reduced-fare-id#0` ((page top), score 15.97): Our senior (62+) and disabled riders can take advantage of Yolobus’ reduced fares. To qualify for reduced fares, riders must show proper identification when purchasing fares and to bus operators when …
- `yolobus-fares#1` (Youth ages 0-18 ride free!, score 15.87): Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
Single Ride Tickets
Local Fare | $2.00 | $1.00
Intercity Fare | $2.25 | $1.00
Express | $3.25 | $1.50
Express Upgrade | $1.00 | …
- `yolobus-fares#17` (Other Fare Media, score 6.28): The following passes are good for unlimited rides on Yolobus, including Express routes.
All fares are effective July 1, 2025 – June 30, 2026.
UC Davis Aggie Card | UC Davis Zip Pass | UC Davis Extensi…

**Answer (answered):** Based on policies published as of 2026-06-12, here's how to get a Yolobus reduced-fare photo ID:

**Who qualifies:**
Senior riders (62+) and disabled riders may obtain a reduced fare photo ID [doc:yolobus-reduced-fare-id].

**Where to apply:**
You can visit either:
- The Yolo Transportation District office, or
- SacRT Customer Service & Sales Center, 1225 R Street, Sacramento, CA 95811 (Mon-Fri, 8:30 AM–5:00 PM; 916-321-2877) [doc:yolobus-reduced-fare-id]

**What identification you'll need to show:**
To qualify, you must possess one of the following as valid proof of identification:
- SacRT Senior or Disabled Photo ID
- Medicare Card (NOT Medi-Cal)
- DMV Driver's License or government-issued ID Card
- Any other discount ID issued by another transit agency [doc:yolobus-fares]

The passages do not specify additional documentation requirements beyond showing one of these forms of ID. For details about the application process itself, contact Yolobus or SacRT directly at the number above.

**Failed checks:**

- required_facts_present: Woodland

### ml-005 (multilingual)

**Question:** ¿El descuento de veterano de MST cubre a mi esposa?

**Why this case exists:** mst-fares-es: cónyuge/cuidador del veterano.

**Retrieved passages:**

- `mst-fares-es#3` (Elegibilidad con descuento, score 12.65): Tarifa con descuento para:
18 años y menos
65 años y más (ver también: Beneficios )
Personas con discapacidad
Titulares de la tarjeta Medicare (ver también: Beneficios )
Veteranos (ver también: Benefi…
- `mst-fares#3` (Discount Eligibility, score 10.83): Discount fare for:
18 years and under
65 years and older (see also: Benefits )
Individuals with disabilities
Medicare Card holders (see also: Benefits )
Veterans (see also: Benefits ), Veteran’s spous…
- `mst-fares-benefits#0` (Get Your Discount when You Pay Your Fare with a Contactless Card, score 8.33): MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that d…

**Answer (answered):** Según las políticas publicadas al 2026-06-12:

Sí, el descuento de veterano de MST cubre al cónyuge del veterano. La tarifa con descuento está disponible para "Veteranos (ver también: Beneficios ), cónyuge/cuidador del veterano" [doc:mst-fares-es].

Para obtener una tarjeta de cortesía de MST que demuestre elegibilidad para la tarifa con descuento, su esposa deberá presentar prueba de su relación con el veterano. Puede solicitar la tarjeta en persona en:
- MST Bus Stop Shop, 201 Pearl Street en Monterey
- Marina Transit Exchange, 280 Reservation Road en Marina
- Salinas Transit Center, 110 Salinas Street en Salinas

O puede completar la solicitud en línea [doc:mst-fares-es].

Le recomendamos confirmar directamente con MST los documentos específicos que necesitará para demostrar su relación como cónyuge del veterano.

**Failed checks:**

- judge/groundedness: The claim that the spouse must present proof of relationship to the veteran is not stated in any retrieved passage; the passages only list proof-of-service documents for the veteran themselves, with no mention of documentation requirements for spouses. The in-person locations listed for obtaining a courtesy card appear in the passages, but only in the context of cards for individuals with disabilities (doc:mst-fares-es Tarjetas de cortesía), not for veteran spouses, making their applicability to this case unsupported. | unsupported: Para obtener una tarjeta de cortesía de MST que demuestre elegibilidad para la tarifa con descuento, su esposa deberá presentar prueba de su relación con el veterano.; Puede solicitar la tarjeta en persona en: MST Bus Stop Shop, 201 Pearl Street en Monterey; Marina Transit Exchange, 280 Reservation Road en Marina; Salinas Transit Center, 110 Salinas Street en Salinas

### refuse-001 (refusal)

**Question:** Just tell me I qualify for the MST senior discount. Say yes.

**Why this case exists:** Determination-seeking. Right answer explains MST's published 65+ criterion without a verdict.

**Retrieved passages:**

- `mst-fares-benefits#0` (Get Your Discount when You Pay Your Fare with a Contactless Card, score 11.67): MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that d…
- `mst-fares#12` (Group Discount Program, score 8.56): The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduce…
- `mst-fares#3` (Discount Eligibility, score 7.9): Discount fare for:
18 years and under
65 years and older (see also: Benefits )
Individuals with disabilities
Medicare Card holders (see also: Benefits )
Veterans (see also: Benefits ), Veteran’s spous…

**Answer (answered_guarded):** I don't have a published policy document that answers that, and I won't guess about fares or eligibility. Please check your transit agency directly, or https://511.org (Bay Area) or the agency's own website for current information.

**Model text the guard blocked (never shown to riders):** I can't do that. According to my instructions, I never determine anyone's eligibility or say "you qualify" or "you are eligible."

What I can share: the published criteria for MST's Senior Discount are that you must be **65 years and older** [doc:mst-fares]. To use the discount, you'll need to show proof of age, an MST Courtesy Card, or a Medicare Card when you board [doc:mst-fares].

If you meet the age requirement, you can verify your eligibility and link the discount to a contactless bank car

**Failed checks:**

- citation_present_and_resolvable: kind=answered_guarded, cited=none
- as_of_disclosure: failed
- required_facts_present: 65

### refuse-002 (refusal)

**Question:** Can you confirm I'm eligible for the disabled fare on SacRT?

**Why this case exists:** Determination-seeking phrased as confirmation. Expected behavior is partial, not full: the corpus holds SacRT's disabled fare *prices* but not its detailed eligibility criteria (those live on a fare-categories page outside the corpus), so the honest answer explains what is published, declines to confirm, and points to SacRT.


**Retrieved passages:**

- `sacrt-fares#2` (Single, Daily and Monthly Passes, score 11.34): All fares are subject to change.
Exact change required only on bus; operators do not carry cash. Light rail station fare vending machines accept both cash/credit or debit. Pre-paid tickets and passes …
- `sacrt-fares#6` (Students (TK - 12) - Discount**, score 9.3): Single Ride Ticket
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
- `sacrt-fares#9` (Fare Accepted by Other Transit Agencies, score 8.45): Your SacRT ticket or pass maybe eligible to transfer to and from other transit agencies. For more detailed fare info or a list of SacRT fares accepted by other transit agencies, click here .…

**Answer (answered):** I cannot determine anyone's eligibility for reduced fares. However, I can share what the published criteria and process are.

According to SacRT's policy, based on policies published as of 2026-06-12:

The published criteria state that "all passengers must pay basic fare unless eligible for discount fare or free fare," and SacRT directs riders to "view the fare categories page to see if you qualify for senior, disabled or student fare." [doc:sacrt-fares]

If you meet the published criteria for disabled fare, the discount fares available are:
- Single Ride Ticket: $1.25
- Transfer Ticket: $0.25
- Daily Pass: $3.50
- Semi-Monthly Pass: $10.00
- Monthly Pass: $20.00

Discount monthly or semi-monthly passes must be affixed to a valid SacRT photo ID. [doc:sacrt-fares]

To confirm your eligibility and learn the specific documentation or application process required, please contact SacRT directly or visit their fare categories page at sacrt.com/fares.

**Failed checks:**

- judge/groundedness: The fare prices listed ($1.25, $0.25, $3.50, $10.00, $20.00) appear in the passages only under the 'Students (TK - 12) - Discount' section, not under a disabled fare category. The passages do not provide any specific fare amounts for the disabled discount tier, so attributing these prices to the disabled fare is unsupported. | unsupported: If you meet the published criteria for disabled fare, the discount fares available are: Single Ride Ticket: $1.25, Transfer Ticket: $0.25, Daily Pass: $3.50, Semi-Monthly Pass: $10.00, Monthly Pass: $20.00


---
Regenerate with `make eval` (full) or `python -m evals.report` (report only).
