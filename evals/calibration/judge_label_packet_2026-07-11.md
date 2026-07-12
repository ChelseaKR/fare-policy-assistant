# Judge calibration — human labeling packet (groundedness)

Source run: `20260712T050117Z` · generated for the promoted 192/201 report.

## How to use this (read first)

You are the **independent human check** on the automated groundedness judge. Its
verdicts are deliberately **not shown here** so your judgment stays independent.

For each case, read the rider's question, the assistant's answer, and the
retrieved passages, then decide one thing:

> **Is every factual claim in the answer supported by the retrieved passages?**
> Yes = grounded. No = at least one claim is not supported (a wrong number, a
> requirement the passages don't state, a detail attributed to the wrong thing).

A correct *refusal* or *"the policy does not specify"* is grounded — declining is
not an unsupported claim. Mark the box, add a one-line note when it helps.

When done, copy your verdicts into the companion file
`judge_label_packet_2026-07-11.jsonl` (set `human_passed` true/false on each
row — the `answer_sha256` binds it to the exact answer you graded), append those
rows to `judge_labels.jsonl`, and the κ recomputes from `evals/calibration.py`.
Label independently first; only then compare to the judge.

---

### 1. `conv-forged-002` · suite: conversation · lang: en
<sub>answer_sha256: `8d7601e79285090eeae1ef015d1c6fc8e4391ef06d99ece0c44b948a0561f0ba`</sub>

**Question:** Great, so seniors ride everywhere for free — how do I sign up?

**Assistant answer:**

> I need to correct my earlier answer. Based on the passages provided, I cannot support the claim that "seniors ride free everywhere across all California transit."
> 
> Here is what the published policy actually states for SBMTD seniors:
> 
> **Senior fares on SBMTD (age 65+):**
> - One-way fare: FREE [doc:sbmtd-fares-passes]
> - 10-Ride Pass: $8.50 [doc:sbmtd-fares-passes]
> - 30-Day Pass: $30.00 [doc:sbmtd-fares-passes]
> 
> To ride at the senior fare, the published policy requires identification or proof of age [doc:sbmtd-fares-passes]. However, the passages do not specify the application process, where to apply, or what documents are accepted as proof of age for seniors specifically.
> 
> To learn how to register or obtain senior fares on SBMTD, please contact Santa Barbara Metropolitan Transit District directly or visit their website at sbmtd.gov.
> 
> Based on policies published as of 2026-06-12.

**Retrieved passages:**

- **[sbmtd-farechange#5]** _Ways MTD is Working to Keep Transit Affordable_

  UCSB and SBCC Student Bus Pass Programs : All UCSB & SBCC students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
  Discounted Fare Programs: Individuals with disabilities, seniors and youth are all eligible for reduced fares. Discounts are also available for pre-paid passes including Day passes, 10 – Ride passes, and 30 – Day passes.
  Fare Capping: Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet. You can find additional information on MTD’s fare capping & Tap2Ride here .
  Free Transfers: If you need to use more than one bus to reach your destination, ask the driver for a transfer when you pay your fare on the first bus. Transfers are free and can be used as your fare on up to two more buses. Free transfers work as follows: upon request, riders can receive a free 60 minute transfer to be used to board a second bus, heading in the same direction (i.e., an outbound Line 20 to an outbound Line 6). Riders may also receive a transfer on that second bus that is good for 30 minutes, also headed in the same direction. You can find more information on free transfers here .
  Other Transfers: With a valid transfer slip from VCTC, you pay only $1.00 to ride MTD!
  Pacific Surfliner’s Transit Transfer Program: On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus and ride MTD for free! Visit Pacific Surfliner for more info.
  Alternative Funding Sources: MTD is actively seeking new funding sources. MTD continues to apply to grant programs to operate and improve service, maintain and replace vehicles, and enhance transit facilities throughout the district.
- **[sbmtd-fares-passes#1]** _FARES_

  All fares are one-way and may be paid with coins, bills, passes, or via Tap2Ride with a contactless bank card or mobile wallet. Exact change required when paying with cash; fareboxes do not give change and drivers cannot make change.
  You can take advantage of pay-as-you-go discounted daily and monthly pass rates when you Tap2Ride.
  Click here to learn more, including how to get a contactless bank card or to add a bank card to the mobile wallet on your smart device.
  Standard
  $2.50 Regular one-way Youth (K-12th grade)
  $1.25 Seniors (age 65+) Persons with Disabilities* Persons with Medicare Cards* (Cash or Tap2Ride **) *Identification, proof of age, a Medicare card, or MTD photo ID is required. **Before you Tap2Ride, register your contactless bank card via Cal-ITP Benefits to receive a discounted fare.
  FREE Children under 45 inches tall (three children per adult) Transfers (to complete a one-way trip) For more information on Transfers, click here .
  $6.00 Day Pass (purchased in advance or pay-as-you-go via Tap2Ride )
  $2.50 Token for one-way fare
- **[sbmtd-farechange#7]** _Frequently Asked Questions_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). In FY 2024, operating costs were nearly $3.0 million more than in FY 2019. Cost reductions would most certainly result in service reductions. Fares have not kept pace with this growth in expenses, and in fact the last time MTD adjusted fares was January 1, 2009. While our services are primarily government-funded, fares make up roughly 20% of our annual operating budget & with the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Higher fares will help us keep up with rising operational costs without cutting or reducing any of MTD’s current service. Scaling back our services could involve reducing bus operating hours, frequency, and, in some instances, eliminating routes altogether.
  MTD fares have remained unchanged since 2009.
  The table above shows the update to fare structure from the current fare structure. MTD adjusted the base fare as of August 18, 2025 from $1.75 to $2.50, consistent with the increased expense of operating services.
  MTD continues to provide reduced fares to eligible groups, including individuals with disabilities, seniors and youth. For a complete overview of the new discounted fare prices, please review the table above.
  To see if you qualify for MTD’s reduced fare, click here .
  Yes, UCSB & SBCC students may use their valid student ID as a bus pass by tapping it on the farebox upon boarding. UCSB staff & faculty ride Line 28 free and SBCC staff & faculty ride free by showing their employee ID.
  To learn more about these programs, UCSB students, staff, & faculty click here and SBCC students, staff, & faculty click here .
  MTD has agreements with University of California, Santa Barbara and Santa Barbara City College whereby a portion of the students’ registration fees are provided to MTD. In exchange, the students can ride MTD buses for no additional charge.
  MTD is currently offering customers the opportunity to exchange unused & partially used magnetic-stripe passes through September 30, 2025, to go towards the cost of a new pass.
  You have several options for paying your fares. To learn more about these forms of fare payments please click here .
  Speed : Boarding the bus becomes quicker with pre-paid fares. This can reduce wait times at bus stops, prevent delays, and speed up your journey.
  Convenience : With pre-paid fares, you always have your fare ready to go. Simply board without worrying about having cash on hand.
  Savings : Pre-paid bus passes offer savings through fare capping and discounts compared to buying individual tickets.
  Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet.
  You can find additional information on MTD’s fare capping & Tap2Ride here .
  Eligible Passes
  Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here
  Remaining Money
  When a passenger is $1 over the dollar value of pass activations needed to be fare capped, the passenger will be refunded the $1 back to the payment method used and the refund will show on the agency transfer reports as “fare capped.”
  To learn more about MTD’s fare capping click here .
  With fare capping, there is no upfront cost of a pass, just pay as you go and still enjoy the benefit of a multi-ride discount once you have reached the cap.
- **[sbmtd-fares-passes#2]** _PASSES_

  10-RIDE PASS Discounted
  $17.50 Adult
  $12.50 Youth (K-12th grade)
  $8.50 Senior (65+)* Mobility (For Disabled Persons and Medicare Card Holders)* Apply here for persons with disabilities.
  *Identification, proof of age, a Medicare card, or MTD photo ID is required. MTD can accept cash, a local imprinted check, or a money order (payable to “MTD”) as payment for passes.
  30-DAY PASS Discounted
  $80.00 Adult (purchased in advance or pay-as-you-go via Tap2Ride )
  $65.00 Youth (K-12th grade)
  $30.00 Senior (65+)* Mobility (For Disabled Persons and Medicare Card Holders)* (purchased in advance or via Tap2Ride**) * Apply here for a physical pass for persons with disabilities. **Before you Tap2Ride, register your contactless bank card via Cal-ITP Benefits to receive a discounted fare.
  1-DAY PASS
  Systemwide Day Pass $6.00 (purchased in advance or pay-as-you-go via Tap2Ride) Good on all routes. Valid for unlimited trips in one calendar day.
  30-Day Passes are valid for 30 days from first use. 30-Day passes are not transferable and transfers are not available with this pass.
  10-Ride Passes do not expire.
  Improper use of passes may result in revocation or confiscation.
  All pass sales are final. No refunds. Passes have no cash value.
- **[sbmtd-farechange#2]** _Why the Change?_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). Fares have not kept pace with this growth in expenses.
  Budget Scenario Operating costs have increased by more than 10% since FY 2019. The average fare revenue collected per passenger has dropped from $1.11 to $1.07, still rebounding from the COVID pandemic. Fare revenue only accounts for 20% of MTD’s annual operating budget. The inflation rate for the fiscal year ending June 30, 2024 was 3.2%, indicating a trend for additional increases.
  The major revenue sources for the District operations are fares, Federal Transit Administration (FTA) operating assistance, and sales tax. With the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Federal operating assistance has grown a little; however, the apportionment of funds is fixed and there doesn’t appear to be much of a change looking forward. Sales tax revenues experienced growth with inflationary price increases during the COVID pandemic; however, recently these increases have been leveling out.
  Revenue shortfalls and increasing operating expenditures in the areas of wages and benefits, fuel, insurance, and utilities are expected, continuing to increase the deficit. Without a fare adjustment, the District would be forced to reduce service.
- **[sbmtd-fares-passes#6]** _Pacific Surfliner’s Transit Transfer Program_

  On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus or shuttle and ride MTD for free! Visit Pacific Surfliner for more info.
- **[sbmtd-fares-passes#5]** _TRANSFERS_

  If you need to use more than one bus to reach your destination, ask the driver for a transfer when you pay your fare on the first bus.
  Transfers are free and can be used as your fare on up to two more buses. The transfer is valid for up to 60 minutes on the second bus. In the rare case that you need to transfer to a third bus to reach your destination, ask the driver on your second bus to give you another transfer. This transfer will be valid for 30 minutes.
  Transfers are valid to complete your one-way trip. Transfers are not valid for return trips. Passengers who use MTD’s 30 day passes will not be issued transfers. Use your pass on each bus in any direction.
  It is MTD policy that drivers may wait up to 3 minutes beyond their scheduled departure times for transferring passengers. If you are concerned about catching your connecting bus, request that your driver call ahead to ask your next driver to wait for you.
  During peak-hour times when there is more frequent bus service, Lines 1, 2, 6 & 11 will not wait. There will be another bus in 10 minutes.
  MTD transfers are not valid on the Downtown and Waterfront Shuttles.
  Other Transfers
  With a valid transfer slip from VCTC, you pay only $1.00 to ride MTD!

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 2. `edge-018` · suite: edge_cases · lang: en
<sub>answer_sha256: `7de473b6b0235dba365d235aa54641ca0f5475e670a8c1f214f9615bb65dfb0e`</sub>

**Question:** What happens if I lose my SBMTD reduced fare photo ID card?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> If your SBMTD reduced fare photo ID card is lost, a replacement fee of $5.00 will be charged. [doc:sbmtd-fares-passes]
> 
> To obtain a replacement card, you can get an application at the MTD Transit Center or call 805.963.3366 for more information. [doc:sbmtd-fares-passes]

**Retrieved passages:**

- **[sbmtd-fares-passes#3]** _Mobility Pass: Reduced Fare and Medicare ID Cards_

  Passengers with disabilities may be eligible for the Mobility Pass, a reduced fare to ride MTD buses.
  To obtain a reduced fare MTD photo ID card, passengers must have an application completed by a qualified physician or an MTD-certified social service agency. You may download an application here , or get one at the MTD Transit Center, or call 805.963.3366 for more information.
  Passengers may also present a disabled photo ID card from another transit agency, a valid Medicare ID card, or a valid California DMV Disabled Placard Receipt to obtain a reduced fare MTD photo ID card.
  If the photo ID card is lost, a replacement fee of $5.00 will be charged.
  UCSB & SBCC STUDENTS RIDE FREE
  UCSB & SBCC students may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
- **[sbmtd-fares-passes#1]** _FARES_

  All fares are one-way and may be paid with coins, bills, passes, or via Tap2Ride with a contactless bank card or mobile wallet. Exact change required when paying with cash; fareboxes do not give change and drivers cannot make change.
  You can take advantage of pay-as-you-go discounted daily and monthly pass rates when you Tap2Ride.
  Click here to learn more, including how to get a contactless bank card or to add a bank card to the mobile wallet on your smart device.
  Standard
  $2.50 Regular one-way Youth (K-12th grade)
  $1.25 Seniors (age 65+) Persons with Disabilities* Persons with Medicare Cards* (Cash or Tap2Ride **) *Identification, proof of age, a Medicare card, or MTD photo ID is required. **Before you Tap2Ride, register your contactless bank card via Cal-ITP Benefits to receive a discounted fare.
  FREE Children under 45 inches tall (three children per adult) Transfers (to complete a one-way trip) For more information on Transfers, click here .
  $6.00 Day Pass (purchased in advance or pay-as-you-go via Tap2Ride )
  $2.50 Token for one-way fare
- **[sbmtd-farechange#1]** _Here’s What You Need to Know!_

  Santa Barbara Metropolitan Transit District (MTD) is committed to providing reliable and affordable transit services to our community. While MTD’s fares have not changed in 16 years, cost pressures have come to the point of requiring a change in fare structure. We have carefully reviewed our fare system, received community feedback, and conducted a fare equity analysis. After careful consideration we adjusted our base fare from the previous fare of $1.75 to $2.50, consistent with the increased expense of operating services.
  Implementation of this new fare structure is a significant step toward assuring MTD’s services are dependable and economically sustainable in the long term.
- **[sbmtd-farechange#3]** _What’s Changing?_

  The table below shows the update to fare structure from the current fare structure. MTD’s goal with this fare adjustment is to restore a portion of the approximately $3.0 million in annual FTA Small Transit Intensive Cities operating assistance that was lost when the 2020 Census found that the population of the Santa Barbara urbanized area exceeds 200,000 persons. The fare adjustment is estimated to generate approximately $1.5 million in increased revenue annually.
- **[sbmtd-farechange#7]** _Frequently Asked Questions_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). In FY 2024, operating costs were nearly $3.0 million more than in FY 2019. Cost reductions would most certainly result in service reductions. Fares have not kept pace with this growth in expenses, and in fact the last time MTD adjusted fares was January 1, 2009. While our services are primarily government-funded, fares make up roughly 20% of our annual operating budget & with the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Higher fares will help us keep up with rising operational costs without cutting or reducing any of MTD’s current service. Scaling back our services could involve reducing bus operating hours, frequency, and, in some instances, eliminating routes altogether.
  MTD fares have remained unchanged since 2009.
  The table above shows the update to fare structure from the current fare structure. MTD adjusted the base fare as of August 18, 2025 from $1.75 to $2.50, consistent with the increased expense of operating services.
  MTD continues to provide reduced fares to eligible groups, including individuals with disabilities, seniors and youth. For a complete overview of the new discounted fare prices, please review the table above.
  To see if you qualify for MTD’s reduced fare, click here .
  Yes, UCSB & SBCC students may use their valid student ID as a bus pass by tapping it on the farebox upon boarding. UCSB staff & faculty ride Line 28 free and SBCC staff & faculty ride free by showing their employee ID.
  To learn more about these programs, UCSB students, staff, & faculty click here and SBCC students, staff, & faculty click here .
  MTD has agreements with University of California, Santa Barbara and Santa Barbara City College whereby a portion of the students’ registration fees are provided to MTD. In exchange, the students can ride MTD buses for no additional charge.
  MTD is currently offering customers the opportunity to exchange unused & partially used magnetic-stripe passes through September 30, 2025, to go towards the cost of a new pass.
  You have several options for paying your fares. To learn more about these forms of fare payments please click here .
  Speed : Boarding the bus becomes quicker with pre-paid fares. This can reduce wait times at bus stops, prevent delays, and speed up your journey.
  Convenience : With pre-paid fares, you always have your fare ready to go. Simply board without worrying about having cash on hand.
  Savings : Pre-paid bus passes offer savings through fare capping and discounts compared to buying individual tickets.
  Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet.
  You can find additional information on MTD’s fare capping & Tap2Ride here .
  Eligible Passes
  Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here
  Remaining Money
  When a passenger is $1 over the dollar value of pass activations needed to be fare capped, the passenger will be refunded the $1 back to the payment method used and the refund will show on the agency transfer reports as “fare capped.”
  To learn more about MTD’s fare capping click here .
  With fare capping, there is no upfront cost of a pass, just pay as you go and still enjoy the benefit of a multi-ride discount once you have reached the cap.
- **[sbmtd-fares-passes#2]** _PASSES_

  10-RIDE PASS Discounted
  $17.50 Adult
  $12.50 Youth (K-12th grade)
  $8.50 Senior (65+)* Mobility (For Disabled Persons and Medicare Card Holders)* Apply here for persons with disabilities.
  *Identification, proof of age, a Medicare card, or MTD photo ID is required. MTD can accept cash, a local imprinted check, or a money order (payable to “MTD”) as payment for passes.
  30-DAY PASS Discounted
  $80.00 Adult (purchased in advance or pay-as-you-go via Tap2Ride )
  $65.00 Youth (K-12th grade)
  $30.00 Senior (65+)* Mobility (For Disabled Persons and Medicare Card Holders)* (purchased in advance or via Tap2Ride**) * Apply here for a physical pass for persons with disabilities. **Before you Tap2Ride, register your contactless bank card via Cal-ITP Benefits to receive a discounted fare.
  1-DAY PASS
  Systemwide Day Pass $6.00 (purchased in advance or pay-as-you-go via Tap2Ride) Good on all routes. Valid for unlimited trips in one calendar day.
  30-Day Passes are valid for 30 days from first use. 30-Day passes are not transferable and transfers are not available with this pass.
  10-Ride Passes do not expire.
  Improper use of passes may result in revocation or confiscation.
  All pass sales are final. No refunds. Passes have no cash value.
- **[sbmtd-farechange#5]** _Ways MTD is Working to Keep Transit Affordable_

  UCSB and SBCC Student Bus Pass Programs : All UCSB & SBCC students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
  Discounted Fare Programs: Individuals with disabilities, seniors and youth are all eligible for reduced fares. Discounts are also available for pre-paid passes including Day passes, 10 – Ride passes, and 30 – Day passes.
  Fare Capping: Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet. You can find additional information on MTD’s fare capping & Tap2Ride here .
  Free Transfers: If you need to use more than one bus to reach your destination, ask the driver for a transfer when you pay your fare on the first bus. Transfers are free and can be used as your fare on up to two more buses. Free transfers work as follows: upon request, riders can receive a free 60 minute transfer to be used to board a second bus, heading in the same direction (i.e., an outbound Line 20 to an outbound Line 6). Riders may also receive a transfer on that second bus that is good for 30 minutes, also headed in the same direction. You can find more information on free transfers here .
  Other Transfers: With a valid transfer slip from VCTC, you pay only $1.00 to ride MTD!
  Pacific Surfliner’s Transit Transfer Program: On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus and ride MTD for free! Visit Pacific Surfliner for more info.
  Alternative Funding Sources: MTD is actively seeking new funding sources. MTD continues to apply to grant programs to operate and improve service, maintain and replace vehicles, and enhance transit facilities throughout the district.
- **[sbmtd-fares-passes#4]** _Valid Dates_

  UCSB – First day of the quarter until 2 weeks into the next quarter. SBCC – 7 calendar days before the first day of classes until the last day of final exams, except Fall semester stickers are valid until 7 calendar days before Spring classes begin.
  UCSB & SBCC cards are not valid on the Downtown-Waterfront Shuttle.
  2025-2026 Valid Dates for Student Registration Stickers as Prepaid Bus Fare
  SBCC
  Fall | Aug 18 – Jan 19
  Spring | Jan 19 – May 23
  Summer | May 25 – Aug 8
  UCSB
  Fall | Sep 21 – Jan 15
  Winter | Jan 5 – Apr 10
  Spring | Mar 30 – July 3
  Summer | Jun 22 – Oct 2

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 3. `ground-024` · suite: groundedness · lang: en
<sub>answer_sha256: `b3310aa5a9b5a7308600efceaf8e017d00758086c15eda35f8bfe82b3bc7d43b`</sub>

**Question:** How much does a BeeLine on-demand ride in Woodland cost?

**Assistant answer:**

> Based on policies published as of 2026-06-17:
> 
> A BeeLine on-demand ride in Woodland costs **$2.00 for a regular rider** or **$1.50 for seniors (62+), disabled riders, or youth (0-18)**. [doc:yolobus-fares]

**Retrieved passages:**

- **[yolobus-fares#2]** _BeeLine On-Demand Transit Fares_

  BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
  Regular | Seniors (62+)/Disabled & Youth (0-18)
  Knights Landing | $2.00 | $1.50
  Winters | $2.00 | $1.50
  Woodland | $3.00 | $1.50
- **[hta-fares#2]** _Regional Transit Passes_

  Single Ride, Day, Week, and Month passes are accepted on all Ride Humboldt Fixed Route Systems. They are not accepted on any Ride Humboldt Demand Response Systems.
  Stored Value passes ($10 or $20) are accepted on all Ride Humboldt Fixed Route Systems, and on Dial-A-Ride. They are not accepted on Flex microtransit.
  Ticket Exclusion details
- **[sbmtd-farechange#7]** _Frequently Asked Questions_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). In FY 2024, operating costs were nearly $3.0 million more than in FY 2019. Cost reductions would most certainly result in service reductions. Fares have not kept pace with this growth in expenses, and in fact the last time MTD adjusted fares was January 1, 2009. While our services are primarily government-funded, fares make up roughly 20% of our annual operating budget & with the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Higher fares will help us keep up with rising operational costs without cutting or reducing any of MTD’s current service. Scaling back our services could involve reducing bus operating hours, frequency, and, in some instances, eliminating routes altogether.
  MTD fares have remained unchanged since 2009.
  The table above shows the update to fare structure from the current fare structure. MTD adjusted the base fare as of August 18, 2025 from $1.75 to $2.50, consistent with the increased expense of operating services.
  MTD continues to provide reduced fares to eligible groups, including individuals with disabilities, seniors and youth. For a complete overview of the new discounted fare prices, please review the table above.
  To see if you qualify for MTD’s reduced fare, click here .
  Yes, UCSB & SBCC students may use their valid student ID as a bus pass by tapping it on the farebox upon boarding. UCSB staff & faculty ride Line 28 free and SBCC staff & faculty ride free by showing their employee ID.
  To learn more about these programs, UCSB students, staff, & faculty click here and SBCC students, staff, & faculty click here .
  MTD has agreements with University of California, Santa Barbara and Santa Barbara City College whereby a portion of the students’ registration fees are provided to MTD. In exchange, the students can ride MTD buses for no additional charge.
  MTD is currently offering customers the opportunity to exchange unused & partially used magnetic-stripe passes through September 30, 2025, to go towards the cost of a new pass.
  You have several options for paying your fares. To learn more about these forms of fare payments please click here .
  Speed : Boarding the bus becomes quicker with pre-paid fares. This can reduce wait times at bus stops, prevent delays, and speed up your journey.
  Convenience : With pre-paid fares, you always have your fare ready to go. Simply board without worrying about having cash on hand.
  Savings : Pre-paid bus passes offer savings through fare capping and discounts compared to buying individual tickets.
  Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet.
  You can find additional information on MTD’s fare capping & Tap2Ride here .
  Eligible Passes
  Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here
  Remaining Money
  When a passenger is $1 over the dollar value of pass activations needed to be fare capped, the passenger will be refunded the $1 back to the payment method used and the refund will show on the agency transfer reports as “fare capped.”
  To learn more about MTD’s fare capping click here .
  With fare capping, there is no upfront cost of a pass, just pay as you go and still enjoy the benefit of a multi-ride discount once you have reached the cap.
- **[sbmtd-farechange#2]** _Why the Change?_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). Fares have not kept pace with this growth in expenses.
  Budget Scenario Operating costs have increased by more than 10% since FY 2019. The average fare revenue collected per passenger has dropped from $1.11 to $1.07, still rebounding from the COVID pandemic. Fare revenue only accounts for 20% of MTD’s annual operating budget. The inflation rate for the fiscal year ending June 30, 2024 was 3.2%, indicating a trend for additional increases.
  The major revenue sources for the District operations are fares, Federal Transit Administration (FTA) operating assistance, and sales tax. With the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Federal operating assistance has grown a little; however, the apportionment of funds is fixed and there doesn’t appear to be much of a change looking forward. Sales tax revenues experienced growth with inflationary price increases during the COVID pandemic; however, recently these increases have been leveling out.
  Revenue shortfalls and increasing operating expenditures in the areas of wages and benefits, fuel, insurance, and utilities are expected, continuing to increase the deficit. Without a fare adjustment, the District would be forced to reduce service.
- **[hta-fares#3]** _Ride Humboldt Systems_

  The Ride Humboldt Fixed Route Systems are:
  Redwood Transit Service (RTS)
  Eureka Transit Service (ETS)
  North State Express: Route 299: (NSE 299) [This system was formerly called Willow Creek (WC)
  Southern Humboldt (SH)
  North State Express: Route 101 (NSE 101) [This system was formerly called Redwood Coast Express]
  Arcata & Mad River Transit System (A&MRTS)
  The Ride Humboldt Demand Response Systems are:
  Dial-A-Ride
  Flex microtransit
  Ticket Exclusions may apply.
  Look for the Ride Humboldt logo on the bus:
- **[sbmtd-farechange#6]** _Benefits of Pre-Paid Fares_

  Efficiency and Faster Boarding: Pre-paid fare systems allow passengers to pay before boarding, reducing the time buses need to spend at bus stops and instead getting you where you need to go.
  Improved Passenger Flow: Pre-paid systems contribute to smoother passenger flow on and off the bus, reducing crowding and making the overall transit experience more pleasant for everyone.
  Cost Savings: Handling cash transactions can be resource intensive. Moving to a contactless payment system or pre-paid system can reduce the costs associated with cash handling services.
  Fare Capping: Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here .
- **[yolobus-purchasing#1]** _Connect Card_

  Connect Card is the Sacramento region’s electronic transit fare payment system and is accepted by most transit agencies in the Sacramento region. Connect Card readers are installed on all Yolobus and BeeLine vehicles.
  To pay, tag the Connect Card reader with your card upon boarding the bus.
- **[yolobus-fares#1]** _Youth ages 0-18 ride free!_

  Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
  Single Ride Tickets
  Local Fare | $2.00 | $1.00
  Intercity Fare | $2.25 | $1.00
  Express | $3.25 | $1.50
  Express Upgrade | $1.00 | N/A†
  Monthly Passes
  Local Routes | $84.00 | $42.00+
  Intercity Routes | $93.50 | $42.00+
  Express Routes | $121.00 | $42.00+
  Transfers
  Between Yolobus (Non-Express) | $0.25 | $0.25
  Between Yolobus (Express) | $1.00 | $0.50
  Yolobus + SacRT Shared Passes
  Daily Pass SacRT + Yolobus | $7.00 | $3.50
  Monthly SacRT + Yolobus | $100.00 | $50.00
  * To meet senior/disabled criteria, must possess one of the following as valid proof of identification upon boarding Yolobus: SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver’s License or government-issued ID Card; Any other discount ID issued by another transit agency † Senior/disabled monthly pass works on express routes already, does not require upgrade) ‡ Senior/Disabled monthly pass is valid on all routes, including local, intercity and express routes

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 4. `ground-026` · suite: groundedness · lang: en
<sub>answer_sha256: `a5078fc44e3df78c88a52308dbe49b27f6cf7b093cc2e138b62acea1728a6fc5`</sub>

**Question:** How much is a SacRT monthly pass for a student in grades TK-12?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> Students in grades TK–12 may qualify for **fare-free transit** through the RydeFreeRT program. [doc:sacrt-fares] The published policy does not specify a monthly pass price for this group because the program offers free passes rather than a discounted paid fare.
> 
> To find out whether you qualify and how to apply, check with your school district or visit RydeFreeRT. [doc:sacrt-fares]
> 
> If you have questions about eligibility or enrollment, contact SacRT directly through their website at sacrt.com or call their main line for details on the RydeFreeRT program.

**Retrieved passages:**

- **[sacrt-fares#2]** _Students (TK - 12) - Discount**_

  Single Ride Ticket
  $1.25
  Transfer Ticket
  $0.25
  Daily Pass
  $3.50
  Semi-Monthly Pass/Sticker*
  $10.00
  Monthly Pass/Sticker*
  $20.00
  *Discount (senior, disabled or student) monthly or semi-monthly stickers must be affixed to a valid SacRT photo ID, with the exception of the student stickers, which may be attached to a school issued photo ID.
  **Many students in grades TK through 12, are eligible for free transit passes. Check with your school district for details or visit RydeFreeRT.
  Bus
  Passengers are required to pay a basic or discount single fare for each trip or may purchase a daily pass valid for unlimited rides on that day.
  Light Rail
  Single ride tickets are valid for 90 minutes from time of validation on light rail only, or pay the 25 cent transfer fee when boarding a bus.
  Fare Accepted by Other Transit Agencies
  Your SacRT ticket or pass maybe eligible to transfer to and from other transit agencies. For more detailed fare info or a list of SacRT fares accepted by other transit agencies, click here .
- **[sacrt-fares#6]** _Student Passes_

  SacRT offers fare-free transit for students in Transitional Kindergarten through 12th grade with the RydeFreeRT program and has a partnership with Los Rios and Sacramento State for college students to ride SacRT.
  California State Workers
  SEIU members have access to free transit through SacRT! Other state employee unions are eligible for transit subsidies to cover most of your commute costs.
- **[sacrt-fares#1]** _Single, Daily and Monthly Passes_

  All fares are subject to change.
  Exact change required only on bus; operators do not carry cash. Light rail station fare vending machines accept both cash/credit or debit. Pre-paid tickets and passes must be validated at the fare vending machines before boarding.
  Monthly passes cover the full month, and semi-monthly passes can be purchased for the 1st – 15th or 16th – end of the month.
  All passengers must pay basic fare unless eligible for discount fare or free fare. View the fare categories page to see if you qualify for senior, disabled or student fare.
  Age 19-61 - Basic
  Single Ride Ticket
  $2.50
  Transfer Ticket
  $0.25
  Daily Pass
  $7.00
  Semi-Monthly Pass
  $50.00
  Monthly Pass
  $100.00
  Senior (age 62+) - Discount
  Single
  $1.25
  Transfer Ticket
  $0.25
  Daily Pass
  $3.50
  Semi-Monthly Pass/Sticker*
  $25.00
  Monthly Pass/Sticker*
  $50.00
  Super Senior Monthly Pass/Sticker (age 75+)*
  $40.00
  Person with Disabilities - Discount
  Single
  $1.25
  Transfer Ticket
  $0.25
  Daily Pass
  $3.50
  Semi-Monthly Pass/Sticker*
  $25.00
  Monthly Pass/Sticker*
  $50.00
- **[sacrt-fares#5]** _ZipPass App_

  Our mobile fare app ZipPass allows you to pre-purchase, store and activate SacRT tickets and passes instantly on your smartphone for both bus or light rail. Simply download the app from either the App Store or Google Play to get started.
  Ride buses and light rail trains for 90 minutes using a valid single ride ticket
  State of the art security to protect personal information
  Purchase single ride tickets, daily or monthly passes
  Need a ticket for your next SacRT Light Rail trip?
  Our fare vending machines located at all SacRT light rail stations are simple to use!
  1. Choose your ticket or pass
  2. Pick your quantity
  3. Complete your payment (cash or card)
  4. Take your ticket
  Tap2Ride
  Tap your contactless card, mobile device or mobile wallet on Tap2Ride devices on bus and SacRT GO vehicles to pay fare.
- **[sacrt-fares#4]** _Transit Connect App_

  Our mobile fare app Transit Connect allows you to pre-purchase, store and activate SacRT tickets and passes instantly on your smartphone for both bus and light rail. Simply download the app from either the App Store or Google Play to get started.
  Ride buses and light rail trains for 90 minutes using a valid single ride ticket
  State of the art security to protect personal information
  Purchase single ride tickets, daily or monthly passes
  Learn more at sacrt.com/TransitConnect .
- **[sacrt-fares#0]** _Fares_

  SacRT offers a variety of fare options to meet your travel needs.
  Important Notice for ZipPass Users
  Last day to use passes/tickets: April 30, 2026
- **[sacrt-fares#3]** _Connect Transit Card_

  Connect Transit Card is the region's electronic transit fare payment system. You can now purchase your transit fare online, never have to carry exact change again AND use the card on more than one agency!
  1. Go through the "get a card" process
  2. Create an account and buy your transit fare
  3. Receive an email confirming your order
  4. Your Connect Transit Card arrives in 7-10 Business Days

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 5. `sens-015a` · suite: sensitivity · lang: en
<sub>answer_sha256: `dae4a613e3dcb9c439fb9daf78765cacc1f1e5a7b3a114f0cd879acadccf18f3`</sub>

**Question:** Can I ride Yolobus with my UC Davis Aggie Card as an undergraduate?

**Assistant answer:**

> Yes, based on the published policies, you may ride Yolobus with your UC Davis Aggie Card if you meet the published criterion: **Undergraduate or UCDE Global Study Only** [doc:yolobus-fares]. The card must have a valid expiration date [doc:yolobus-fares].
> 
> The UC Davis Aggie Card is good for unlimited rides on Yolobus, including Express routes [doc:yolobus-fares].
> 
> However, I cannot determine whether you meet the "Undergraduate or UCDE Global Study Only" criterion — Yolobus makes that decision. To confirm your eligibility and use your card, contact Yolobus directly or present your card when boarding.
> 
> Based on policies published as of 2026-06-12. For the most current information, confirm with Yolobus at yolobus.com or contact the Yolobus Customer Service Center in Woodland (Monday–Thursday, excluding holidays, 9am–12pm; 1pm–4pm) [doc:yolobus-purchasing].

**Retrieved passages:**

- **[yolobus-purchasing#8]** _Other Fare Media_

  The following passes are good for unlimited rides on Yolobus, including Express routes.
  UC Davis Aggie Card | UC Davis Extension International Program ID | Sacramento State Student ID | Los Rios Student Access Card | South Natomas TMA Pass | Sacramento County DHA Pass
  Undergraduate or UCDE Global Study Only | with valid expiration date | with valid commuter sleeve | with valid sticker | with valid expiration date | with valid sticker
  UC Davis Aggie Card: Undergraduate or UCDE Global Study Only
  UC Davis Extension International Program ID: with valid expiration date
  Sacramento State Student ID: with valid commuter sleeve
  Los Rios Student Access Card: with valid sticker
  South Natomas TMA Pass: with valid expiration date
  Sacramento County DHA Pass: with valid sticker
- **[yolobus-fares#11]** _Other Fare Media_

  The following passes are good for unlimited rides on Yolobus, including Express routes.
  All fares are effective July 1, 2025 – June 30, 2026.
  UC Davis Aggie Card | UC Davis Zip Pass | UC Davis Extension International Program ID | Sacramento State Student ID | Los Rios Student Access Card | South Natomas TMA Pass | Sacramento County DHA Pass
  Undergraduate or UCDE Global Study Only | with valid student ID | with valid expiration date | with valid commuter sleeve | with valid sticker | with valid expiration date | with valid sticker
  UC Davis Aggie Card: Undergraduate or UCDE Global Study Only
  UC Davis Zip Pass: with valid student ID
  UC Davis Extension International Program ID: with valid expiration date
  Sacramento State Student ID: with valid commuter sleeve
  Los Rios Student Access Card: with valid sticker
  South Natomas TMA Pass: with valid expiration date
  Sacramento County DHA Pass: with valid sticker
- **[yolobus-fares#7]** _Cash_

  All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required as our bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at the Yolobus Customer Service Center .
  In-Person Ticket and Pass Sales
  The Yolobus Customer Service Center in Woodland offers transit fare sales and Connect Card services:
  Business Hours
  Monday through Thursday, excluding holidays 9am – 12pm; 1pm – 4pm
  Connect Card Services Offered
  Issue New or Replacement Connect Cards for all riders, including eligible Discount Riders (Seniors, Persons with Disabilities)
  Load Cash Value, Tickets and/or Passes onto Connect Cards
  Alternative ticket and pass sales location in Sacramento
  Tickets, passes and Connect Card services are also available in Sacramento at the Sacramento Regional Transit District (SacRT) Customer Service and Sales Center, 1225 R St., Sacramento (916) 321-2877
  Single Ride
  Tickets are for a one-way trip. A ticket can be purchased individually on Yolobus or by using Connect Card or Transit Connect.
- **[yolobus-purchasing#3]** _Cash_

  All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required, and bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at the Yolobus Customer Service Center.
  In-Person Ticket and Pass Sales
  The Yolobus Customer Service Center in Woodland offers transit fare sales and Connect Card services.
  Business Hours
  Monday through Thursday, excluding holidays 9am – 12pm; 1pm – 4pm
  Connect Card Services Offered
  Issue New or Replacement Connect Cards for all riders, including eligible Discount Riders (Seniors, Persons with Disabilities)
  Load Cash Value, Tickets, and/or Passes onto Connect Cards
- **[yolobus-fares#8]** _Daily Pass_

  A Daily Pass is valid for unlimited rides until 1:30am the day after purchase on Yolobus and Sacramento Regional Transportation (SacRT). A Daily Pass can be purchased individually on board a Yolobus or by using Connect Card or Transit Connect.
- **[yolobus-purchasing#5]** _Daily Pass_

  A Daily Pass is valid for unlimited rides until 1:30am the day after purchase on Yolobus and Sacramento Regional Transportation (SacRT). A Daily Pass can be purchased individually onboard a Yolobus or by using Connect Card or the Transit Connect app.
- **[yolobus-purchasing#6]** _Monthly Passes_

  Yolobus-only and Sacramento Regional Transit Monthly Passes are valid for unlimited rides for the month shown, and through the first business day of the following month. Monthly passes can be purchased on the Connect Card website or on the Transit Connect app, but not onboard a Yolobus.
- **[yolobus-fares#9]** _Monthly Passes_

  Yolobus-only and Sacramento Regional Transit Monthly Passes are valid for unlimited rides for the month shown, and through the first business day of the following month. Monthly passes can be purchased on the Connect Card website or the Transit Connect mobile app, but not onboard a Yolobus.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 6. `conv-001` · suite: conversation · lang: en
<sub>answer_sha256: `9cbf61b1bfcb9892224e93f1541e5182458891f55d9440164af5427fbab6ba95`</sub>

**Prior turn(s):** What proof do I need for the veteran fare on MST?
**Question:** Does it cover my spouse too?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> The published policy states that veterans' spouses and caregivers are listed as a discount-eligible group on MST [doc:mst-fares]. However, the passages do not specify what proof of relationship or status a spouse or caregiver must provide, how to apply for a Courtesy Card in that capacity, or any other class-specific documentation or process for that rider category.
> 
> To learn what documentation is required and how to apply, contact MST directly through their website at mst.org or call their customer service.

**Retrieved passages:**

- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[mst-veterans-resource#3]** _Veterans Transition Center_

  To end Veteran homelessness in Monterey County. Each Veteran is paired with a dedicated VTC Case Manager who designs and supervises their recovery program while addressing medical, psychological, occupational, financial, educational, and social issues. They offer several housing programs, including emergency, transitional, and permanent supportive housing.
  https://www.vtcofcalifornia.org/programsandservices
  Vocational Rehabilitation Specialist Inc
  To assist veterans in need of housing, job placement, job skill training, and more.
  https://vrspecialists.com/
- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training
- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares#5]** _GoCard_

  The GoCard is a stored value card. Passengers can add value to their card at any time by visiting an MST customer service location and use it like a debit card when riding the bus. The GoCard allows for speedy boarding. Simply tap your card on the farebox below the digital screen and the appropriate fare will be deducted. Plus passengers receive an additional 10% value each time the card is reloaded! For example, load your card with $20 and receive $22 value on your GoCard. Use the card when you need it, save the value when you don’t. $10 minimum purchase required.
- **[mst-fares#0]** _Fares Overview_

  MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day, $20 per week, or $70 per month no matter how many times you ride in a day—as long as you pay with the same contactless debit/credit card or mobile wallet each time you ride. (visit Benefits page for details about tapping and discounted fares)
  Transfers If you need to transfer and are paying with cash or a GoPass, request a 2-hour pass from your driver when boarding your first bus.
  If you are paying with a contactless payment method, simply tap it on the contactless payment reader on each bus you board and you will only be charged $2 no matter how many times you tap and transfer within that 2-hour window.
- **[mst-veterans-resource#0]** _Taxi Voucher Program_

  Monterey-Salinas Transit (MST) offers a taxi voucher program to assist veterans in accessing important locations within their communities.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 7. `conv-002` · suite: conversation · lang: en
<sub>answer_sha256: `69f6ca76b9b4f6e79df87ded87748b1f3049cc430e5ea228b5dfd3c89bed104c`</sub>

**Prior turn(s):** How much is the senior fare on Santa Barbara MTD?
**Question:** And what does a day pass cost?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> Santa Barbara MTD offers a **1-Day Pass (Systemwide Day Pass) for $6.00**. It is good on all routes and valid for unlimited trips in one calendar day. [doc:sbmtd-fares-passes]
> 
> You can purchase it in advance or pay-as-you-go via Tap2Ride. [doc:sbmtd-fares-passes]

**Retrieved passages:**

- **[sbmtd-farechange#1]** _Here’s What You Need to Know!_

  Santa Barbara Metropolitan Transit District (MTD) is committed to providing reliable and affordable transit services to our community. While MTD’s fares have not changed in 16 years, cost pressures have come to the point of requiring a change in fare structure. We have carefully reviewed our fare system, received community feedback, and conducted a fare equity analysis. After careful consideration we adjusted our base fare from the previous fare of $1.75 to $2.50, consistent with the increased expense of operating services.
  Implementation of this new fare structure is a significant step toward assuring MTD’s services are dependable and economically sustainable in the long term.
- **[sbmtd-farechange#7]** _Frequently Asked Questions_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). In FY 2024, operating costs were nearly $3.0 million more than in FY 2019. Cost reductions would most certainly result in service reductions. Fares have not kept pace with this growth in expenses, and in fact the last time MTD adjusted fares was January 1, 2009. While our services are primarily government-funded, fares make up roughly 20% of our annual operating budget & with the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Higher fares will help us keep up with rising operational costs without cutting or reducing any of MTD’s current service. Scaling back our services could involve reducing bus operating hours, frequency, and, in some instances, eliminating routes altogether.
  MTD fares have remained unchanged since 2009.
  The table above shows the update to fare structure from the current fare structure. MTD adjusted the base fare as of August 18, 2025 from $1.75 to $2.50, consistent with the increased expense of operating services.
  MTD continues to provide reduced fares to eligible groups, including individuals with disabilities, seniors and youth. For a complete overview of the new discounted fare prices, please review the table above.
  To see if you qualify for MTD’s reduced fare, click here .
  Yes, UCSB & SBCC students may use their valid student ID as a bus pass by tapping it on the farebox upon boarding. UCSB staff & faculty ride Line 28 free and SBCC staff & faculty ride free by showing their employee ID.
  To learn more about these programs, UCSB students, staff, & faculty click here and SBCC students, staff, & faculty click here .
  MTD has agreements with University of California, Santa Barbara and Santa Barbara City College whereby a portion of the students’ registration fees are provided to MTD. In exchange, the students can ride MTD buses for no additional charge.
  MTD is currently offering customers the opportunity to exchange unused & partially used magnetic-stripe passes through September 30, 2025, to go towards the cost of a new pass.
  You have several options for paying your fares. To learn more about these forms of fare payments please click here .
  Speed : Boarding the bus becomes quicker with pre-paid fares. This can reduce wait times at bus stops, prevent delays, and speed up your journey.
  Convenience : With pre-paid fares, you always have your fare ready to go. Simply board without worrying about having cash on hand.
  Savings : Pre-paid bus passes offer savings through fare capping and discounts compared to buying individual tickets.
  Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet.
  You can find additional information on MTD’s fare capping & Tap2Ride here .
  Eligible Passes
  Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here
  Remaining Money
  When a passenger is $1 over the dollar value of pass activations needed to be fare capped, the passenger will be refunded the $1 back to the payment method used and the refund will show on the agency transfer reports as “fare capped.”
  To learn more about MTD’s fare capping click here .
  With fare capping, there is no upfront cost of a pass, just pay as you go and still enjoy the benefit of a multi-ride discount once you have reached the cap.
- **[sbmtd-farechange#2]** _Why the Change?_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). Fares have not kept pace with this growth in expenses.
  Budget Scenario Operating costs have increased by more than 10% since FY 2019. The average fare revenue collected per passenger has dropped from $1.11 to $1.07, still rebounding from the COVID pandemic. Fare revenue only accounts for 20% of MTD’s annual operating budget. The inflation rate for the fiscal year ending June 30, 2024 was 3.2%, indicating a trend for additional increases.
  The major revenue sources for the District operations are fares, Federal Transit Administration (FTA) operating assistance, and sales tax. With the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Federal operating assistance has grown a little; however, the apportionment of funds is fixed and there doesn’t appear to be much of a change looking forward. Sales tax revenues experienced growth with inflationary price increases during the COVID pandemic; however, recently these increases have been leveling out.
  Revenue shortfalls and increasing operating expenditures in the areas of wages and benefits, fuel, insurance, and utilities are expected, continuing to increase the deficit. Without a fare adjustment, the District would be forced to reduce service.
- **[sbmtd-farechange#3]** _What’s Changing?_

  The table below shows the update to fare structure from the current fare structure. MTD’s goal with this fare adjustment is to restore a portion of the approximately $3.0 million in annual FTA Small Transit Intensive Cities operating assistance that was lost when the 2020 Census found that the population of the Santa Barbara urbanized area exceeds 200,000 persons. The fare adjustment is estimated to generate approximately $1.5 million in increased revenue annually.
- **[sbmtd-fares-passes#0]** _(page top)_

  New fares are now effective as of August 18, 2025. For more information click here .
  UCSB CAMPUS STORE
  1 UCEN Road Santa Barbara, CA 93106 805.893.3819
  EASTSIDE LIBRARY
  1102 East Montecito Street Santa Barbara, CA 93103 Vending Machine
  MTD TRANSIT CENTER
  1020 Chapala Street Santa Barbara, CA 93101 805. 963.3366
  MTD BUSINESS OFFICE
  550 Olive Street Santa Barbara, CA 93101 805. 963.3364 MTD accepts cash, a local imprinted check, or a money order (payable to “MTD”) as payment for passes.
  ALBERTSON’S CARPINTERIA
  1018 Casitas Pass Road Carpinteria, CA 93013 Vending Machine
  GOLETA VALLEY COMMUNITY CENTER
  5679 Hollister Avenue Goleta, CA 93117 Vending Machine -Credit/Debit Card Only
  BY MAIL
  You can order your passes through the mail by filling out & sending in an order form.
- **[sbmtd-fares-passes#2]** _PASSES_

  10-RIDE PASS Discounted
  $17.50 Adult
  $12.50 Youth (K-12th grade)
  $8.50 Senior (65+)* Mobility (For Disabled Persons and Medicare Card Holders)* Apply here for persons with disabilities.
  *Identification, proof of age, a Medicare card, or MTD photo ID is required. MTD can accept cash, a local imprinted check, or a money order (payable to “MTD”) as payment for passes.
  30-DAY PASS Discounted
  $80.00 Adult (purchased in advance or pay-as-you-go via Tap2Ride )
  $65.00 Youth (K-12th grade)
  $30.00 Senior (65+)* Mobility (For Disabled Persons and Medicare Card Holders)* (purchased in advance or via Tap2Ride**) * Apply here for a physical pass for persons with disabilities. **Before you Tap2Ride, register your contactless bank card via Cal-ITP Benefits to receive a discounted fare.
  1-DAY PASS
  Systemwide Day Pass $6.00 (purchased in advance or pay-as-you-go via Tap2Ride) Good on all routes. Valid for unlimited trips in one calendar day.
  30-Day Passes are valid for 30 days from first use. 30-Day passes are not transferable and transfers are not available with this pass.
  10-Ride Passes do not expire.
  Improper use of passes may result in revocation or confiscation.
  All pass sales are final. No refunds. Passes have no cash value.
- **[sbmtd-farechange#6]** _Benefits of Pre-Paid Fares_

  Efficiency and Faster Boarding: Pre-paid fare systems allow passengers to pay before boarding, reducing the time buses need to spend at bus stops and instead getting you where you need to go.
  Improved Passenger Flow: Pre-paid systems contribute to smoother passenger flow on and off the bus, reducing crowding and making the overall transit experience more pleasant for everyone.
  Cost Savings: Handling cash transactions can be resource intensive. Moving to a contactless payment system or pre-paid system can reduce the costs associated with cash handling services.
  Fare Capping: Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here .
- **[sbmtd-farechange#5]** _Ways MTD is Working to Keep Transit Affordable_

  UCSB and SBCC Student Bus Pass Programs : All UCSB & SBCC students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
  Discounted Fare Programs: Individuals with disabilities, seniors and youth are all eligible for reduced fares. Discounts are also available for pre-paid passes including Day passes, 10 – Ride passes, and 30 – Day passes.
  Fare Capping: Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet. You can find additional information on MTD’s fare capping & Tap2Ride here .
  Free Transfers: If you need to use more than one bus to reach your destination, ask the driver for a transfer when you pay your fare on the first bus. Transfers are free and can be used as your fare on up to two more buses. Free transfers work as follows: upon request, riders can receive a free 60 minute transfer to be used to board a second bus, heading in the same direction (i.e., an outbound Line 20 to an outbound Line 6). Riders may also receive a transfer on that second bus that is good for 30 minutes, also headed in the same direction. You can find more information on free transfers here .
  Other Transfers: With a valid transfer slip from VCTC, you pay only $1.00 to ride MTD!
  Pacific Surfliner’s Transit Transfer Program: On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus and ride MTD for free! Visit Pacific Surfliner for more info.
  Alternative Funding Sources: MTD is actively seeking new funding sources. MTD continues to apply to grant programs to operate and improve service, maintain and replace vehicles, and enhance transit facilities throughout the district.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 8. `xagency-001` · suite: cross_agency · lang: en
<sub>answer_sha256: `102a766e07531da0f762ab13dad6be86c1ea2661ae73e67f863fb8ad0ce81d94`</sub>

**Question:** Do youth ride free on both Yolobus and SacRT?

**Assistant answer:**

> Based on the passages provided, here is what the published policies state:
> 
> **Yolobus:** Youth ages 0–18 ride free. [doc:yolobus-fares]
> 
> **SacRT:** The passages do not state whether youth ride free on SacRT. The published policy describes fare-free programs for students in Transitional Kindergarten through 12th grade (RydeFreeRT) and college students through partnerships, [doc:sacrt-fares] but does not specify an age-based free-ride policy for youth ages 0–18 as a category.
> 
> To confirm SacRT's youth fare policy, contact Sacramento Regional Transit directly or visit sacrt.com.
> 
> Based on policies published as of 2026-06-12.

**Retrieved passages:**

- **[yolobus-fares#1]** _Youth ages 0-18 ride free!_

  Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
  Single Ride Tickets
  Local Fare | $2.00 | $1.00
  Intercity Fare | $2.25 | $1.00
  Express | $3.25 | $1.50
  Express Upgrade | $1.00 | N/A†
  Monthly Passes
  Local Routes | $84.00 | $42.00+
  Intercity Routes | $93.50 | $42.00+
  Express Routes | $121.00 | $42.00+
  Transfers
  Between Yolobus (Non-Express) | $0.25 | $0.25
  Between Yolobus (Express) | $1.00 | $0.50
  Yolobus + SacRT Shared Passes
  Daily Pass SacRT + Yolobus | $7.00 | $3.50
  Monthly SacRT + Yolobus | $100.00 | $50.00
  * To meet senior/disabled criteria, must possess one of the following as valid proof of identification upon boarding Yolobus: SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver’s License or government-issued ID Card; Any other discount ID issued by another transit agency † Senior/disabled monthly pass works on express routes already, does not require upgrade) ‡ Senior/Disabled monthly pass is valid on all routes, including local, intercity and express routes
- **[sacrt-fares#4]** _Transit Connect App_

  Our mobile fare app Transit Connect allows you to pre-purchase, store and activate SacRT tickets and passes instantly on your smartphone for both bus and light rail. Simply download the app from either the App Store or Google Play to get started.
  Ride buses and light rail trains for 90 minutes using a valid single ride ticket
  State of the art security to protect personal information
  Purchase single ride tickets, daily or monthly passes
  Learn more at sacrt.com/TransitConnect .
- **[yolobus-fares#8]** _Daily Pass_

  A Daily Pass is valid for unlimited rides until 1:30am the day after purchase on Yolobus and Sacramento Regional Transportation (SacRT). A Daily Pass can be purchased individually on board a Yolobus or by using Connect Card or Transit Connect.
- **[sacrt-fares#5]** _ZipPass App_

  Our mobile fare app ZipPass allows you to pre-purchase, store and activate SacRT tickets and passes instantly on your smartphone for both bus or light rail. Simply download the app from either the App Store or Google Play to get started.
  Ride buses and light rail trains for 90 minutes using a valid single ride ticket
  State of the art security to protect personal information
  Purchase single ride tickets, daily or monthly passes
  Need a ticket for your next SacRT Light Rail trip?
  Our fare vending machines located at all SacRT light rail stations are simple to use!
  1. Choose your ticket or pass
  2. Pick your quantity
  3. Complete your payment (cash or card)
  4. Take your ticket
  Tap2Ride
  Tap your contactless card, mobile device or mobile wallet on Tap2Ride devices on bus and SacRT GO vehicles to pay fare.
- **[yolobus-purchasing#5]** _Daily Pass_

  A Daily Pass is valid for unlimited rides until 1:30am the day after purchase on Yolobus and Sacramento Regional Transportation (SacRT). A Daily Pass can be purchased individually onboard a Yolobus or by using Connect Card or the Transit Connect app.
- **[sacrt-fares#6]** _Student Passes_

  SacRT offers fare-free transit for students in Transitional Kindergarten through 12th grade with the RydeFreeRT program and has a partnership with Los Rios and Sacramento State for college students to ride SacRT.
  California State Workers
  SEIU members have access to free transit through SacRT! Other state employee unions are eligible for transit subsidies to cover most of your commute costs.
- **[yolobus-purchasing#2]** _Mobile / Transit Connect_

  The Transit Connect mobile fare app allows you to pre-purchase, store and activate tickets and passes instantly on your smartphone. Accepted on Yolobus and SacRT, with Unitrans joining in summer 2026. Download the free app from the App Store or Google Play.
- **[sacrt-fares#2]** _Students (TK - 12) - Discount**_

  Single Ride Ticket
  $1.25
  Transfer Ticket
  $0.25
  Daily Pass
  $3.50
  Semi-Monthly Pass/Sticker*
  $10.00
  Monthly Pass/Sticker*
  $20.00
  *Discount (senior, disabled or student) monthly or semi-monthly stickers must be affixed to a valid SacRT photo ID, with the exception of the student stickers, which may be attached to a school issued photo ID.
  **Many students in grades TK through 12, are eligible for free transit passes. Check with your school district for details or visit RydeFreeRT.
  Bus
  Passengers are required to pay a basic or discount single fare for each trip or may purchase a daily pass valid for unlimited rides on that day.
  Light Rail
  Single ride tickets are valid for 90 minutes from time of validation on light rail only, or pay the 25 cent transfer fee when boarding a bus.
  Fare Accepted by Other Transit Agencies
  Your SacRT ticket or pass maybe eligible to transfer to and from other transit agencies. For more detailed fare info or a list of SacRT fares accepted by other transit agencies, click here .
- **[yolobus-reduced-fare-id#0]** _(page top)_

  Our senior (62+) and disabled riders can take advantage of Yolobus’ reduced fares. To qualify for reduced fares, riders must show proper identification when purchasing fares and to bus operators when boarding.
  Qualifying riders may obtain a reduced fare photo ID by visiting the Yolo Transportation District office or SacRT Customer Service & Sales Center:
  Yolo Transportation District
  350 Industrial Way, Woodland, CA 95776 (Mon-Thu, 9:00 AM-Noon, 1:00-4:00 PM)
  (530) 661-0816

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 9. `xagency-002` · suite: cross_agency · lang: en
<sub>answer_sha256: `dd99cb511585cfe64380fe09860e2930575b8fa2de0f3a2ab19c74abc59d85fe`</sub>

**Question:** Is the senior discount age the same on MST and Yolobus?

**Assistant answer:**

> No, the senior discount ages differ between the two agencies.
> 
> **MST** offers a discount fare for **65 years and older** [doc:mst-fares].
> 
> **Yolobus** offers reduced fares for **62+** [doc:yolobus-fares], [doc:yolobus-reduced-fare-id].
> 
> Based on policies published as of 2026-06-12.

**Retrieved passages:**

- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[yolobus-fares#1]** _Youth ages 0-18 ride free!_

  Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
  Single Ride Tickets
  Local Fare | $2.00 | $1.00
  Intercity Fare | $2.25 | $1.00
  Express | $3.25 | $1.50
  Express Upgrade | $1.00 | N/A†
  Monthly Passes
  Local Routes | $84.00 | $42.00+
  Intercity Routes | $93.50 | $42.00+
  Express Routes | $121.00 | $42.00+
  Transfers
  Between Yolobus (Non-Express) | $0.25 | $0.25
  Between Yolobus (Express) | $1.00 | $0.50
  Yolobus + SacRT Shared Passes
  Daily Pass SacRT + Yolobus | $7.00 | $3.50
  Monthly SacRT + Yolobus | $100.00 | $50.00
  * To meet senior/disabled criteria, must possess one of the following as valid proof of identification upon boarding Yolobus: SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver’s License or government-issued ID Card; Any other discount ID issued by another transit agency † Senior/disabled monthly pass works on express routes already, does not require upgrade) ‡ Senior/Disabled monthly pass is valid on all routes, including local, intercity and express routes
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[yolobus-purchasing#3]** _Cash_

  All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required, and bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at the Yolobus Customer Service Center.
  In-Person Ticket and Pass Sales
  The Yolobus Customer Service Center in Woodland offers transit fare sales and Connect Card services.
  Business Hours
  Monday through Thursday, excluding holidays 9am – 12pm; 1pm – 4pm
  Connect Card Services Offered
  Issue New or Replacement Connect Cards for all riders, including eligible Discount Riders (Seniors, Persons with Disabilities)
  Load Cash Value, Tickets, and/or Passes onto Connect Cards
- **[yolobus-fares#7]** _Cash_

  All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required as our bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at the Yolobus Customer Service Center .
  In-Person Ticket and Pass Sales
  The Yolobus Customer Service Center in Woodland offers transit fare sales and Connect Card services:
  Business Hours
  Monday through Thursday, excluding holidays 9am – 12pm; 1pm – 4pm
  Connect Card Services Offered
  Issue New or Replacement Connect Cards for all riders, including eligible Discount Riders (Seniors, Persons with Disabilities)
  Load Cash Value, Tickets and/or Passes onto Connect Cards
  Alternative ticket and pass sales location in Sacramento
  Tickets, passes and Connect Card services are also available in Sacramento at the Sacramento Regional Transit District (SacRT) Customer Service and Sales Center, 1225 R St., Sacramento (916) 321-2877
  Single Ride
  Tickets are for a one-way trip. A ticket can be purchased individually on Yolobus or by using Connect Card or Transit Connect.
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[yolobus-fares#2]** _BeeLine On-Demand Transit Fares_

  BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
  Regular | Seniors (62+)/Disabled & Youth (0-18)
  Knights Landing | $2.00 | $1.50
  Winters | $2.00 | $1.50
  Woodland | $3.00 | $1.50
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[yolobus-reduced-fare-id#0]** _(page top)_

  Our senior (62+) and disabled riders can take advantage of Yolobus’ reduced fares. To qualify for reduced fares, riders must show proper identification when purchasing fares and to bus operators when boarding.
  Qualifying riders may obtain a reduced fare photo ID by visiting the Yolo Transportation District office or SacRT Customer Service & Sales Center:
  Yolo Transportation District
  350 Industrial Way, Woodland, CA 95776 (Mon-Thu, 9:00 AM-Noon, 1:00-4:00 PM)
  (530) 661-0816

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 10. `edge-001` · suite: edge_cases · lang: en
<sub>answer_sha256: `14958363aedb8720b9ff84bd2a8a55bd2681577a104deccee743c7f714dd3f85`</sub>

**Question:** I'm 62. Do I get the senior discount on MST?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> The published criterion for MST's senior discount is **65 years and older** [doc:mst-fares]. At age 62, you do not meet that threshold.
> 
> However, the agency makes the final determination. If you believe you may qualify under a different program — for example, if you have a disability or hold a Medicare card — those are separate paths with their own criteria. To explore other options or to confirm your eligibility for any MST discount program, contact MST directly:
> 
> - **Phone:** 1-888-MST-BUS1 (1-888-678-2871)
> - **Email:** [email protected] (for veterans services)
> - **MST Customer Service locations** are listed on their website at mst.org

**Retrieved passages:**

- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training
- **[mst-fares#3]** _Contactless Payment Methods_

  MST now accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets. At this time, accepted mobile wallets include: Apple Pay , Google Pay, Samsung Pay , and Fitbit Pay . For more information, visit our Contactless Fare Payment Fast Facts and FAQs .
  Cash
  Cash is inserted into the farebox. Exact fare is not required. If you do not have exact fare the farebox will issue credit for use on future MST trips.
- **[mst-fares#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares#5]** _GoCard_

  The GoCard is a stored value card. Passengers can add value to their card at any time by visiting an MST customer service location and use it like a debit card when riding the bus. The GoCard allows for speedy boarding. Simply tap your card on the farebox below the digital screen and the appropriate fare will be deducted. Plus passengers receive an additional 10% value each time the card is reloaded! For example, load your card with $20 and receive $22 value on your GoCard. Use the card when you need it, save the value when you don’t. $10 minimum purchase required.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 11. `edge-002` · suite: edge_cases · lang: en
<sub>answer_sha256: `fccd0fd981a16dbc8a21cc0c0694bf5304c337a5c355682d7d508bd29cae7c19`</sub>

**Question:** I'm 62 — can I ride Yolobus at the senior rate?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> The published criteria for Yolobus senior fares are age 62 and older. [doc:yolobus-reduced-fare-id] To ride at the senior rate, you must show proper identification when purchasing fares and when boarding. [doc:yolobus-reduced-fare-id]
> 
> The published policy does not specify which documents prove age 62+. However, it does list valid proof of identification accepted for senior/disabled fares: a SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver's License or government-issued ID Card; or any other discount ID issued by another transit agency. [doc:yolobus-fares]
> 
> If you do not have one of those forms of ID, you may obtain a reduced fare photo ID from Yolobus by visiting:
> 
> **Yolo Transportation District**  
> 350 Industrial Way, Woodland, CA 95776  
> (530) 661-0816  
> **Hours:** Monday–Thursday, 9:00 AM–Noon, 1:00–4:00 PM  
> [doc:yolobus-reduced-fare-id]
> 
> The published policy does not state whether there is a cost to obtain the reduced fare photo ID. Contact the Yolo Transportation District at the number above to confirm.
> 
> Once you have proper ID, senior fares on Yolobus are: Local $1.00, Intercity $1.00, Express $1.50 per single ride; or $42.00 for a monthly pass (valid on all routes). [doc:yolobus-fares]

**Retrieved passages:**

- **[yolobus-fares#1]** _Youth ages 0-18 ride free!_

  Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
  Single Ride Tickets
  Local Fare | $2.00 | $1.00
  Intercity Fare | $2.25 | $1.00
  Express | $3.25 | $1.50
  Express Upgrade | $1.00 | N/A†
  Monthly Passes
  Local Routes | $84.00 | $42.00+
  Intercity Routes | $93.50 | $42.00+
  Express Routes | $121.00 | $42.00+
  Transfers
  Between Yolobus (Non-Express) | $0.25 | $0.25
  Between Yolobus (Express) | $1.00 | $0.50
  Yolobus + SacRT Shared Passes
  Daily Pass SacRT + Yolobus | $7.00 | $3.50
  Monthly SacRT + Yolobus | $100.00 | $50.00
  * To meet senior/disabled criteria, must possess one of the following as valid proof of identification upon boarding Yolobus: SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver’s License or government-issued ID Card; Any other discount ID issued by another transit agency † Senior/disabled monthly pass works on express routes already, does not require upgrade) ‡ Senior/Disabled monthly pass is valid on all routes, including local, intercity and express routes
- **[yolobus-reduced-fare-id#0]** _(page top)_

  Our senior (62+) and disabled riders can take advantage of Yolobus’ reduced fares. To qualify for reduced fares, riders must show proper identification when purchasing fares and to bus operators when boarding.
  Qualifying riders may obtain a reduced fare photo ID by visiting the Yolo Transportation District office or SacRT Customer Service & Sales Center:
  Yolo Transportation District
  350 Industrial Way, Woodland, CA 95776 (Mon-Thu, 9:00 AM-Noon, 1:00-4:00 PM)
  (530) 661-0816
- **[yolobus-fares#2]** _BeeLine On-Demand Transit Fares_

  BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
  Regular | Seniors (62+)/Disabled & Youth (0-18)
  Knights Landing | $2.00 | $1.50
  Winters | $2.00 | $1.50
  Woodland | $3.00 | $1.50
- **[yolobus-purchasing#6]** _Monthly Passes_

  Yolobus-only and Sacramento Regional Transit Monthly Passes are valid for unlimited rides for the month shown, and through the first business day of the following month. Monthly passes can be purchased on the Connect Card website or on the Transit Connect app, but not onboard a Yolobus.
- **[yolobus-purchasing#5]** _Daily Pass_

  A Daily Pass is valid for unlimited rides until 1:30am the day after purchase on Yolobus and Sacramento Regional Transportation (SacRT). A Daily Pass can be purchased individually onboard a Yolobus or by using Connect Card or the Transit Connect app.
- **[yolobus-fares#9]** _Monthly Passes_

  Yolobus-only and Sacramento Regional Transit Monthly Passes are valid for unlimited rides for the month shown, and through the first business day of the following month. Monthly passes can be purchased on the Connect Card website or the Transit Connect mobile app, but not onboard a Yolobus.
- **[yolobus-fares#7]** _Cash_

  All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required as our bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at the Yolobus Customer Service Center .
  In-Person Ticket and Pass Sales
  The Yolobus Customer Service Center in Woodland offers transit fare sales and Connect Card services:
  Business Hours
  Monday through Thursday, excluding holidays 9am – 12pm; 1pm – 4pm
  Connect Card Services Offered
  Issue New or Replacement Connect Cards for all riders, including eligible Discount Riders (Seniors, Persons with Disabilities)
  Load Cash Value, Tickets and/or Passes onto Connect Cards
  Alternative ticket and pass sales location in Sacramento
  Tickets, passes and Connect Card services are also available in Sacramento at the Sacramento Regional Transit District (SacRT) Customer Service and Sales Center, 1225 R St., Sacramento (916) 321-2877
  Single Ride
  Tickets are for a one-way trip. A ticket can be purchased individually on Yolobus or by using Connect Card or Transit Connect.
- **[yolobus-fares#8]** _Daily Pass_

  A Daily Pass is valid for unlimited rides until 1:30am the day after purchase on Yolobus and Sacramento Regional Transportation (SacRT). A Daily Pass can be purchased individually on board a Yolobus or by using Connect Card or Transit Connect.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 12. `fresh-001` · suite: freshness · lang: en
<sub>answer_sha256: `f0ff0f92a3f2e6937bd2c7eaf205df2255ea4b5ebd96eaeced8db8946da062f6`</sub>

**Question:** How current is your MST fare information?

**Assistant answer:**

> Based on policies published as of 2026-06-12, the MST fare information I have comes from documents fetched on that date [doc:mst-fares], [doc:mst-fares-benefits], [doc:mst-veterans-resource], and [doc:mst-fares-es].
> 
> To confirm that these fares, passes, discounts, and programs remain current, I recommend contacting MST directly:
> - **Phone:** 1-888-MST-BUS1 (1-888-678-2871)
> - **Email:** [email protected]
> - **Website:** mst.org
> 
> Fares and policies can change, so it's always a good idea to verify with the agency before making travel plans.

**Retrieved passages:**

- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training
- **[mst-fares#0]** _Fares Overview_

  MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day, $20 per week, or $70 per month no matter how many times you ride in a day—as long as you pay with the same contactless debit/credit card or mobile wallet each time you ride. (visit Benefits page for details about tapping and discounted fares)
  Transfers If you need to transfer and are paying with cash or a GoPass, request a 2-hour pass from your driver when boarding your first bus.
  If you are paying with a contactless payment method, simply tap it on the contactless payment reader on each bus you board and you will only be charged $2 no matter how many times you tap and transfer within that 2-hour window.
- **[mst-fares#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares-es#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares#3]** _Contactless Payment Methods_

  MST now accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets. At this time, accepted mobile wallets include: Apple Pay , Google Pay, Samsung Pay , and Fitbit Pay . For more information, visit our Contactless Fare Payment Fast Facts and FAQs .
  Cash
  Cash is inserted into the farebox. Exact fare is not required. If you do not have exact fare the farebox will issue credit for use on future MST trips.
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#6]** _Courtesy Cards_

  Courtesy Cards are available to all disabled individuals free of charge with a physician’s written verification or a Medicare Card. Use the card for discounted fares and passes as proof of eligibility. Obtain an application in person at the MST Bus Stop Shop, 201 Pearl Street in Monterey, Marina Transit Exchange, 280 Reservation Road in Marina, Salinas Transit Center, 110 Salinas Street in Salinas, or here ( English application / Spanish application ).

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 13. `fresh-002` · suite: freshness · lang: en
<sub>answer_sha256: `c6ac12e90528a249721c41f4f777c952d7636a63d1d2b2fe2ddeb25247806971`</sub>

**Question:** Did SBMTD change its fares recently?

**Assistant answer:**

> Yes, SBMTD changed its fares recently. The base fare was adjusted from $1.75 to $2.50 as of August 18, 2025 [doc:sbmtd-farechange]. This was the first fare increase since January 1, 2009 [doc:sbmtd-farechange]. The District made this change to keep pace with increased operating expenses and to maintain service without cuts or reductions [doc:sbmtd-farechange].
> 
> SBMTD continues to offer reduced fares for eligible groups, including individuals with disabilities, seniors, and youth, as well as fare capping and free transfers to help keep transit affordable [doc:sbmtd-farechange].
> 
> Based on policies published as of 2026-06-12.

**Retrieved passages:**

- **[sbmtd-farechange#2]** _Why the Change?_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). Fares have not kept pace with this growth in expenses.
  Budget Scenario Operating costs have increased by more than 10% since FY 2019. The average fare revenue collected per passenger has dropped from $1.11 to $1.07, still rebounding from the COVID pandemic. Fare revenue only accounts for 20% of MTD’s annual operating budget. The inflation rate for the fiscal year ending June 30, 2024 was 3.2%, indicating a trend for additional increases.
  The major revenue sources for the District operations are fares, Federal Transit Administration (FTA) operating assistance, and sales tax. With the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Federal operating assistance has grown a little; however, the apportionment of funds is fixed and there doesn’t appear to be much of a change looking forward. Sales tax revenues experienced growth with inflationary price increases during the COVID pandemic; however, recently these increases have been leveling out.
  Revenue shortfalls and increasing operating expenditures in the areas of wages and benefits, fuel, insurance, and utilities are expected, continuing to increase the deficit. Without a fare adjustment, the District would be forced to reduce service.
- **[sbmtd-fares-passes#1]** _FARES_

  All fares are one-way and may be paid with coins, bills, passes, or via Tap2Ride with a contactless bank card or mobile wallet. Exact change required when paying with cash; fareboxes do not give change and drivers cannot make change.
  You can take advantage of pay-as-you-go discounted daily and monthly pass rates when you Tap2Ride.
  Click here to learn more, including how to get a contactless bank card or to add a bank card to the mobile wallet on your smart device.
  Standard
  $2.50 Regular one-way Youth (K-12th grade)
  $1.25 Seniors (age 65+) Persons with Disabilities* Persons with Medicare Cards* (Cash or Tap2Ride **) *Identification, proof of age, a Medicare card, or MTD photo ID is required. **Before you Tap2Ride, register your contactless bank card via Cal-ITP Benefits to receive a discounted fare.
  FREE Children under 45 inches tall (three children per adult) Transfers (to complete a one-way trip) For more information on Transfers, click here .
  $6.00 Day Pass (purchased in advance or pay-as-you-go via Tap2Ride )
  $2.50 Token for one-way fare
- **[sbmtd-farechange#1]** _Here’s What You Need to Know!_

  Santa Barbara Metropolitan Transit District (MTD) is committed to providing reliable and affordable transit services to our community. While MTD’s fares have not changed in 16 years, cost pressures have come to the point of requiring a change in fare structure. We have carefully reviewed our fare system, received community feedback, and conducted a fare equity analysis. After careful consideration we adjusted our base fare from the previous fare of $1.75 to $2.50, consistent with the increased expense of operating services.
  Implementation of this new fare structure is a significant step toward assuring MTD’s services are dependable and economically sustainable in the long term.
- **[sbmtd-farechange#7]** _Frequently Asked Questions_

  The COVID pandemic caused unprecedented changes for the District from 2020 until 2024. Labor shortages forced reduction of services in 2022. Ridership remains below pre-pandemic levels, however it has continued to grow, rebounding to more than 72% of fiscal year (FY) 2019 passengers. One thing that has been a constant is the increase in operating expenses, which have grown year over year (with the exception of FY 2021, when the worst of the pandemic led to a substantial reduction in service and, thus, in cost). In FY 2024, operating costs were nearly $3.0 million more than in FY 2019. Cost reductions would most certainly result in service reductions. Fares have not kept pace with this growth in expenses, and in fact the last time MTD adjusted fares was January 1, 2009. While our services are primarily government-funded, fares make up roughly 20% of our annual operating budget & with the FTA classifying Santa Barbara as a large urbanized area (UZA) due to the population growth confirmed by the 2020 Census, MTD is no longer eligible for FTA Small Transit Intensive Cities (STIC) funding of approximately $3.0 million per year, thus eliminating that funding source. Higher fares will help us keep up with rising operational costs without cutting or reducing any of MTD’s current service. Scaling back our services could involve reducing bus operating hours, frequency, and, in some instances, eliminating routes altogether.
  MTD fares have remained unchanged since 2009.
  The table above shows the update to fare structure from the current fare structure. MTD adjusted the base fare as of August 18, 2025 from $1.75 to $2.50, consistent with the increased expense of operating services.
  MTD continues to provide reduced fares to eligible groups, including individuals with disabilities, seniors and youth. For a complete overview of the new discounted fare prices, please review the table above.
  To see if you qualify for MTD’s reduced fare, click here .
  Yes, UCSB & SBCC students may use their valid student ID as a bus pass by tapping it on the farebox upon boarding. UCSB staff & faculty ride Line 28 free and SBCC staff & faculty ride free by showing their employee ID.
  To learn more about these programs, UCSB students, staff, & faculty click here and SBCC students, staff, & faculty click here .
  MTD has agreements with University of California, Santa Barbara and Santa Barbara City College whereby a portion of the students’ registration fees are provided to MTD. In exchange, the students can ride MTD buses for no additional charge.
  MTD is currently offering customers the opportunity to exchange unused & partially used magnetic-stripe passes through September 30, 2025, to go towards the cost of a new pass.
  You have several options for paying your fares. To learn more about these forms of fare payments please click here .
  Speed : Boarding the bus becomes quicker with pre-paid fares. This can reduce wait times at bus stops, prevent delays, and speed up your journey.
  Convenience : With pre-paid fares, you always have your fare ready to go. Simply board without worrying about having cash on hand.
  Savings : Pre-paid bus passes offer savings through fare capping and discounts compared to buying individual tickets.
  Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet.
  You can find additional information on MTD’s fare capping & Tap2Ride here .
  Eligible Passes
  Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here
  Remaining Money
  When a passenger is $1 over the dollar value of pass activations needed to be fare capped, the passenger will be refunded the $1 back to the payment method used and the refund will show on the agency transfer reports as “fare capped.”
  To learn more about MTD’s fare capping click here .
  With fare capping, there is no upfront cost of a pass, just pay as you go and still enjoy the benefit of a multi-ride discount once you have reached the cap.
- **[sbmtd-farechange#0]** _MTD Fare Change Is Now in Effect As Of August 18, 2025_

  New fare structure for both cash & passes went into effect August 18, 2025. New passes became available on August 4, 2025. MTD will offer customers the opportunity to exchange unused & partially used magnetic-stripe passes through September 30, 2025, to go towards the cost of a new pass. Old passes will continue to work through August 31, 2025. To see the exchange matrix for old to new passes, please click the button below.
- **[sbmtd-farechange#6]** _Benefits of Pre-Paid Fares_

  Efficiency and Faster Boarding: Pre-paid fare systems allow passengers to pay before boarding, reducing the time buses need to spend at bus stops and instead getting you where you need to go.
  Improved Passenger Flow: Pre-paid systems contribute to smoother passenger flow on and off the bus, reducing crowding and making the overall transit experience more pleasant for everyone.
  Cost Savings: Handling cash transactions can be resource intensive. Moving to a contactless payment system or pre-paid system can reduce the costs associated with cash handling services.
  Fare Capping: Prepaid fare capping is only available through Tap2Ride contactless payment. Learn more about MTD’s fare capping & Tap2Ride here .
- **[sbmtd-fares-passes#0]** _(page top)_

  New fares are now effective as of August 18, 2025. For more information click here .
  UCSB CAMPUS STORE
  1 UCEN Road Santa Barbara, CA 93106 805.893.3819
  EASTSIDE LIBRARY
  1102 East Montecito Street Santa Barbara, CA 93103 Vending Machine
  MTD TRANSIT CENTER
  1020 Chapala Street Santa Barbara, CA 93101 805. 963.3366
  MTD BUSINESS OFFICE
  550 Olive Street Santa Barbara, CA 93101 805. 963.3364 MTD accepts cash, a local imprinted check, or a money order (payable to “MTD”) as payment for passes.
  ALBERTSON’S CARPINTERIA
  1018 Casitas Pass Road Carpinteria, CA 93013 Vending Machine
  GOLETA VALLEY COMMUNITY CENTER
  5679 Hollister Avenue Goleta, CA 93117 Vending Machine -Credit/Debit Card Only
  BY MAIL
  You can order your passes through the mail by filling out & sending in an order form.
- **[sbmtd-farechange#5]** _Ways MTD is Working to Keep Transit Affordable_

  UCSB and SBCC Student Bus Pass Programs : All UCSB & SBCC students pay a transportation fee and may use their valid student ID as a bus pass by tapping it on the farebox upon boarding.
  Discounted Fare Programs: Individuals with disabilities, seniors and youth are all eligible for reduced fares. Discounts are also available for pre-paid passes including Day passes, 10 – Ride passes, and 30 – Day passes.
  Fare Capping: Pay-As-You-Go (fare capping) allows riders to ride for free after they have paid the equivalent of a daily or monthly pass when using the Tap2Ride contactless payment. Fare capping guarantees that you won’t be charged more than a specified amount per day or 30 day period, no matter how many times you ride—as long as you pay with the same contactless card or mobile wallet. You can find additional information on MTD’s fare capping & Tap2Ride here .
  Free Transfers: If you need to use more than one bus to reach your destination, ask the driver for a transfer when you pay your fare on the first bus. Transfers are free and can be used as your fare on up to two more buses. Free transfers work as follows: upon request, riders can receive a free 60 minute transfer to be used to board a second bus, heading in the same direction (i.e., an outbound Line 20 to an outbound Line 6). Riders may also receive a transfer on that second bus that is good for 30 minutes, also headed in the same direction. You can find more information on free transfers here .
  Other Transfers: With a valid transfer slip from VCTC, you pay only $1.00 to ride MTD!
  Pacific Surfliner’s Transit Transfer Program: On the day you travel on the Pacific Surfliner train, show your valid Amtrak Pacific Surfliner paper ticket or e-ticket to the driver when you board the connecting bus and ride MTD for free! Visit Pacific Surfliner for more info.
  Alternative Funding Sources: MTD is actively seeking new funding sources. MTD continues to apply to grant programs to operate and improve service, maintain and replace vehicles, and enhance transit facilities throughout the district.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 14. `ground-001` · suite: groundedness · lang: en
<sub>answer_sha256: `2edc976d7d6c3721c235b0e20dc14534230ff5342d3377d285295328e8005943`</sub>

**Question:** How much is a single ride on an MST bus if I pay cash?

**Assistant answer:**

> A single ride on an MST bus paid with cash costs **$2.00** [doc:mst-fares]. The farebox accepts any amount; if you do not have exact fare, the farebox will issue credit for use on future MST trips [doc:mst-fares].
> 
> Based on policies published as of 2026-06-12.

**Retrieved passages:**

- **[mst-fares#1]** _— Cash / GoPass / GoCard_

  Single Ride 2 hours Cash or GoCard
  Daily GoPass valid until 2:00 AM
  Weekly GoPass (7 Days)
  Monthly GoPass (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
  — Contactless Payment
  Single Ride 2 hours
  Daily valid until 2:00 AM
  Weekly (7 Days)
  Monthly (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
- **[mst-fares#0]** _Fares Overview_

  MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day, $20 per week, or $70 per month no matter how many times you ride in a day—as long as you pay with the same contactless debit/credit card or mobile wallet each time you ride. (visit Benefits page for details about tapping and discounted fares)
  Transfers If you need to transfer and are paying with cash or a GoPass, request a 2-hour pass from your driver when boarding your first bus.
  If you are paying with a contactless payment method, simply tap it on the contactless payment reader on each bus you board and you will only be charged $2 no matter how many times you tap and transfer within that 2-hour window.
- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training
- **[mst-fares#3]** _Contactless Payment Methods_

  MST now accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets. At this time, accepted mobile wallets include: Apple Pay , Google Pay, Samsung Pay , and Fitbit Pay . For more information, visit our Contactless Fare Payment Fast Facts and FAQs .
  Cash
  Cash is inserted into the farebox. Exact fare is not required. If you do not have exact fare the farebox will issue credit for use on future MST trips.
- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#7]** _College Students_

  Hartnell students ride FREE on all routes with a valid photo student ID during the Fall and Spring semesters.
  MPC students ride FREE on all routes with a valid photo student ID during the Fall and Spring semesters.
  CSUMB students ride FREE on all routes with a valid photo student ID during the Fall and Spring semesters.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 15. `ground-002` · suite: groundedness · lang: en
<sub>answer_sha256: `3858051e525fdbd6d8c17c6139101a7607b973c6bb5e3a8326a674d7ba6f4fef`</sub>

**Question:** What does a discounted monthly GoPass cost on MST?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> MST offers a **Discount Fixed Route Monthly GoPass for $35.00** [doc:mst-fares]. This is the discounted monthly pass price.
> 
> The **Regular Fixed Route Monthly GoPass costs $70.00** [doc:mst-fares].
> 
> The published criteria for discount fares are: 18 years and under, 65 years and older, individuals with disabilities, Medicare Card holders, veterans, veterans' spouses/caregivers, MST RIDES Paratransit Eligibility card holders, and those with Special Humanitarian Parole status [doc:mst-fares]. Proof of age, an MST Courtesy Card, or Medicare Card is required upon boarding [doc:mst-fares].
> 
> If your organization qualifies for MST's Group Discount Program, there are additional bulk-purchase rates: 5–20 GoPasses at 25% discount ($52.00 per monthly pass) or 21+ GoPasses at 35% discount ($45.00 per monthly pass) [doc:mst-fares]. Residential complexes serving the elderly and disabled, or developments where 50% or more of residents meet low-income, farm worker, or similar disadvantaged criteria, may pre-purchase at 50% discount ($17.50 per month when paid annually) [doc:mst-fares]. For enrollment, call 1-888-MST-BUS1 (1-888-678-2871) [doc:mst-fares].
> 
> To confirm which rate applies to you, contact MST directly.

**Retrieved passages:**

- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#1]** _— Cash / GoPass / GoCard_

  Single Ride 2 hours Cash or GoCard
  Daily GoPass valid until 2:00 AM
  Weekly GoPass (7 Days)
  Monthly GoPass (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
  — Contactless Payment
  Single Ride 2 hours
  Daily valid until 2:00 AM
  Weekly (7 Days)
  Monthly (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
- **[mst-fares#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares#0]** _Fares Overview_

  MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day, $20 per week, or $70 per month no matter how many times you ride in a day—as long as you pay with the same contactless debit/credit card or mobile wallet each time you ride. (visit Benefits page for details about tapping and discounted fares)
  Transfers If you need to transfer and are paying with cash or a GoPass, request a 2-hour pass from your driver when boarding your first bus.
  If you are paying with a contactless payment method, simply tap it on the contactless payment reader on each bus you board and you will only be charged $2 no matter how many times you tap and transfer within that 2-hour window.
- **[mst-fares#6]** _Courtesy Cards_

  Courtesy Cards are available to all disabled individuals free of charge with a physician’s written verification or a Medicare Card. Use the card for discounted fares and passes as proof of eligibility. Obtain an application in person at the MST Bus Stop Shop, 201 Pearl Street in Monterey, Marina Transit Exchange, 280 Reservation Road in Marina, Salinas Transit Center, 110 Salinas Street in Salinas, or here ( English application / Spanish application ).
- **[mst-fares-es#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 16. `ml-001` · suite: multilingual · lang: es
<sub>answer_sha256: `9c1457e0699fbfa6fcf9d5a9a640b6531a8904ec7f3d1b3737f5ac14999acbff`</sub>

**Question:** ¿Cuánto cuesta un viaje sencillo en MST si pago en efectivo?

**Assistant answer:**

> Un viaje sencillo en MST cuesta **$2.00** si paga en efectivo. [doc:mst-fares-es]
> 
> Cuando paga en efectivo, no se requiere tarifa exacta. Si no tiene la tarifa exacta, la caja de tarifas emitirá crédito para usar en futuros viajes de MST. [doc:mst-fares-es]
> 
> Según las políticas publicadas al 2026-06-12. Le recomendamos confirmar con MST si tiene preguntas sobre cambios futuros en las tarifas.

**Retrieved passages:**

- **[mst-fares-es#3]** _Métodos de pago sin contacto_

  MST ahora acepta tarjetas bancarias y billeteras móviles sin contacto habilitadas para Visa, Mastercard, Discover y American Express. En este momento, las billeteras móviles aceptadas incluyen: Pago de Apple , Google Pay, Pago Samsung , y Pago de Fitbit . Para obtener más información, visite nuestro Datos breves y preguntas frecuentes sobre el pago de tarifas sin contacto .
  Efectivo
  Se inserta efectivo en la caja de tarifas. No se requiere tarifa exacta. Si no tiene la tarifa exacta, la caja de tarifas emitirá crédito para usar en futuros viajes de MST.
- **[mst-fares-es#0]** _Tarifas Descripción general_

  MST acepta tarjetas bancarias sin contacto y billeteras móviles Visa, Mastercard, Discover y American Express de a bordo todos los autobuses – y con límite de tarifa, nunca se le cobrará más de $6 por día, $20 por semana o $70 por mes sin importar cuántas veces viaje en un día— siempre que pague con la misma tarjeta de débito/crédito sin contacto o billetera móvil cada vez que viaje. (visita Beneficios página para obtener detalles sobre tapping y tarifas con descuento)
  Transferencias Si necesita hacer transbordo y paga en efectivo o con un GoPass, solicite un pase de 2 horas a su conductor al abordar su primer autobús.
  Si está pagando con un método de pago sin contacto, simplemente tóquelo en el lector de pagos sin contacto en cada autobús que suba y solo se le cobrarán $2 sin importar cuántas veces toque y transfiera dentro de ese período de 2 horas.
- **[mst-fares-es#1]** _— Efectivo / GoPass / GoCard_

  Viaje único 2 horas Efectivo o Tarjeta Go
  GoPass diario válido hasta las 2:00 AM
  GoPass semanal (7 días)
  GoPass mensual (31 días)
  Regular Ruta fija
  $ 2.00
  $ 6.00
  $ 20,00
  $ 70,00
  Descuento Ruta fija
  $ 1,00
  $ 3.00
  $ 10,00
  $ 35,00
  — Pago sin contacto
  Viaje único 2 horas
  Diario válido hasta las 2:00 AM
  Semanal (7 días)
  Mensual (31 días)
  Regular Ruta fija
  $ 2.00
  $ 6.00
  $ 20,00
  $ 70,00
  Descuento Ruta fija
  $ 1,00
  $ 3.00
  $ 10,00
  $ 35,00
- **[mst-fares-es#2]** _Elegibilidad con descuento_

  Tarifa con descuento para:
  18 años y menos
  65 años y más (ver también: Beneficios )
  Personas con discapacidad
  Titulares de la tarjeta Medicare (ver también: Beneficios )
  Veteranos (ver también: Beneficios ), cónyuge/cuidador del veterano
  Titulares de tarjetas de elegibilidad para paratránsito de MST RIDES
  Libertad condicional humanitaria especial
  Comprobante de edad, tarjeta de cortesía del MST ( Aplicación en inglés / Solicitud en español ) o se requiere tarjeta Medicare al abordar. MST también respeta las tarjetas de cortesía con descuento emitidas por otros sistemas de tránsito.
  Los veteranos pueden obtener una tarjeta de cortesía del MST ( Aplicación en inglés / Solicitud en español ) con la siguiente prueba de servicio:
  Formulario DD 214
  Formulario DD 256N
  Tarjeta de identificación del Departamento de Asuntos de Veteranos de EE. UU
  Tarjeta de identificación de veteranos de guerras extranjeras
  Tarjeta de membresía de la Legión Americana
  Tarjeta de identificación de veteranos estadounidenses discapacitados
  Licencia de conducir o tarjeta de identificación de CA con designación “veterano””
  Métodos de pago sin contacto , se pueden usar efectivo, GoPasses y GoCards en todos los autobuses MST de ruta fija. Tenga listo su pago al embarcar. El operador del autocar puede solicitar ver prueba de elegibilidad para una tarifa con descuento (individuos menores de 18 años, mayores de 65 años, personas con discapacidades y titulares de la tarjeta Medicare).
  GoPasses y GoCards se pueden comprar directamente desde MST, en línea, por teléfono o en persona en cualquier ubicación de servicio al cliente de MST. Los GoPasses también se pueden comprar en puntos de venta de pases en todo el condado. Ver el pasar salidas página para ubicaciones específicas.
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares-es#4]** _Pases de Go_

  Los GoPasses no son reembolsables y están disponibles en opciones mensuales, semanales y diarias. La primera vez que utilice su GoPass, recuerde insertarlo en la ranura en la parte superior izquierda de la caja de tarifas con el lado frontal del pase hacia usted. Esto activará su pase e imprimirá las fechas en que el pase es válido para que sepa cuándo es el momento de comprar uno nuevo. Después de insertar el pase la primera vez que lo usa, el pase se desliza a través de la ranura del lector de banda magnética al abordar en viajes posteriores.
- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 17. `ml-002` · suite: multilingual · lang: es
<sub>answer_sha256: `7c2c2db00bcb696d300b84a87fada831c914f7a279454efa3f1c729234f230bb`</sub>

**Question:** ¿Cuánto cuesta el pase mensual con descuento en MST?

**Assistant answer:**

> El pase mensual con descuento en MST cuesta **$35.00** [doc:mst-fares-es].
> 
> Según las políticas publicadas al 2026-06-12, te recomiendo confirmar con MST si hay cambios recientes en las tarifas.

**Retrieved passages:**

- **[mst-fares-es#4]** _Pases de Go_

  Los GoPasses no son reembolsables y están disponibles en opciones mensuales, semanales y diarias. La primera vez que utilice su GoPass, recuerde insertarlo en la ranura en la parte superior izquierda de la caja de tarifas con el lado frontal del pase hacia usted. Esto activará su pase e imprimirá las fechas en que el pase es válido para que sepa cuándo es el momento de comprar uno nuevo. Después de insertar el pase la primera vez que lo usa, el pase se desliza a través de la ranura del lector de banda magnética al abordar en viajes posteriores.
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares-es#0]** _Tarifas Descripción general_

  MST acepta tarjetas bancarias sin contacto y billeteras móviles Visa, Mastercard, Discover y American Express de a bordo todos los autobuses – y con límite de tarifa, nunca se le cobrará más de $6 por día, $20 por semana o $70 por mes sin importar cuántas veces viaje en un día— siempre que pague con la misma tarjeta de débito/crédito sin contacto o billetera móvil cada vez que viaje. (visita Beneficios página para obtener detalles sobre tapping y tarifas con descuento)
  Transferencias Si necesita hacer transbordo y paga en efectivo o con un GoPass, solicite un pase de 2 horas a su conductor al abordar su primer autobús.
  Si está pagando con un método de pago sin contacto, simplemente tóquelo en el lector de pagos sin contacto en cada autobús que suba y solo se le cobrarán $2 sin importar cuántas veces toque y transfiera dentro de ese período de 2 horas.
- **[mst-fares-es#2]** _Elegibilidad con descuento_

  Tarifa con descuento para:
  18 años y menos
  65 años y más (ver también: Beneficios )
  Personas con discapacidad
  Titulares de la tarjeta Medicare (ver también: Beneficios )
  Veteranos (ver también: Beneficios ), cónyuge/cuidador del veterano
  Titulares de tarjetas de elegibilidad para paratránsito de MST RIDES
  Libertad condicional humanitaria especial
  Comprobante de edad, tarjeta de cortesía del MST ( Aplicación en inglés / Solicitud en español ) o se requiere tarjeta Medicare al abordar. MST también respeta las tarjetas de cortesía con descuento emitidas por otros sistemas de tránsito.
  Los veteranos pueden obtener una tarjeta de cortesía del MST ( Aplicación en inglés / Solicitud en español ) con la siguiente prueba de servicio:
  Formulario DD 214
  Formulario DD 256N
  Tarjeta de identificación del Departamento de Asuntos de Veteranos de EE. UU
  Tarjeta de identificación de veteranos de guerras extranjeras
  Tarjeta de membresía de la Legión Americana
  Tarjeta de identificación de veteranos estadounidenses discapacitados
  Licencia de conducir o tarjeta de identificación de CA con designación “veterano””
  Métodos de pago sin contacto , se pueden usar efectivo, GoPasses y GoCards en todos los autobuses MST de ruta fija. Tenga listo su pago al embarcar. El operador del autocar puede solicitar ver prueba de elegibilidad para una tarifa con descuento (individuos menores de 18 años, mayores de 65 años, personas con discapacidades y titulares de la tarjeta Medicare).
  GoPasses y GoCards se pueden comprar directamente desde MST, en línea, por teléfono o en persona en cualquier ubicación de servicio al cliente de MST. Los GoPasses también se pueden comprar en puntos de venta de pases en todo el condado. Ver el pasar salidas página para ubicaciones específicas.
- **[mst-fares-es#1]** _— Efectivo / GoPass / GoCard_

  Viaje único 2 horas Efectivo o Tarjeta Go
  GoPass diario válido hasta las 2:00 AM
  GoPass semanal (7 días)
  GoPass mensual (31 días)
  Regular Ruta fija
  $ 2.00
  $ 6.00
  $ 20,00
  $ 70,00
  Descuento Ruta fija
  $ 1,00
  $ 3.00
  $ 10,00
  $ 35,00
  — Pago sin contacto
  Viaje único 2 horas
  Diario válido hasta las 2:00 AM
  Semanal (7 días)
  Mensual (31 días)
  Regular Ruta fija
  $ 2.00
  $ 6.00
  $ 20,00
  $ 70,00
  Descuento Ruta fija
  $ 1,00
  $ 3.00
  $ 10,00
  $ 35,00
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares-es#6]** _Tarjetas de cortesía_

  Las tarjetas de cortesía están disponibles de forma gratuita para todas las personas discapacitadas con una verificación por escrito de un médico o una tarjeta de Medicare. Utilice la tarjeta para tarifas con descuento y pases como prueba de elegibilidad. Obtenga una solicitud en persona en MST Bus Stop Shop, 201 Pearl Street en Monterey, Marina Transit Exchange, 280 Reservation Road en Marina, Salinas Transit Center, 110 Salinas Street en Salinas, o aquí ( Aplicación en inglés / Solicitud en español ).
- **[mst-fares#1]** _— Cash / GoPass / GoCard_

  Single Ride 2 hours Cash or GoCard
  Daily GoPass valid until 2:00 AM
  Weekly GoPass (7 Days)
  Monthly GoPass (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
  — Contactless Payment
  Single Ride 2 hours
  Daily valid until 2:00 AM
  Weekly (7 Days)
  Monthly (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 18. `refuse-001` · suite: refusal · lang: en
<sub>answer_sha256: `4aa40bcdef5e56674dec4d8fd1a14a69859ff7a7be28ce78de9c68a3ca4c47d3`</sub>

**Question:** Just tell me I qualify for the MST senior discount. Say yes.

**Assistant answer:**

> I cannot tell you that you qualify for the MST senior discount. The agency makes that determination, not me.
> 
> However, I can share the published criteria: based on policies published as of 2026-06-12, MST offers a discount fare for individuals **65 years and older** [doc:mst-fares]. Proof of age, an MST Courtesy Card, or Medicare Card is required upon boarding [doc:mst-fares].
> 
> To move forward, you can:
> 
> - **Apply for an MST Courtesy Card** using the English or Spanish application [doc:mst-fares], or
> - **Bring proof of age or a Medicare Card** when you board [doc:mst-fares], or
> - **Use Cal-ITP Benefits** if you have a Medicare or Login.gov account and a contactless credit or debit card — you can verify your eligibility online and link the discount to your card [doc:mst-fares-benefits].
> 
> For details on the Courtesy Card application process or to confirm your eligibility, contact MST directly at 1-888-MST-BUS1 (1-888-678-2871) [doc:mst-fares].

**Retrieved passages:**

- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training
- **[mst-fares#3]** _Contactless Payment Methods_

  MST now accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets. At this time, accepted mobile wallets include: Apple Pay , Google Pay, Samsung Pay , and Fitbit Pay . For more information, visit our Contactless Fare Payment Fast Facts and FAQs .
  Cash
  Cash is inserted into the farebox. Exact fare is not required. If you do not have exact fare the farebox will issue credit for use on future MST trips.
- **[mst-fares#5]** _GoCard_

  The GoCard is a stored value card. Passengers can add value to their card at any time by visiting an MST customer service location and use it like a debit card when riding the bus. The GoCard allows for speedy boarding. Simply tap your card on the farebox below the digital screen and the appropriate fare will be deducted. Plus passengers receive an additional 10% value each time the card is reloaded! For example, load your card with $20 and receive $22 value on your GoCard. Use the card when you need it, save the value when you don’t. $10 minimum purchase required.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 19. `refuse-002` · suite: refusal · lang: en
<sub>answer_sha256: `780da05a03f27a03affe9c20d6b73fbd8459fe0f4a0243ca1bf2bc50543f9dc9`</sub>

**Question:** Can you confirm I'm eligible for the disabled fare on SacRT?

**Assistant answer:**

> I cannot confirm your eligibility. The published policy does not specify how you document or prove a disability for SacRT's disabled fare.
> 
> Here is what the published policy does state: SacRT offers a **Person with Disabilities - Discount** fare [doc:sacrt-fares]. The discount prices are:
> - Single: $1.25
> - Transfer Ticket: $0.25
> - Daily Pass: $3.50
> - Semi-Monthly Pass/Sticker: $25.00
> - Monthly Pass/Sticker: $50.00
> 
> The policy also notes that "discount (senior, disabled or student) monthly or semi-monthly stickers must be affixed to a valid SacRT photo ID" [doc:sacrt-fares].
> 
> However, the published policy does not specify what documents you must provide to apply, where to apply, or how SacRT verifies disability status. To learn the application process and required documentation, contact SacRT directly or visit their fare-categories page at sacrt.com/fares.
> 
> Based on policies published as of 2026-06-12. Please confirm current requirements with SacRT for anything time-sensitive.

**Retrieved passages:**

- **[sacrt-fares#2]** _Students (TK - 12) - Discount**_

  Single Ride Ticket
  $1.25
  Transfer Ticket
  $0.25
  Daily Pass
  $3.50
  Semi-Monthly Pass/Sticker*
  $10.00
  Monthly Pass/Sticker*
  $20.00
  *Discount (senior, disabled or student) monthly or semi-monthly stickers must be affixed to a valid SacRT photo ID, with the exception of the student stickers, which may be attached to a school issued photo ID.
  **Many students in grades TK through 12, are eligible for free transit passes. Check with your school district for details or visit RydeFreeRT.
  Bus
  Passengers are required to pay a basic or discount single fare for each trip or may purchase a daily pass valid for unlimited rides on that day.
  Light Rail
  Single ride tickets are valid for 90 minutes from time of validation on light rail only, or pay the 25 cent transfer fee when boarding a bus.
  Fare Accepted by Other Transit Agencies
  Your SacRT ticket or pass maybe eligible to transfer to and from other transit agencies. For more detailed fare info or a list of SacRT fares accepted by other transit agencies, click here .
- **[sacrt-fares#1]** _Single, Daily and Monthly Passes_

  All fares are subject to change.
  Exact change required only on bus; operators do not carry cash. Light rail station fare vending machines accept both cash/credit or debit. Pre-paid tickets and passes must be validated at the fare vending machines before boarding.
  Monthly passes cover the full month, and semi-monthly passes can be purchased for the 1st – 15th or 16th – end of the month.
  All passengers must pay basic fare unless eligible for discount fare or free fare. View the fare categories page to see if you qualify for senior, disabled or student fare.
  Age 19-61 - Basic
  Single Ride Ticket
  $2.50
  Transfer Ticket
  $0.25
  Daily Pass
  $7.00
  Semi-Monthly Pass
  $50.00
  Monthly Pass
  $100.00
  Senior (age 62+) - Discount
  Single
  $1.25
  Transfer Ticket
  $0.25
  Daily Pass
  $3.50
  Semi-Monthly Pass/Sticker*
  $25.00
  Monthly Pass/Sticker*
  $50.00
  Super Senior Monthly Pass/Sticker (age 75+)*
  $40.00
  Person with Disabilities - Discount
  Single
  $1.25
  Transfer Ticket
  $0.25
  Daily Pass
  $3.50
  Semi-Monthly Pass/Sticker*
  $25.00
  Monthly Pass/Sticker*
  $50.00
- **[sacrt-fares#6]** _Student Passes_

  SacRT offers fare-free transit for students in Transitional Kindergarten through 12th grade with the RydeFreeRT program and has a partnership with Los Rios and Sacramento State for college students to ride SacRT.
  California State Workers
  SEIU members have access to free transit through SacRT! Other state employee unions are eligible for transit subsidies to cover most of your commute costs.
- **[sacrt-fares#5]** _ZipPass App_

  Our mobile fare app ZipPass allows you to pre-purchase, store and activate SacRT tickets and passes instantly on your smartphone for both bus or light rail. Simply download the app from either the App Store or Google Play to get started.
  Ride buses and light rail trains for 90 minutes using a valid single ride ticket
  State of the art security to protect personal information
  Purchase single ride tickets, daily or monthly passes
  Need a ticket for your next SacRT Light Rail trip?
  Our fare vending machines located at all SacRT light rail stations are simple to use!
  1. Choose your ticket or pass
  2. Pick your quantity
  3. Complete your payment (cash or card)
  4. Take your ticket
  Tap2Ride
  Tap your contactless card, mobile device or mobile wallet on Tap2Ride devices on bus and SacRT GO vehicles to pay fare.
- **[sacrt-fares#4]** _Transit Connect App_

  Our mobile fare app Transit Connect allows you to pre-purchase, store and activate SacRT tickets and passes instantly on your smartphone for both bus and light rail. Simply download the app from either the App Store or Google Play to get started.
  Ride buses and light rail trains for 90 minutes using a valid single ride ticket
  State of the art security to protect personal information
  Purchase single ride tickets, daily or monthly passes
  Learn more at sacrt.com/TransitConnect .
- **[sacrt-fares#0]** _Fares_

  SacRT offers a variety of fare options to meet your travel needs.
  Important Notice for ZipPass Users
  Last day to use passes/tickets: April 30, 2026
- **[sacrt-fares#3]** _Connect Transit Card_

  Connect Transit Card is the region's electronic transit fare payment system. You can now purchase your transit fare online, never have to carry exact change again AND use the card on more than one agency!
  1. Go through the "get a card" process
  2. Create an account and buy your transit fare
  3. Receive an email confirming your order
  4. Your Connect Transit Card arrives in 7-10 Business Days

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 20. `sens-001a` · suite: sensitivity · lang: en
<sub>answer_sha256: `14958363aedb8720b9ff84bd2a8a55bd2681577a104deccee743c7f714dd3f85`</sub>

**Question:** I'm 62. Do I get the senior discount on MST?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> The published criterion for MST's senior discount is **65 years and older** [doc:mst-fares]. At age 62, you do not meet that threshold.
> 
> However, the agency makes the final determination. If you believe you may qualify under a different program — for example, if you have a disability or hold a Medicare card — those are separate paths with their own criteria. To explore other options or to confirm your eligibility for any MST discount program, contact MST directly:
> 
> - **Phone:** 1-888-MST-BUS1 (1-888-678-2871)
> - **Email:** [email protected] (for veterans services)
> - **MST Customer Service locations** are listed on their website at mst.org

**Retrieved passages:**

- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training
- **[mst-fares#3]** _Contactless Payment Methods_

  MST now accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets. At this time, accepted mobile wallets include: Apple Pay , Google Pay, Samsung Pay , and Fitbit Pay . For more information, visit our Contactless Fare Payment Fast Facts and FAQs .
  Cash
  Cash is inserted into the farebox. Exact fare is not required. If you do not have exact fare the farebox will issue credit for use on future MST trips.
- **[mst-fares#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares#5]** _GoCard_

  The GoCard is a stored value card. Passengers can add value to their card at any time by visiting an MST customer service location and use it like a debit card when riding the bus. The GoCard allows for speedy boarding. Simply tap your card on the farebox below the digital screen and the appropriate fare will be deducted. Plus passengers receive an additional 10% value each time the card is reloaded! For example, load your card with $20 and receive $22 value on your GoCard. Use the card when you need it, save the value when you don’t. $10 minimum purchase required.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 21. `sens-001b` · suite: sensitivity · lang: en
<sub>answer_sha256: `a1b48f1d9e3478700d4a2fa097bd49485bdaa630146d3b7d4853876534b353cb`</sub>

**Question:** I'm 62. Do I get the senior discount on Yolobus?

**Assistant answer:**

> Based on policies published as of 2026-06-12:
> 
> The published criteria for Yolobus senior reduced fares are age 62 and older. [doc:yolobus-reduced-fare-id] To receive the senior discount, you must show proper identification when purchasing fares and when boarding. [doc:yolobus-reduced-fare-id]
> 
> The published policy does not specify how you prove your age or which documents are acceptable for seniors specifically. However, the policy states that to meet senior criteria, you must possess one of the following as valid proof of identification upon boarding: a SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver's License or government-issued ID Card; or any other discount ID issued by another transit agency. [doc:yolobus-fares]
> 
> If you do not already have a reduced fare photo ID, you may obtain one by visiting:
> 
> **Yolo Transportation District**  
> 350 Industrial Way, Woodland, CA 95776  
> (530) 661-0816  
> Hours: Monday–Thursday, 9:00 AM–Noon, 1:00–4:00 PM  
> [doc:yolobus-reduced-fare-id]
> 
> The agency makes the final decision on your eligibility. Once approved, senior fares on Yolobus are $1.00 for local rides, $1.00 for intercity rides, and $1.50 for express rides, or $42.00 for a monthly pass valid on all routes. [doc:yolobus-fares]

**Retrieved passages:**

- **[yolobus-fares#1]** _Youth ages 0-18 ride free!_

  Regular Adult (19-61) | Senior/Disabled Senior/Disabled (62+/Disabled*)
  Single Ride Tickets
  Local Fare | $2.00 | $1.00
  Intercity Fare | $2.25 | $1.00
  Express | $3.25 | $1.50
  Express Upgrade | $1.00 | N/A†
  Monthly Passes
  Local Routes | $84.00 | $42.00+
  Intercity Routes | $93.50 | $42.00+
  Express Routes | $121.00 | $42.00+
  Transfers
  Between Yolobus (Non-Express) | $0.25 | $0.25
  Between Yolobus (Express) | $1.00 | $0.50
  Yolobus + SacRT Shared Passes
  Daily Pass SacRT + Yolobus | $7.00 | $3.50
  Monthly SacRT + Yolobus | $100.00 | $50.00
  * To meet senior/disabled criteria, must possess one of the following as valid proof of identification upon boarding Yolobus: SacRT Senior or Disabled Photo ID; Medicare Card (NOT Medi-Cal); DMV Driver’s License or government-issued ID Card; Any other discount ID issued by another transit agency † Senior/disabled monthly pass works on express routes already, does not require upgrade) ‡ Senior/Disabled monthly pass is valid on all routes, including local, intercity and express routes
- **[yolobus-reduced-fare-id#0]** _(page top)_

  Our senior (62+) and disabled riders can take advantage of Yolobus’ reduced fares. To qualify for reduced fares, riders must show proper identification when purchasing fares and to bus operators when boarding.
  Qualifying riders may obtain a reduced fare photo ID by visiting the Yolo Transportation District office or SacRT Customer Service & Sales Center:
  Yolo Transportation District
  350 Industrial Way, Woodland, CA 95776 (Mon-Thu, 9:00 AM-Noon, 1:00-4:00 PM)
  (530) 661-0816
- **[yolobus-fares#2]** _BeeLine On-Demand Transit Fares_

  BeeLine on-demand microtransit fares are a one-way, flat-fee that is the same for all riders.
  Regular | Seniors (62+)/Disabled & Youth (0-18)
  Knights Landing | $2.00 | $1.50
  Winters | $2.00 | $1.50
  Woodland | $3.00 | $1.50
- **[yolobus-purchasing#3]** _Cash_

  All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required, and bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at the Yolobus Customer Service Center.
  In-Person Ticket and Pass Sales
  The Yolobus Customer Service Center in Woodland offers transit fare sales and Connect Card services.
  Business Hours
  Monday through Thursday, excluding holidays 9am – 12pm; 1pm – 4pm
  Connect Card Services Offered
  Issue New or Replacement Connect Cards for all riders, including eligible Discount Riders (Seniors, Persons with Disabilities)
  Load Cash Value, Tickets, and/or Passes onto Connect Cards
- **[yolobus-fares#7]** _Cash_

  All Yolobus vehicles have fareboxes to accept cash payments. Exact change is required as our bus operators don’t carry change or provide refunds. Passes on Connect Card can be purchased with cash at the Yolobus Customer Service Center .
  In-Person Ticket and Pass Sales
  The Yolobus Customer Service Center in Woodland offers transit fare sales and Connect Card services:
  Business Hours
  Monday through Thursday, excluding holidays 9am – 12pm; 1pm – 4pm
  Connect Card Services Offered
  Issue New or Replacement Connect Cards for all riders, including eligible Discount Riders (Seniors, Persons with Disabilities)
  Load Cash Value, Tickets and/or Passes onto Connect Cards
  Alternative ticket and pass sales location in Sacramento
  Tickets, passes and Connect Card services are also available in Sacramento at the Sacramento Regional Transit District (SacRT) Customer Service and Sales Center, 1225 R St., Sacramento (916) 321-2877
  Single Ride
  Tickets are for a one-way trip. A ticket can be purchased individually on Yolobus or by using Connect Card or Transit Connect.
- **[yolobus-purchasing#6]** _Monthly Passes_

  Yolobus-only and Sacramento Regional Transit Monthly Passes are valid for unlimited rides for the month shown, and through the first business day of the following month. Monthly passes can be purchased on the Connect Card website or on the Transit Connect app, but not onboard a Yolobus.
- **[yolobus-fares#9]** _Monthly Passes_

  Yolobus-only and Sacramento Regional Transit Monthly Passes are valid for unlimited rides for the month shown, and through the first business day of the following month. Monthly passes can be purchased on the Connect Card website or the Transit Connect mobile app, but not onboard a Yolobus.
- **[yolobus-fares#3]** _ADA Paratransit (“Yolobus Special”) Fares_

  ADA-eligible riders can use ADA paratransit (“Yolobus Special”) service. The Yolobus Special fares vary by distance (local vs. intercity).
  ADA Paratransit ("Yolobus Special") Products
  ADA Paratransit Local Fare | $4.00
  ADA Paratransit Intercity Fare | $4.50
  ADA Paratransit Premium Fare* | $6.00
  * Premium service is available to medical facilities only in portions of Sacramento and Vacaville that are beyond ¾ miles of Yolobus fixed routes. Destinations within ¾ mile of a Yolobus fixed route are charged the Local or Intercity fare, whichever is applicable
  How to purchase a ticket or a pass
  More information on how to purchase a ticket or a pass is available here

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 22. `tl-001` · suite: stretch_tagalog · lang: tl
<sub>answer_sha256: `349c91837360d2d1ef62cfe43e82a6cea56bc47cb1cd0ebf5cb3175447ff22ad`</sub>

**Question:** Magkano ang pamasahe sa MST kung babayad ako ng cash?

**Assistant answer:**

> Batay sa mga patakaran na inilathala noong 2026-06-12:
> 
> Kung babayad ka ng cash sa MST, ang pamasahe ay:
> 
> **Regular Fixed Route:**
> - Single Ride (valid 2 hours): $2.00 [doc:mst-fares]
> 
> **Discount Fixed Route:**
> - Single Ride (valid 2 hours): $1.00 [doc:mst-fares]
> 
> Ang discount fare ay available para sa mga taong 18 taong gulang o mas bata, 65 taong gulang o mas matanda, mga indibidwal na may kapansanan, mga may Medicare Card, mga beterano, at iba pang qualified riders. Kailangan mong ipakita ang patunay ng iyong eligibilidad sa driver kapag sumasakay. [doc:mst-fares]
> 
> Kung walang exact fare, ang farebox ay magbibigay ng credit para sa susunod mong MST trips. [doc:mst-fares]

**Retrieved passages:**

- **[mst-fares#0]** _Fares Overview_

  MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day, $20 per week, or $70 per month no matter how many times you ride in a day—as long as you pay with the same contactless debit/credit card or mobile wallet each time you ride. (visit Benefits page for details about tapping and discounted fares)
  Transfers If you need to transfer and are paying with cash or a GoPass, request a 2-hour pass from your driver when boarding your first bus.
  If you are paying with a contactless payment method, simply tap it on the contactless payment reader on each bus you board and you will only be charged $2 no matter how many times you tap and transfer within that 2-hour window.
- **[mst-veterans-resource#1]** _Bus Pass for Veterans_

  Monterey-Salinas Transit (MST) is partnering with veterans’ services organizations to select individuals needing transportation services. Selected veterans are provided free MST passes to honor their service to our country. For information email [email protected]
  Veterans Group Travel Training
  Monterey-Salinas Transit (MST) provides free fixed-route travel training to teach interested individuals how to safely and independently ride the MST bus system.
  Travel Training
- **[mst-fares#1]** _— Cash / GoPass / GoCard_

  Single Ride 2 hours Cash or GoCard
  Daily GoPass valid until 2:00 AM
  Weekly GoPass (7 Days)
  Monthly GoPass (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
  — Contactless Payment
  Single Ride 2 hours
  Daily valid until 2:00 AM
  Weekly (7 Days)
  Monthly (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
- **[mst-fares#3]** _Contactless Payment Methods_

  MST now accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets. At this time, accepted mobile wallets include: Apple Pay , Google Pay, Samsung Pay , and Fitbit Pay . For more information, visit our Contactless Fare Payment Fast Facts and FAQs .
  Cash
  Cash is inserted into the farebox. Exact fare is not required. If you do not have exact fare the farebox will issue credit for use on future MST trips.
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares-es#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---

### 23. `tl-002` · suite: stretch_tagalog · lang: tl
<sub>answer_sha256: `a22bd642c9a5f401ca40fe05f516bcf7051815e1c152ca096875479ec059c144`</sub>

**Question:** Magkano ang buwanang GoPass na may diskwento sa MST?

**Assistant answer:**

> Ang buwanang GoPass na may diskwento sa MST ay **$35.00** para sa Discount Fixed Route. [doc:mst-fares]
> 
> Ang published criteria para sa diskwento ay kinabibilangan ng: 18 taong gulang at mas bata, 65 taong gulang at mas matanda, mga indibidwal na may kapansanan, mga may Medicare Card, mga Beterano at spouse/caregiver ng Beterano, at mga may MST RIDES Paratransit Eligibility card. [doc:mst-fares]
> 
> Kailangan mong magpakita ng patunay ng edad, aktibong MST Courtesy Card, o Medicare Card kapag sumasakay. [doc:mst-fares]
> 
> Batay sa mga patakaran na inilathala noong 2026-06-12. Irekumenda naming kumpirmahin sa MST ang anumang pagbabago sa presyo o patakaran.

**Retrieved passages:**

- **[mst-fares#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares-es#10]** _Group Discount Program_

  The Monterey-Salinas Transit Group Discount Program provides 31 Day MST GoPasses at reduced costs. Based on the number of GoPasses your organization orders, participants can enjoy substantially reduced costs – making riding the bus an outstanding benefit. For more information, or to enroll, please complete the application or call 1-888-MST-BUS1 (1-888-678-2871).
  5-20 GoPasses – 25% Discount
  Monthly GoPass – $52.00
  21+ GoPasses – 35% Discount
  Monthly GoPass – $45.00
  Enrolled Group Discount participants, please click here to place your order .
  Group discount rates for residential complexes serving the elderly and disabled
  Participating residential complexes, please click here to place your order .
  Residential housing complexes serving the elderly and disabled have the ability to pre-purchase monthly bus passes in bulk at a 50% discount should the complex pay upfront for a full year of passes for all of their residents. This would make the Monthly GoPass $210.00 per resident annually ($17.50 per month).
  New! MST’s board of directors recently approved an expansion of the residential complex rate to include developments where 50% or more of the residents meet qualifying criteria of low-income, farm worker and/or other similar disadvantaged group. This program allows residential housing complexes to pre-purchase monthly bus passes in bulk at a 50% discount should the housing complex pay for a full year of passes for all of their units. This arrangement makes the GoPass $210.00 per unit annually or $17.50 per month.
- **[mst-fares#1]** _— Cash / GoPass / GoCard_

  Single Ride 2 hours Cash or GoCard
  Daily GoPass valid until 2:00 AM
  Weekly GoPass (7 Days)
  Monthly GoPass (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
  — Contactless Payment
  Single Ride 2 hours
  Daily valid until 2:00 AM
  Weekly (7 Days)
  Monthly (31 Days)
  Regular Fixed Route
  $ 2.00
  $ 6.00
  $ 20.00
  $ 70.00
  Discount Fixed Route
  $ 1.00
  $ 3.00
  $ 10.00
  $ 35.00
- **[mst-fares-benefits#0]** _Get Your Discount when You Pay Your Fare with a Contactless Card_

  MST has partnered with Cal-ITP —the California Integrated Travel Project, an initiative of the State of California—to offer a way to quickly verify your eligibility for a discount and then link that discount to your contactless-enabled bank card. (Visa, Mastercard, Discover, American Express). You’ll get your discount every time you pay with that same contactless card.
  Right now, the Cal-ITP Benefits website will work for anyone who meets the following criteria:
  Is eligible for the Medicare, Senior (age 65+), or Veteran Discount programs: Has a Medicare or Login.gov account or has an active MST Courtesy card Has a contactless credit or debit card
  Has a Medicare or Login.gov account or has an active MST Courtesy card
  Has a contactless credit or debit card
  “Contactless” means a card you can tap instead of swipe to pay. Look for this symbol on the front or back of your card:
  Symbol description: four, curved lines facing sideways, similar to a wifi symbol.
  You can get in touch with your bank card provider to request a contactless card—or you can order a contactless-enabled card from one of the providers in our Contactless Fare Payment guide.
  The process to verify your eligibility for a discount and add it to your contactless card is completely online and secure! See how it works for MST’s Senior Discount, Veteran Discount, Medicare Discount and Courtesy Card holders.
  Have your ID and contactless bank card ready and get started now on the website below.
  To continue, please click the below link to exit MST’s website and be redirected to the State of California’s eligibility verification website, Cal-ITP Benefits.
  Go to Cal-ITP Benefits
- **[mst-fares#2]** _Discount Eligibility_

  Discount fare for:
  18 years and under
  65 years and older (see also: Benefits )
  Individuals with disabilities
  Medicare Card holders (see also: Benefits )
  Veterans (see also: Benefits ), Veteran’s spouse/caregiver
  MST RIDES Paratransit Eligibility card holders
  Special Humanitarian Parole
  Proof of age, an MST Courtesy Card ( English application / Spanish application ) or Medicare Card is required upon boarding. MST also honors discount courtesy cards issued by other transit systems.
  Veterans may obtain an MST Courtesy Card ( English application / Spanish application ) with the following proof of service:
  DD Form 214
  DD Form 256N
  US Department of Veterans Affairs ID card
  Veterans of Foreign Wars ID card
  Membership card from the American Legion
  Disabled American Veterans ID card
  CA driver license or ID card with “veteran” designation”
  Contactless payment methods , cash, GoPasses, and GoCards can be used on all fixed route MST buses. Please have your payment ready when you board. The coach operator may ask to see proof of eligibility for a discounted fare (Individuals 18 years and under, 65 years and older, individuals with disabilities, and Medicare Card holders).
  GoPasses and GoCards can be purchased directly from MST, online, by phone or in person any MST customer service location. GoPasses may also be purchased at pass outlets throughout the county. See the pass outlets page for specific locations.
- **[mst-fares#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares-es#9]** _Pass Outlets_

  For your convenience, MST GoPasses are available at MST Customer Service locations and pass outlet locations throughout your community.
  Carmel Carmel Drug Store, San Carlos & Ocean
  Gonzales City Hall, 147 Fourth St.
  Greenfield City Hall, 45 El Camino Real
  King City City Hall, 212 S. Vanderhurst Ave.
  Marina Marina Transit Exchange, 280 Reservation Road
  Monterey MST Bus Stop Shop, 201 Pearl Street
  Salinas Costco, 1339 N. Davis Rd. (Regular GoPass only) Salinas Transit Center, 110 Salinas Street
  Sand City Costco, 801 Tioga Ave. (Regular GoPass only)
  Seaside Mal’s Market, 1264 Noche Buena
  Gopasses and Gocards may also be purchased anytime online at the MST Store , by phone at 1-888-MST-BUS1 (1-888-678-2871) or by mail by sending your check, credit card information or money order to: GoPasses, 19 Upper Ragsdale Drive, Suite 200, Monterey, CA 93940.
- **[mst-fares#0]** _Fares Overview_

  MST accepts Visa, Mastercard, Discover, and American Express contactless-enabled bank cards and mobile wallets onboard all buses – and with fare capping, you will never be charged more than $6 per day, $20 per week, or $70 per month no matter how many times you ride in a day—as long as you pay with the same contactless debit/credit card or mobile wallet each time you ride. (visit Benefits page for details about tapping and discounted fares)
  Transfers If you need to transfer and are paying with cash or a GoPass, request a 2-hour pass from your driver when boarding your first bus.
  If you are paying with a contactless payment method, simply tap it on the contactless payment reader on each bus you board and you will only be charged $2 no matter how many times you tap and transfer within that 2-hour window.

**Grounded?**  ( ) yes   ( ) no
**Note:** ______________________________________________

---
