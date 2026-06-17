# Evaluation Report

Generated from the run at `2026-06-17T03:11:14+00:00` (full, live).

- Answer model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` · Judge model: `us.anthropic.claude-sonnet-4-6`
- Judges ran: yes
- Prompt versions: system v5 2026-06-16 (v4 added no-inferred-requirements for ml-005; v5 adds Humboldt Transit as the fifth agency), answer_user v2 2026-06-12 (v1 2026-06-11; v2 adds the unstated-details reminder — eval cases ml-005, ml-011), judge_groundedness v1 2026-06-11, judge_helpfulness v2 2026-06-12 (v1 2026-06-11; v2 stops the judge second-guessing documented policy — eval cases ml-010, fresh-007)
- Duration: 922.6s
- Cost (estimated): $1.6856 for 712,254 tokens — answer $0.4558, judge $1.2298 (exact tokens, list-price estimate)

## Scoreboard

| Suite | Passed | Total | Pass rate |
|---|---|---|---|
| conversation | 4 | 6 | 66.7% |
| edge_cases | 33 | 33 | 100.0% |
| freshness | 10 | 10 | 100.0% |
| groundedness | 27 | 29 | 93.1% |
| multilingual | 20 | 21 | 95.2% |
| refusal | 18 | 19 | 94.7% |
| **all** | **112** | **118** | **94.9%** |

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
| ml-010 | ✗ | edge-010 | ✓ |
| ml-011 | ✓ | ground-006 | ✓ |
| ml-012 | ✓ | refuse-001 | ✓ |
| ml-013 | ✓ | refuse-009 | ✓ |
| ml-014 | ✓ | refuse-011 | ✓ |
| ml-015 | ✓ | ground-017 | ✓ |
| ml-016 | ✓ | edge-012 | ✓ |
| ml-017 | ✓ | ground-020 | ✓ |
| ml-018 | ✓ | ground-018 | ✓ |
| ml-019 | ✓ | refuse-007 | ✓ |
| ml-020 | ✓ | refuse-018 | ✗ |
| ml-021 | ✓ | ground-027 | ✓ |

## Judge calibration

Human labels checked against this run's judge verdicts on 16 of 16 sampled (case, judge) pairs.

- Raw agreement: **93.8%**
- Cohen's κ: **0.636**
- Note: small, pass-skewed sample; read agreement alongside n and kappa.

## Representative failures

First 3 failures per suite, in case order — not cherry-picked.

### conv-004 (conversation)

**Conversation:**

1. How much is the senior fare on MST?
2. So just tell me I qualify.

**Why this case exists:** Determination-seeking as a follow-up. The guard must hold across turns: explain the 65+ criterion, never rule on the rider, even with the senior context already established.


**Retrieved passages:**

- `mst-fares-benefits#0` (Get Your Discount when You Pay Your Fare with a Contactless Card, score 9.73): MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that d…
- `mst-fares#0` (Fares Overview, score 8.34): MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day…
- `mst-veterans-resource#1` (Bus Pass for Veterans, score 7.93): Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their …

**Answer (answered):** I can't determine anyone's eligibility. That's a decision only you and MST can make together.

What I can do is share the published criteria. Based on policies published as of 2026-06-12:

MST offers discount fares for [doc:mst-fares]:
- 18 years and under
- 65 years and older
- Individuals with disabilities
- Medicare Card holders
- Veterans and veterans' spouses/caregivers
- MST RIDES Paratransit Eligibility card holders
- Special Humanitarian Parole

To use a discount fare, you'll need to show proof of eligibility when you board. The published policy does not specify what documents prove each category — only that proof of age, an MST Courtesy Card, or Medicare Card is required [doc:mst-fares].

If you think you may qualify for one of these categories, contact MST to confirm:
- Phone: 1-888-MST-BUS1 (1-888-678-2871)
- Email: [email protected]
- Website: mst.org

**Failed checks:**

- judge/groundedness: The answer claims the policy does not specify what documents prove each category, but the retrieved passage explicitly lists multiple acceptable proof-of-service documents for veterans (DD Form 214, DD Form 256N, US Department of Veterans Affairs ID card, Veterans of Foreign Wars ID card, Membership card from the American Legion, Disabled American Veterans ID card, CA driver license or ID card with 'veteran' designation). This makes the claim that documents are unspecified for categories other than age/Courtesy Card/Medicare inaccurate and unsupported by the passages. | unsupported: The published policy does not specify what documents prove each category — only that proof of age, an MST Courtesy Card, or Medicare Card is required
- judge/helpfulness: score=2 — The expected behavior is 'answer,' meaning the assistant should provide the concrete eligibility criteria a rider needs. However, the assistant refuses to confirm eligibility and hedges extensively, even though the question likely just wants the published criteria explained clearly. The assistant does list the discount categories and contact info, which is partially useful, but the framing ('I can't determine anyone's eligibility') is unnecessarily cautious and doesn't serve a rider who simply wants to know if they qualify based on published policy. A better response would directly present the criteria and let the rider self-assess.

### conv-005 (conversation)

**Conversation:**

1. ¿Cuánto cuesta el pasaje de adulto mayor en MST?
2. ¿Y para veteranos qué documentos necesito?

**Why this case exists:** Spanish multi-turn against mst-fares-es: the veteran follow-up inherits the MST context and must list the DD 214 proof of service, in Spanish.


**Retrieved passages:**

- `mst-fares-es#2` (Elegibilidad con descuento, score 20.52): Tarifa con descuento para:
18 años y menos
65 años y más (ver también: Beneficios )
Personas con discapacidad
Titulares de la tarjeta Medicare (ver también: Beneficios )
Veteranos (ver también: Benefi…
- `mst-veterans-resource#1` (Bus Pass for Veterans, score 7.96): Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their …
- `mst-fares#2` (Discount Eligibility, score 7.08): Discount fare for:
18 years and under
65 years and older (see also: Benefits )
Individuals with disabilities
Medicare Card holders (see also: Benefits )
Veterans (see also: Benefits ), Veteran’s spous…

**Answer (answered):** Según las políticas publicadas al 2026-06-12, para obtener una tarjeta de cortesía de MST como veterano, puede presentar uno de los siguientes documentos de prueba de servicio [doc:mst-fares]:

- Formulario DD 214
- Formulario DD 256N
- Tarjeta de identificación del Departamento de Asuntos de Veteranos de EE. UU.
- Tarjeta de identificación de Veteranos de Guerras Extranjeras
- Tarjeta de membresía de la Legión Americana
- Tarjeta de identificación de Veteranos Estadounidenses Discapacitados
- Licencia de conducir o tarjeta de identificación de California con designación "veterano"

Con la tarjeta de cortesía, puede acceder a tarifas con descuento [doc:mst-fares].

Además, MST ofrece un programa de viajes en taxi para veteranos y proporciona entrenamiento gratuito en viajes de ruta fija [doc:mst-veterans-resource].

Para más información o para solicitar la tarjeta, puede llamar a MST al 1-888-MST-BUS1 (1-888-678-2871) o contactar a [email protected].

**Failed checks:**

- judge/groundedness: The phone number 1-888-MST-BUS1 appears in the passages only in the context of the Group Discount Program, not for veteran courtesy card inquiries. The email address [email protected] appears in the passages only for the free bus pass partnership program for selected veterans, not as a general contact for veteran courtesy card applications. Attributing these contact details to veteran courtesy card inquiries is not supported by the retrieved passages. | unsupported: puede llamar a MST al 1-888-MST-BUS1 (1-888-678-2871); contactar a [email protected]

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

**Answer (answered):** Based on policies published as of 2026-06-12:

A BeeLine on-demand ride in Woodland costs **$2.00 for a regular adult fare** or **$1.50 for seniors (62+), disabled riders, or youth (0-18)** [doc:yolobus-fares]. These are one-way, flat-fee fares.

**Failed checks:**

- required_facts_present: re:\$\s?3(\.00)?\b
- judge/groundedness: The retrieved passage clearly states the regular fare for Woodland BeeLine on-demand is $3.00, not $2.00. The $1.50 reduced fare for seniors/disabled/youth is correctly stated, but the regular adult fare is wrong and unsupported by the passages. | unsupported: $2.00 for a regular adult fare

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

SacRT offers fare-free transit for students in Transitional Kindergarten through 12th grade through the RydeFreeRT program [doc:sacrt-fares]. The published policy does not specify a monthly pass price for TK-12 students, since the program provides free passes rather than paid passes.

For details about how to enroll or confirm your eligibility, contact your school district or visit RydeFreeRT [doc:sacrt-fares].

**Failed checks:**

- required_facts_present: re:\$\s?20\b
- judge/groundedness: The passages explicitly list a Monthly Pass/Sticker price of $20.00 for Students (TK-12) in the discount fare table, so the assistant's claim that no monthly pass price is specified is directly contradicted by the retrieved passages. While the RydeFreeRT program is also mentioned, the existence of a paid $20 monthly pass option is clearly documented. | unsupported: The published policy does not specify a monthly pass price for TK-12 students

### ml-010 (multilingual)

**Question:** ¿Los niños viajan gratis en Yolobus?

**Why this case exists:** Cross-lingual: Youth ages 0-18 ride free.

**Retrieved passages:**

- `yolobus-fares#1` (Youth ages 0-18 ride free!, score 6.85): Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
Single Ride Tickets
Local Fare | $2.00 | $1.00
Intercity Fare | $2.25 | $1.00
Express | $3.25 | $1.50
Express Upgrade | $1.00 | …
- `yolobus-purchasing#2` (Mobile / Transit Connect, score 3.91): The Transit Connect mobile fare app allows you to pre-purchase, store and activate tickets and passes instantly on your smartphone. Accepted on Yolobus and SacRT, with Unitrans joining in summer 2026.…
- `yolobus-fares#0` (Yolobus Fixed Route Bus Fares, score 3.86): Yolobus fixed route bus fares vary depending on which type of service you ride. All below fares are effective July 1, 2025 – June 30, 2026 .…

**Answer (answered):** Sí, según las políticas publicadas al 2026-06-12:

**Jóvenes de 0 a 18 años viajan gratis en Yolobus.** [doc:yolobus-fares]

Sin embargo, la política publicada no especifica si esta tarifa gratuita aplica también a los servicios BeeLine (microtránsito bajo demanda), que tienen una estructura de tarifas diferente. Para confirmar si tu hijo puede viajar gratis en BeeLine, te recomendamos contactar directamente a Yolobus.

**Failed checks:**

- judge/groundedness: The claim that the free youth fare policy does not specify whether it applies to BeeLine is contradicted by the retrieved passages, which explicitly show that BeeLine on-demand fares for Youth (0-18) are $1.50, not free. Therefore, the passages do specify BeeLine youth fares, making the assistant's claim that this is unspecified unsupported and actually misleading. | unsupported: la política publicada no especifica si esta tarifa gratuita aplica también a los servicios BeeLine (microtránsito bajo demanda)

### refuse-018 (refusal)

**Question:** How much is an Amtrak ticket from Santa Barbara to Los Angeles?

**Why this case exists:** Amtrak fares are outside the corpus, but the corpus does publish the Surfliner-to-MTD free transfer. The ideal answer declines the train price, points to Amtrak, and may share the published transfer fact — so expected is partial, with the groundedness judge policing any invented prices rather than a hard-refusal check.


**Retrieved passages:**

- `sbmtd-fares-passes#0` ((page top), score 9.96): New fares are now effective as of August 18, 2025. For more information click here .
UCSB CAMPUS STORE
1 UCEN Road Santa Barbara, CA 93106 805.893.3819
EASTSIDE LIBRARY
1102 East Montecito Street Sant…
- `sbmtd-fares-passes#6` (Pacific Surfliner’s Transit Transfer Program, score 9.08): On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus or shuttle and ride MTD for free…
- `sbmtd-farechange#3` (What’s Changing?, score 8.73): The table below shows the update to fare structure from the current fare structure. MTD’s goal with this fare adjustment is to restore a portion of the approximately $3.0 million in annual FTA Small T…

**Answer (answered):** I don't have information about Amtrak ticket prices in the passages provided. Those passages cover Santa Barbara MTD's local transit fares and programs only.

For Amtrak Pacific Surfliner ticket prices, please contact Amtrak directly or visit amtrak.com.

However, I can tell you that according to MTD's policy, if you have a valid Amtrak Pacific Surfliner paper ticket or e-ticket, you can ride MTD buses for free on the day you travel—just show your ticket to the driver when boarding [doc:sbmtd-fares-passes].

**Failed checks:**

- as_of_disclosure: failed


---
Regenerate with `make eval` (full) or `python -m evals.report` (report only).
