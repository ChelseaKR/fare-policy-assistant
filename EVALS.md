# Evaluation Report

Generated from the run at `2026-06-30T04:35:31+00:00` (full, live).

- Answer model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` · Judge model: `us.anthropic.claude-sonnet-4-6`
- Judges ran: yes
- Prompt versions: system v6 2026-06-29 (v5 added Humboldt Transit; v6: still give an asked-for price when a free program also covers those riders (ground-026), and keep the as-of line on partial answers (refuse-018)), answer_user v3 2026-06-29 (v2 added the unstated-details reminder — ml-005, ml-011; v3 keeps the as-of line on partial answers — refuse-018), judge_groundedness v1 2026-06-11, judge_helpfulness v2 2026-06-12 (v1 2026-06-11; v2 stops the judge second-guessing documented policy — eval cases ml-010, fresh-007)
- Corpus version: `0938fff0539a`
- Duration: 850.3s
- Cost (estimated): $1.7021 for 732,847 tokens — answer $0.4747, judge $1.2274 (exact tokens, list-price estimate)

## Scoreboard

| Suite | Passed | Total | Pass rate |
|---|---|---|---|
| conversation | 4 | 6 | 66.7% |
| edge_cases | 33 | 33 | 100.0% |
| freshness | 10 | 10 | 100.0% |
| groundedness | 29 | 29 | 100.0% |
| multilingual | 18 | 21 | 85.7% |
| refusal | 19 | 19 | 100.0% |
| **all** | **113** | **118** | **95.8%** |

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
| ml-012 | ✗ | refuse-001 | ✓ |
| ml-013 | ✓ | refuse-009 | ✓ |
| ml-014 | ✓ | refuse-011 | ✓ |
| ml-015 | ✗ | ground-017 | ✓ |
| ml-016 | ✓ | edge-012 | ✓ |
| ml-017 | ✓ | ground-020 | ✓ |
| ml-018 | ✓ | ground-018 | ✓ |
| ml-019 | ✓ | refuse-007 | ✓ |
| ml-020 | ✓ | refuse-018 | ✓ |
| ml-021 | ✓ | ground-027 | ✓ |

## Judge calibration

Human labels checked against this run's judge verdicts on 16 of 16 sampled (case, judge) pairs.

- Raw agreement: **87.5%**
- Cohen's κ: **0.429**
- Stale labels skipped (answer changed since labeling): **0**
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

**Answer (answered):** I can't determine your eligibility. That's a decision only you and MST can make together.

What I can share: based on policies published as of 2026-06-12, MST offers discount fares for several groups [doc:mst-fares]:

- 18 years and under
- 65 years and older
- Individuals with disabilities
- Medicare Card holders
- Veterans and veterans' spouses/caregivers
- MST RIDES Paratransit Eligibility card holders
- Special Humanitarian Parole

To verify your eligibility and get a discount linked to a contactless card, you can use Cal-ITP Benefits online—you'll need your ID and a contactless bank card ready [doc:mst-fares-benefits]. Or you can apply for an MST Courtesy Card in person at one of three locations in Monterey, Marina, or Salinas [doc:mst-fares].

If you have questions about which category applies to you or what proof you'll need, contact MST at 1-888-MST-BUS1 (1-888-678-2871) or visit mst.org/fares/.

**Failed checks:**

- judge/helpfulness: score=2 — The question 'just tell me I qualify' lacks any context about who the rider is or what program they're asking about, making it impossible to confirm eligibility. However, the expected behavior is 'answer,' suggesting the assistant should have made a determination. Without prior context establishing the rider's characteristics, the assistant correctly couldn't confirm eligibility, but it did provide a useful list of qualifying categories and next steps. The response is somewhat helpful as a redirect but fails to directly answer the implied question, and the expected behavior of 'answer' suggests there was likely prior context the assistant should have used to give a direct confirmation.

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

**Answer (answered):** Según las políticas publicadas al 2026-06-12, para obtener una tarjeta de cortesía de MST con descuento para veteranos, puede presentar uno de los siguientes documentos como prueba de servicio [doc:mst-fares]:

- Formulario DD 214
- Formulario DD 256N
- Tarjeta de identificación del Departamento de Asuntos de Veteranos de EE. UU.
- Tarjeta de identificación de Veteranos de Guerras Extranjeras
- Tarjeta de membresía de la Legión Americana
- Tarjeta de identificación de Veteranos Estadounidenses Discapacitados
- Licencia de conducir o tarjeta de identificación de California con designación "veterano"

Puede solicitar la tarjeta de cortesía en persona en:
- MST Bus Stop Shop, 201 Pearl Street en Monterey
- Marina Transit Exchange, 280 Reservation Road en Marina
- Salinas Transit Center, 110 Salinas Street en Salinas

O puede completar una solicitud en línea [doc:mst-fares-es].

También puede verificar su elegibilidad para descuentos a través del programa Cal-ITP Benefits si tiene una cuenta de Medicare o Login.gov y una tarjeta bancaria sin contacto [doc:mst-fares-benefits].

**Failed checks:**

- judge/groundedness: The in-person locations listed (MST Bus Stop Shop at 201 Pearl Street, Marina Transit Exchange at 280 Reservation Road, Salinas Transit Center at 110 Salinas Street) appear in the passages only in the context of obtaining courtesy cards for individuals with disabilities, not specifically for veterans. The passages about veterans only mention obtaining an MST Courtesy Card via an application (English/Spanish) without specifying those physical locations for veterans. All other claims about the required documents and the Cal-ITP Benefits program are directly supported by the retrieved passages. | unsupported: Puede solicitar la tarjeta de cortesía en persona en: MST Bus Stop Shop, 201 Pearl Street en Monterey; Marina Transit Exchange, 280 Reservation Road en Marina; Salinas Transit Center, 110 Salinas Street en Salinas

### ml-004 (multilingual)

**Question:** ¿Qué prueba de servicio necesito para la tarifa de veterano en MST?

**Why this case exists:** mst-fares-es: Formulario DD 214 y demás documentos aceptados.

**Retrieved passages:**

- `mst-fares-es#2` (Elegibilidad con descuento, score 32.22): Tarifa con descuento para:
18 años y menos
65 años y más (ver también: Beneficios )
Personas con discapacidad
Titulares de la tarjeta Medicare (ver también: Beneficios )
Veteranos (ver también: Benefi…
- `mst-fares-es#3` (Métodos de pago sin contacto, score 15.27): MST ahora acepta tarjetas bancarias y billeteras móviles sin contacto habilitadas para Visa, Mastercard, Discover y American Express. En este momento, las billeteras móviles aceptadas incluyen: Pago d…
- `mst-fares-es#6` (Tarjetas de cortesía, score 13.73): Las tarjetas de cortesía están disponibles de forma gratuita para todas las personas discapacitadas con una verificación por escrito de un médico o una tarjeta de Medicare. Utilice la tarjeta para tar…

**Answer (answered):** Según las políticas publicadas al 2026-06-12, los veteranos pueden obtener una tarjeta de cortesía del MST con la siguiente prueba de servicio [doc:mst-fares-es]:

- Formulario DD 214
- Formulario DD 256N
- Tarjeta de identificación del Departamento de Asuntos de Veteranos de EE. UU
- Tarjeta de identificación de veteranos de guerras extranjeras
- Tarjeta de membresía de la Legión Americana
- Tarjeta de identificación de veteranos estadounidenses discapacitados
- Licencia de conducir o tarjeta de identificación de CA con designación "veterano"

Puede obtener la solicitud en persona en MST Bus Stop Shop (201 Pearl Street en Monterey), Marina Transit Exchange (280 Reservation Road en Marina), Salinas Transit Center (110 Salinas Street en Salinas), o en línea [doc:mst-fares-es].

Para confirmar los detalles actuales del proceso, le recomendamos contactar directamente a MST.

**Failed checks:**

- judge/groundedness: The seven proof-of-service documents listed in the answer are all directly supported by the retrieved passages. However, the in-person pickup locations (MST Bus Stop Shop at 201 Pearl Street, Marina Transit Exchange at 280 Reservation Road, and Salinas Transit Center at 110 Salinas Street) appear only in the Courtesy Cards section, which describes how disabled individuals can obtain a courtesy card—not veterans specifically. The passages do not state that veterans can pick up their courtesy card application at those locations. | unsupported: Puede obtener la solicitud en persona en MST Bus Stop Shop (201 Pearl Street en Monterey), Marina Transit Exchange (280 Reservation Road en Marina), Salinas Transit Center (110 Salinas Street en Salinas)

### ml-012 (multilingual)

**Question:** Solo dime que sí califico para el descuento de adulto mayor de MST.

**Why this case exists:** Spanish determination-seeking; explain the 65+ criterion, no verdict.

**Retrieved passages:**

- `mst-fares-es#0` (Tarifas Descripción general, score 14.09): MST acepta tarjetas bancarias sin contacto y billeteras móviles Visa, Mastercard, Discover y American Express de a bordo todos los autobuses – y con límite de tarifa, nunca se le cobrará más de $6 por…
- `mst-fares-es#2` (Elegibilidad con descuento, score 11.46): Tarifa con descuento para:
18 años y menos
65 años y más (ver también: Beneficios )
Personas con discapacidad
Titulares de la tarjeta Medicare (ver también: Beneficios )
Veteranos (ver también: Benefi…
- `mst-fares-es#6` (Tarjetas de cortesía, score 9.54): Las tarjetas de cortesía están disponibles de forma gratuita para todas las personas discapacitadas con una verificación por escrito de un médico o una tarjeta de Medicare. Utilice la tarjeta para tar…

**Answer (answered):** No puedo determinar si calificas para el descuento de adulto mayor de MST. 

Según las políticas publicadas al 2026-06-12, los criterios publicados para el descuento de adulto mayor (65 años y más) son: tener 65 años o más [doc:mst-fares-es]. Para usar el descuento al abordar, deberás presentar prueba de edad, una tarjeta de cortesía de MST, o una tarjeta Medicare [doc:mst-fares-es].

Para confirmar tu elegibilidad y obtener una tarjeta de cortesía, puedes:
- Solicitar en persona en MST Bus Stop Shop (201 Pearl Street, Monterey), Marina Transit Exchange (280 Reservation Road, Marina), o Salinas Transit Center (110 Salinas Street, Salinas) [doc:mst-fares-es]
- Llamar a 1-888-MST-BUS1 (1-888-678-2871)
- Visitar mst.org

**Failed checks:**

- judge/helpfulness: score=2 — The rider asked a yes/no question about eligibility, but the assistant refused to give a direct answer despite having the relevant policy criteria (age 65+). Since the assistant doesn't know the rider's age, it could have simply stated the criterion and asked the rider to confirm, or explained that anyone 65+ qualifies — instead it deflected unnecessarily. The redirect information is useful but the core question was not addressed directly.

### ml-015 (multilingual)

**Question:** ¿Cómo funcionan los transbordos en MST si pago en efectivo?

**Why this case exists:** Mirror of ground-017 against mst-fares-es: 'solicite un pase de 2 horas a su conductor'.

**Retrieved passages:**

- `mst-fares-es#0` (Tarifas Descripción general, score 18.27): MST acepta tarjetas bancarias sin contacto y billeteras móviles Visa, Mastercard, Discover y American Express de a bordo todos los autobuses – y con límite de tarifa, nunca se le cobrará más de $6 por…
- `mst-fares-es#3` (Métodos de pago sin contacto, score 13.69): MST ahora acepta tarjetas bancarias y billeteras móviles sin contacto habilitadas para Visa, Mastercard, Discover y American Express. En este momento, las billeteras móviles aceptadas incluyen: Pago d…
- `mst-fares-es#2` (Elegibilidad con descuento, score 11.27): Tarifa con descuento para:
18 años y menos
65 años y más (ver también: Beneficios )
Personas con discapacidad
Titulares de la tarjeta Medicare (ver también: Beneficios )
Veteranos (ver también: Benefi…

**Answer (answered):** Según las políticas publicadas al 2026-06-12:

Si paga en efectivo, debe solicitar un pase de 2 horas a su conductor al abordar su primer autobús. Si necesita hacer transbordo, puede usar ese pase de 2 horas para subir a otro autobús sin pagar tarifa adicional [doc:mst-fares-es].

La política publicada no especifica el precio del pase de 2 horas para efectivo. Para detalles adicionales, le recomendamos contactar a MST directamente al 1-888-MST-BUS1 (1-888-678-2871) o visitar mst.org.

**Failed checks:**

- judge/groundedness: The passages state that when paying cash, you should request a 2-hour pass from the driver when boarding the first bus, but they do not explicitly state that this pass allows boarding another bus without additional payment. The claim that there is no additional fare is an inference not directly supported by the retrieved passages. The price of the 2-hour cash pass ($2.00) is visible in the fare table but the answer correctly notes it is not specified, so that omission is not an unsupported claim. | unsupported: puede usar ese pase de 2 horas para subir a otro autobús sin pagar tarifa adicional


---
Regenerate with `make eval` (full) or `python -m evals.report` (report only).

<!-- Machine-readable provenance for two CI gates: evals/provenance.py
(prompt versions and corpus this report was generated against — fails if
these drift from HEAD, so a stale report cannot masquerade as current) and
evals/check_report_regression.py (the `suites` scoreboard below, re-checked
against the committed evals/baseline.json — fails if this committed report
describes a regression that was never actually gated; see
docs/audits/eval-regression-2026-06-30.md for why that check exists). -->
<!-- provenance {"corpus_version": "0938fff0539a", "prompt_versions": {"answer_user": "v3 2026-06-29 (v2 added the unstated-details reminder — ml-005, ml-011; v3 keeps the as-of line on partial answers — refuse-018)", "judge_groundedness": "v1 2026-06-11", "judge_helpfulness": "v2 2026-06-12 (v1 2026-06-11; v2 stops the judge second-guessing documented policy — eval cases ml-010, fresh-007)", "system": "v6 2026-06-29 (v5 added Humboldt Transit; v6: still give an asked-for price when a free program also covers those riders (ground-026), and keep the as-of line on partial answers (refuse-018))"}, "run_id": "2026-06-30T04:35:31+00:00", "suites": {"conversation": {"pass_rate": 66.7, "passed": 4, "total": 6}, "edge_cases": {"pass_rate": 100.0, "passed": 33, "total": 33}, "freshness": {"pass_rate": 100.0, "passed": 10, "total": 10}, "groundedness": {"pass_rate": 100.0, "passed": 29, "total": 29}, "multilingual": {"pass_rate": 85.7, "passed": 18, "total": 21}, "refusal": {"pass_rate": 100.0, "passed": 19, "total": 19}}} -->
