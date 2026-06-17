# Evaluation Report

Generated from the run at `2026-06-17T01:00:41+00:00` (full, live).

- Answer model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` · Judge model: `us.anthropic.claude-sonnet-4-6`
- Judges ran: yes
- Prompt versions: system v4 2026-06-12 (v3 added rule-2 share-criteria sentence; v4 adds no-inferred-requirements for ml-005), answer_user v2 2026-06-12 (v1 2026-06-11; v2 adds the unstated-details reminder — eval cases ml-005, ml-011), judge_groundedness v1 2026-06-11, judge_helpfulness v2 2026-06-12 (v1 2026-06-11; v2 stops the judge second-guessing documented policy — eval cases ml-010, fresh-007)
- Duration: 772.3s
- Cost (estimated): $1.4538 for 606,692 tokens — answer $0.3750, judge $1.0788 (exact tokens, list-price estimate)

## Scoreboard

| Suite | Passed | Total | Pass rate |
|---|---|---|---|
| edge_cases | 27 | 28 | 96.4% |
| freshness | 10 | 10 | 100.0% |
| groundedness | 24 | 26 | 92.3% |
| multilingual | 19 | 20 | 95.0% |
| refusal | 18 | 19 | 94.7% |
| **all** | **98** | **103** | **95.1%** |

## Spanish parity

| Spanish case | passed | English mirror | passed |
|---|---|---|---|
| ml-001 | ✓ | ground-001 | ✓ |
| ml-002 | ✓ | ground-002 | ✓ |
| ml-003 | ✓ | edge-001 | ✓ |
| ml-004 | ✗ | edge-008 | ✓ |
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
| ml-020 | ✓ | refuse-018 | ✗ |

## Judge calibration

Human labels checked against this run's judge verdicts on 15 of 16 sampled (case, judge) pairs.

- Raw agreement: **100.0%**
- Cohen's κ: **1.000**
- Note: small, pass-skewed sample; read agreement alongside n and kappa.
- Unmatched (no judge verdict in this run): edge-002/groundedness

## Representative failures

First 3 failures per suite, in case order — not cherry-picked.

### edge-002 (edge_cases)

**Question:** I'm 62 — can I ride Yolobus at the senior rate?

**Why this case exists:** yolobus-fares: Senior is 62+. Same age, different agency than edge-001: the boundary pair.

**Retrieved passages:**

- `yolobus-fares#1` (Youth ages 0-18 ride free!, score 13.68): Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
Single Ride Tickets
Local Fare | $2.00 | $1.00
Intercity Fare | $2.25 | $1.00
Express | $3.25 | $1.50
Express Upgrade | $1.00 | …
- `yolobus-reduced-fare-id#0` ((page top), score 11.84): Our senior (62+) and disabled riders can take advantage of Yolobus’ reduced fares. To qualify for reduced fares, riders must show proper identification when purchasing fares and to bus operators when …
- `yolobus-fares#2` (BeeLine On-Demand Transit Fares, score 10.31): BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
Regular | Seniors (62+)/Disabled & Youth (0-18)
Knights Landing | $2.00 | $1.50
Winters | $2.00 | $1.50
Wo…

**Answer (answered):** Based on policies published as of 2026-06-12:

The published criteria are: riders aged 62 and older may qualify for Yolobus senior reduced fares. [doc:yolobus-reduced-fare-id]

To use reduced fares, you must show proper identification when purchasing fares and when boarding. [doc:yolobus-reduced-fare-id] The policy lists acceptable forms as: SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver's License or government-issued ID Card; or any other discount ID issued by another transit agency. [doc:yolobus-fares]

You may also obtain a Yolobus reduced fare photo ID by visiting:
- **Yolo Transportation District**, 350 Industrial Way, Woodland, CA 95776  
  Mon–Thu, 9:00 AM–Noon, 1:00–4:00 PM  
  (530) 661-0816

or

- **SacRT Customer Service & Sales Center**, 1225 R St., Sacramento  
  (916) 321-2877 [doc:yolobus-reduced-fare-id]

Senior fares on Yolobus include: Local $1.00, Intercity $1.00, Express $1.50, and monthly passes at $42.00+. [doc:yolobus-fares]

To confirm your eligibility and next steps, contact Yolobus directly at the numbers above.

**Failed checks:**

- (judge only)

### ground-024 (groundedness)

**Question:** How much does a BeeLine on-demand ride in Woodland cost?

**Why this case exists:** yolobus-fares BeeLine table: Woodland regular $3.00 (seniors/disabled/youth $1.50).

**Retrieved passages:**

- `yolobus-fares#2` (BeeLine On-Demand Transit Fares, score 20.75): BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
Regular | Seniors (62+)/Disabled & Youth (0-18)
Knights Landing | $2.00 | $1.50
Winters | $2.00 | $1.50
Wo…
- `sbmtd-farechange#7` (Frequently Asked Questions, score 7.27): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…
- `sbmtd-farechange#2` (Why the Change?, score 6.7): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…

**Answer (answered):** Based on policies published as of 2026-06-12:

A BeeLine on-demand ride in Woodland costs **$2.00 for regular riders** [doc:yolobus-fares]. 

If you are a senior (62+), disabled, or youth (0-18), the fare is **$1.50** [doc:yolobus-fares].

To confirm current fares or if you have questions about your specific situation, contact Yolo County Transportation District directly or visit yolobus.com.

**Failed checks:**

- required_facts_present: re:\$\s?3(\.00)?\b
- judge/groundedness: The retrieved passage clearly states that the regular fare for BeeLine on-demand transit in Woodland is $3.00, not $2.00. The $2.00 regular fare applies to Knights Landing and Winters, not Woodland. The $1.50 reduced fare for seniors/disabled/youth in Woodland is correctly stated. | unsupported: A BeeLine on-demand ride in Woodland costs $2.00 for regular riders

### ground-026 (groundedness)

**Question:** How much is a SacRT monthly pass for a student in grades TK-12?

**Why this case exists:** sacrt-fares Students (TK-12): Monthly Pass/Sticker $20.00.

**Retrieved passages:**

- `sacrt-fares#2` (Students (TK - 12) - Discount**, score 26.34): Single Ride Ticket
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
- `sacrt-fares#6` (Student Passes, score 18.25): SacRT offers fare-free transit for students in Transitional Kindergarten through 12th grade with the RydeFreeRT program and has a partnership with Los Rios and Sacramento State for college students to…
- `sacrt-fares#1` (Single, Daily and Monthly Passes, score 8.72): All fares are subject to change.
Exact change required only on bus; operators do not carry cash. Light rail station fare vending machines accept both cash/credit or debit. Pre-paid tickets and passes …

**Answer (answered):** Based on policies published as of 2026-06-12:

SacRT offers **fare-free transit for students in Transitional Kindergarten through 12th grade through the RydeFreeRT program** [doc:sacrt-fares]. The published policy does not specify a monthly pass price for TK-12 students, because the program provides free passes rather than paid ones.

For details on how to enroll or confirm eligibility, the passages suggest checking with your school district or visiting RydeFreeRT [doc:sacrt-fares].

**Failed checks:**

- required_facts_present: re:\$\s?20\b

### ml-004 (multilingual)

**Question:** ¿Qué prueba de servicio necesito para la tarifa de veterano en MST?

**Why this case exists:** mst-fares-es: Formulario DD 214 y demás documentos aceptados.

**Retrieved passages:**

- `mst-fares-es#2` (Elegibilidad con descuento, score 31.27): Tarifa con descuento para:
18 años y menos
65 años y más (ver también: Beneficios )
Personas con discapacidad
Titulares de la tarjeta Medicare (ver también: Beneficios )
Veteranos (ver también: Benefi…
- `mst-fares-es#3` (Métodos de pago sin contacto, score 14.35): MST ahora acepta tarjetas bancarias y billeteras móviles sin contacto habilitadas para Visa, Mastercard, Discover y American Express. En este momento, las billeteras móviles aceptadas incluyen: Pago d…
- `mst-fares-es#6` (Tarjetas de cortesía, score 13.08): Las tarjetas de cortesía están disponibles de forma gratuita para todas las personas discapacitadas con una verificación por escrito de un médico o una tarjeta de Medicare. Utilice la tarjeta para tar…

**Answer (answered):** Según las políticas publicadas al 2026-06-12, para obtener la tarifa de veterano en MST, puede presentar cualquiera de las siguientes pruebas de servicio:

- Formulario DD 214 [doc:mst-fares-es]
- Formulario DD 256N [doc:mst-fares-es]
- Tarjeta de identificación del Departamento de Asuntos de Veteranos de EE. UU [doc:mst-fares-es]
- Tarjeta de identificación de veteranos de guerras extranjeras [doc:mst-fares-es]
- Tarjeta de membresía de la Legión Americana [doc:mst-fares-es]
- Tarjeta de identificación de veteranos estadounidenses discapacitados [doc:mst-fares-es]
- Licencia de conducir o tarjeta de identificación de CA con designación "veterano" [doc:mst-fares-es]

Con esta prueba, puede solicitar una tarjeta de cortesía del MST de forma gratuita en persona en cualquiera de las ubicaciones de servicio al cliente de MST, o en línea. [doc:mst-fares-es]

Para confirmar los detalles actuales o si tiene preguntas adicionales, le recomendamos contactar directamente a MST.

**Failed checks:**

- judge/groundedness: The passages state that veterans may obtain an MST Courtesy Card with the listed proof of service, and the Courtesy Cards passage mentions obtaining applications at specific locations (MST Bus Stop Shop, Marina Transit Exchange, Salinas Transit Center) or online, but it does not explicitly state that the veteran courtesy card is free of charge. The 'free' claim in the answer is only supported for individuals with disabilities, not explicitly for veterans. | unsupported: puede solicitar una tarjeta de cortesía del MST de forma gratuita en persona en cualquiera de las ubicaciones de servicio al cliente de MST, o en línea

### refuse-018 (refusal)

**Question:** How much is an Amtrak ticket from Santa Barbara to Los Angeles?

**Why this case exists:** Amtrak fares are outside the corpus, but the corpus does publish the Surfliner-to-MTD free transfer. The ideal answer declines the train price, points to Amtrak, and may share the published transfer fact — so expected is partial, with the groundedness judge policing any invented prices rather than a hard-refusal check.


**Retrieved passages:**

- `sbmtd-fares-passes#0` ((page top), score 9.52): New fares are now effective as of August 18, 2025. For more information click here .
UCSB CAMPUS STORE
1 UCEN Road Santa Barbara, CA 93106 805.893.3819
EASTSIDE LIBRARY
1102 East Montecito Street Sant…
- `sbmtd-fares-passes#6` (Pacific Surfliner’s Transit Transfer Program, score 9.33): On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus or shuttle and ride MTD for free…
- `sbmtd-farechange#3` (What’s Changing?, score 8.41): The table below shows the update to fare structure from the current fare structure. MTD’s goal with this fare adjustment is to restore a portion of the approximately $3.0 million in annual FTA Small T…

**Answer (answered):** I don't have information about Amtrak ticket prices in the passages provided. Those passages cover Santa Barbara MTD bus fares and passes only.

For Amtrak Pacific Surfliner ticket prices and schedules, please visit Amtrak's website directly or call 1-800-USA-RAIL.

However, I can tell you that according to MTD policy: if you have a valid Amtrak Pacific Surfliner paper ticket or e-ticket on the day you travel, you can show it to an MTD bus driver when boarding a connecting bus or shuttle and ride MTD for free [doc:sbmtd-fares-passes].

**Failed checks:**

- as_of_disclosure: failed


---
Regenerate with `make eval` (full) or `python -m evals.report` (report only).
