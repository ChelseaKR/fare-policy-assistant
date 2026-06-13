# Evaluation Report

Generated from the run at `2026-06-13T00:50:43+00:00` (full, live).

- Answer model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` · Judge model: `us.anthropic.claude-sonnet-4-6`
- Judges ran: yes
- Prompt versions: system v4 2026-06-12 (v3 added rule-2 share-criteria sentence; v4 adds no-inferred-requirements for ml-005), answer_user v2 2026-06-12 (v1 2026-06-11; v2 adds the unstated-details reminder — eval cases ml-005, ml-011), judge_groundedness v1 2026-06-11, judge_helpfulness v2 2026-06-12 (v1 2026-06-11; v2 stops the judge second-guessing documented policy — eval cases ml-010, fresh-007)
- Duration: 723.0s

## Scoreboard

| Suite | Passed | Total | Pass rate |
|---|---|---|---|
| edge_cases | 26 | 28 | 92.9% |
| freshness | 9 | 10 | 90.0% |
| groundedness | 24 | 26 | 92.3% |
| multilingual | 19 | 20 | 95.0% |
| refusal | 18 | 19 | 94.7% |
| **all** | **96** | **103** | **93.2%** |

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

## Representative failures

First 3 failures per suite, in case order — not cherry-picked.

### edge-002 (edge_cases)

**Question:** I'm 62 — can I ride Yolobus at the senior rate?

**Why this case exists:** yolobus-fares: Senior is 62+. Same age, different agency than edge-001: the boundary pair.

**Retrieved passages:**

- `yolobus-fares#1` (Youth ages 0-18 ride free!, score 13.63): Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
Single Ride Tickets
Local Fare | $2.00 | $1.00
Intercity Fare | $2.25 | $1.00
Express | $3.25 | $1.50
Express Upgrade | $1.00 | …
- `yolobus-reduced-fare-id#0` ((page top), score 11.8): Our senior (62+) and disabled riders can take advantage of Yolobus’ reduced fares. To qualify for reduced fares, riders must show proper identification when purchasing fares and to bus operators when …
- `yolobus-fares#2` (BeeLine On-Demand Transit Fares, score 10.28): BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
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

- judge/groundedness: Nearly all claims are supported by the retrieved passages. However, the SacRT Customer Service & Sales Center address (1225 R St., Sacramento) and phone number (916) 321-2877 are attributed to doc:yolobus-reduced-fare-id, but that passage only mentions the SacRT Customer Service & Sales Center without providing an address or phone number. Those specific details appear in doc:yolobus-fares, but the answer's citation is misleading and the address/phone are not found in the reduced-fare-id passage where they are attributed. | unsupported: SacRT Customer Service & Sales Center, 1225 R St., Sacramento (916) 321-2877 [doc:yolobus-reduced-fare-id]

### edge-025 (edge_cases)

**Question:** Can I ride Yolobus with my UC Davis Aggie Card?

**Why this case exists:** yolobus-fares Other Fare Media: UC Davis Aggie Card valid for unlimited rides, undergraduate or UCDE Global Study only — the restriction is the point.

**Retrieved passages:**

- `yolobus-purchasing#8` (Other Fare Media, score 17.21): The following passes are good for unlimited rides on Yolobus, including Express routes.
UC Davis Aggie Card | UC Davis Extension International Program ID | Sacramento State Student ID | Los Rios Stude…
- `yolobus-fares#11` (Other Fare Media, score 16.83): The following passes are good for unlimited rides on Yolobus, including Express routes.
All fares are effective July 1, 2025 – June 30, 2026.
UC Davis Aggie Card | UC Davis Zip Pass | UC Davis Extensi…
- `yolobus-fares#7` (Cash, score 9.71): All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required as our bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at t…

**Answer (answered):** Yes, you can ride Yolobus with your UC Davis Aggie Card. The UC Davis Aggie Card is good for unlimited rides on Yolobus, including Express routes, provided it is for an Undergraduate or UCDE Global Study participant and has a valid expiration date. [doc:yolobus-fares]

Based on policies published as of 2026-06-12, I recommend confirming the current validity requirements with Yolobus directly at yolobus.com or by contacting their Customer Service Center in Woodland (Monday–Thursday, 9am–12pm and 1pm–4pm, excluding holidays).

**Failed checks:**

- judge/groundedness: The passages indicate the UC Davis Aggie Card is valid for 'Undergraduate or UCDE Global Study Only' and separately note 'with valid student ID' as the condition for the UC Davis Aggie Card in [doc:yolobus-fares]. The answer conflates the eligibility restriction with a 'valid expiration date' requirement, but the 'valid expiration date' condition in the passages applies to the UC Davis Extension International Program ID, not the Aggie Card. The correct condition for the Aggie Card per [doc:yolobus-fares] is 'with valid student ID,' not 'valid expiration date.' | unsupported: provided it is for an Undergraduate or UCDE Global Study participant and has a valid expiration date

### fresh-001 (freshness)

**Question:** How current is your MST fare information?

**Why this case exists:** The assistant must disclose the snapshot date its answers are based on.

**Retrieved passages:**

- `mst-fares#0` (Fares Overview, score 8.66): MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day…
- `mst-veterans-resource#1` (Bus Pass for Veterans, score 8.11): Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their …
- `mst-fares-benefits#0` (Get Your Discount when You Pay Your Fare with a Contactless Card, score 6.65): MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that d…

**Answer (answered_guarded):** I don't have a published policy document that answers that, and I won't guess about fares or eligibility. Please check your transit agency directly, or https://511.org (Bay Area) or the agency's own website for current information.

**Model text the guard blocked (never shown to riders):** Based on policies published as of **2026-06-12**, the MST fare information I have comes from documents fetched on that date, including MST's Fares Overview, Benefits page, Discount Eligibility, Pass Outlets, and related pages [doc:mst-fares, doc:mst-fares-benefits, doc:mst-veterans-resource].

For anything time-sensitive—such as current pass prices, outlet locations, or eligibility verification processes—I recommend confirming directly with MST by phone at **1-888-MST-BUS1 (1-888-678-2871)**, vi

**Failed checks:**

- citation_present_and_resolvable: kind=answered_guarded, cited=none
- as_of_disclosure: failed
- required_facts_present: re:20\d\d
- judge/helpfulness: score=2 — The question asks about the currency/freshness of the assistant's MST fare information—a meta-question the assistant could and should answer by stating what corpus or document dates it has access to. Instead, it deflects entirely as if it were a fare question it can't answer, which doesn't serve the rider. A partial answer acknowledging what it knows about its own knowledge cutoff or document dates would have been more helpful, though the redirect to the agency is at least useful.

### ground-024 (groundedness)

**Question:** How much does a BeeLine on-demand ride in Woodland cost?

**Why this case exists:** yolobus-fares BeeLine table: Woodland regular $3.00 (seniors/disabled/youth $1.50).

**Retrieved passages:**

- `yolobus-fares#2` (BeeLine On-Demand Transit Fares, score 20.71): BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
Regular | Seniors (62+)/Disabled & Youth (0-18)
Knights Landing | $2.00 | $1.50
Winters | $2.00 | $1.50
Wo…
- `sbmtd-farechange#7` (Frequently Asked Questions, score 7.22): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…
- `sbmtd-farechange#2` (Why the Change?, score 6.65): The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has…

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

- `sacrt-fares#2` (Students (TK - 12) - Discount**, score 26.23): Single Ride Ticket
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
- `sacrt-fares#6` (Student Passes, score 18.2): SacRT offers fare-free transit for students in Transitional Kindergarten through 12th grade with the RydeFreeRT program and has a partnership with Los Rios and Sacramento State for college students to…
- `sacrt-fares#1` (Single, Daily and Monthly Passes, score 8.69): All fares are subject to change.
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

- `mst-fares-es#2` (Elegibilidad con descuento, score 31.14): Tarifa con descuento para:
18 años y menos
65 años y más (ver también: Beneficios )
Personas con discapacidad
Titulares de la tarjeta Medicare (ver también: Beneficios )
Veteranos (ver también: Benefi…
- `mst-fares-es#3` (Métodos de pago sin contacto, score 14.32): MST ahora acepta tarjetas bancarias y billeteras móviles sin contacto habilitadas para Visa, Mastercard, Discover y American Express. En este momento, las billeteras móviles aceptadas incluyen: Pago d…
- `mst-fares-es#6` (Tarjetas de cortesía, score 13.04): Las tarjetas de cortesía están disponibles de forma gratuita para todas las personas discapacitadas con una verificación por escrito de un médico o una tarjeta de Medicare. Utilice la tarjeta para tar…

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

- judge/groundedness: The passages state that veterans may obtain an MST Courtesy Card with the listed proof of service, and the Courtesy Cards passage says they are available free of charge to individuals with disabilities (not veterans specifically). The passages do not explicitly state that the veteran courtesy card is free, nor do they confirm that veterans can apply online or at any customer service location (the specific pickup locations listed in the passages are for disability courtesy cards). The seven proof-of-service documents are fully supported. | unsupported: puede solicitar una tarjeta de cortesía del MST de forma gratuita en persona en cualquiera de las ubicaciones de servicio al cliente de MST, o en línea

### refuse-018 (refusal)

**Question:** How much is an Amtrak ticket from Santa Barbara to Los Angeles?

**Why this case exists:** Amtrak fares are outside the corpus, but the corpus does publish the Surfliner-to-MTD free transfer. The ideal answer declines the train price, points to Amtrak, and may share the published transfer fact — so expected is partial, with the groundedness judge policing any invented prices rather than a hard-refusal check.


**Retrieved passages:**

- `sbmtd-fares-passes#0` ((page top), score 9.5): New fares are now effective as of August 18, 2025. For more information click here .
UCSB CAMPUS STORE
1 UCEN Road Santa Barbara, CA 93106 805.893.3819
EASTSIDE LIBRARY
1102 East Montecito Street Sant…
- `sbmtd-fares-passes#6` (Pacific Surfliner’s Transit Transfer Program, score 9.31): On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus or shuttle and ride MTD for free…
- `sbmtd-farechange#3` (What’s Changing?, score 8.38): The table below shows the update to fare structure from the current fare structure. MTD’s goal with this fare adjustment is to restore a portion of the approximately $3.0 million in annual FTA Small T…

**Answer (answered):** I don't have information about Amtrak ticket prices in the passages provided. Those passages cover Santa Barbara MTD bus fares and passes only.

For Amtrak Pacific Surfliner ticket prices and schedules, please visit Amtrak's website directly or call 1-800-USA-RAIL.

However, I can tell you that according to MTD policy: if you have a valid Amtrak Pacific Surfliner paper ticket or e-ticket on the day you travel, you can show it to an MTD bus driver when boarding a connecting bus or shuttle and ride MTD for free [doc:sbmtd-fares-passes].

**Failed checks:**

- as_of_disclosure: failed


---
Regenerate with `make eval` (full) or `python -m evals.report` (report only).
