# Manual accessibility walkthrough

The automated gates (`web/a11y.py` in CI, blocking pa11y/axe) check structure and
computed contrast. They cannot check the lived experience: whether the page is
actually operable and comprehensible with a screen reader and a keyboard. This
file is the record of that human step. Until the result table below is filled in
by a person, the demo is accessibility-reviewed by automation only, and the
README and model card say so.

## How to run it

Test the deployed demo (or `web/index.html` served locally) with at least one
screen reader and keyboard-only, at default zoom and at 400%. Record the date,
the tool and version, and the outcome of each item. File a follow-up issue for
anything that fails and link it here.

Recommended coverage: NVDA + Firefox (Windows) and VoiceOver + Safari (macOS or
iOS). One desktop and one mobile pass is the minimum for a phone-first civic
audience.

## Checklist

Each item is pass / fail / not-tested with a note. Items marked (auto) are also
covered by the static gate; they are listed so the manual pass confirms the
automation did not miss the real behavior.

### Keyboard

- [ ] Every control (text box, Ask, examples, text-size A/A+/A++, high contrast,
      Yes/No feedback, Start over) is reachable and operable by Tab and Enter.
- [ ] Focus order is logical and visible (the focus outline is the 3px ring).
- [ ] No keyboard trap anywhere in the form or transcript.
- [ ] After submitting a question, focus moves to the new answer turn so a
      keyboard user lands on the response (the page sets `tabindex=-1` and calls
      `focus()` on each turn; confirm it actually lands).

### Screen reader

- [ ] The page title and the single `h1` are announced; heading order (h1 then
      the card h2s) reads sensibly. (auto: structure)
- [ ] The "What it will not do" list is announced before the input, so the
      limits are heard, not skipped.
- [ ] The status line ("Looking through the published policies…", errors) is
      announced through the polite live region without stealing focus. (auto:
      `role=status`, `aria-live=polite` present)
- [ ] A new answer is announced or is reachable immediately after it arrives.
- [ ] Citations read as usable source references, not a wall of brackets: each
      "AGENCY: Title (fetched DATE)" link is announced with its agency and
      title. (this is the R1a-3 item the static gate cannot verify)
- [ ] The "Based on policies published as of DATE" line and the "Fetched N days
      ago" staleness note are announced and understandable.
- [ ] The feedback control announces its purpose ("Was this helpful?", Yes/No
      with `aria-label`).
- [ ] Spanish answers carry `lang="es"` so the screen reader switches voice.

### Low vision / zoom / contrast

- [ ] Text-size controls (A / A+ / A++) visibly scale the page and persist on
      reload; the pressed state is announced (`aria-pressed`).
- [ ] High-contrast toggle increases contrast and persists; pressed state
      announced.
- [ ] The page reflows without horizontal scrolling at 400% zoom (1.4.10). (auto:
      zoom not disabled)
- [ ] Contrast meets AA in both the default and high-contrast themes. (auto:
      blocking axe/pa11y)
- [ ] Target sizes are comfortable on a phone. (auto: 24px minimum in CSS)

## Code-level pre-audit — 2026-07-11 (by source inspection, not a screen reader)

This section is a head start for the human pass, not a substitute for it. It
records what could be confirmed by reading `web/index.html` and running the
static gate (`python -m web.a11y`, green on this commit). Every row a real
screen reader must judge — whether an announcement is actually heard and makes
sense — is left explicitly **for the human pass**. Do not promote any "needs
human" row to pass without an actual screen-reader session.

**Keyboard**

- Verified by code: every control is a native `<button>`, `<textarea>`, or
  `<a>` — focusable and Enter/Space-operable without extra ARIA. Focus order
  follows DOM order, which matches the visual order (banner → h1 → display
  settings → "will not do" → ask form → examples → status → transcript). The
  focus ring is `outline: 3px solid #1d4ed8` via `:focus-visible`. No script
  installs a focus trap. On each new answer the code sets `tabindex=-1` on the
  turn and calls `focus()`, so a keyboard user is moved onto the response.
- Needs human pass: confirm the focus *visibly* lands on the new turn and that
  the ring is perceivable at 400% and in high-contrast mode.

**Screen reader**

- Verified by code (structure): one `<h1>`, card `<h2>`s, structured-answer
  `<h3>`s — no skipped levels. The "What it will not do" `<section>` precedes
  the ask form in the DOM. Status is `role=status aria-live=polite`. Answer
  turns carry `lang` (`ans.setAttribute("lang", data.language)`) so a Spanish
  answer can switch voice. Feedback buttons have `aria-label` ("Yes, helpful" /
  "No, not helpful") beside a "Was this helpful?" label. Citations render as
  `<a>AGENCY: Title</a> (fetched DATE)` inside a `<ul>`.
- Minor finding (not a failure): the "Sources" caption is a `<strong>`, not a
  heading, so it is not a screen-reader heading-nav target. It sits inside the
  focused turn, so it is still reachable; consider an `<h3>` if the human pass
  finds the sources hard to locate.
- Needs human pass: whether the moved focus actually announces the new answer;
  whether the citation list reads as usable references rather than a bracket
  wall; whether the polite live region announces status without stealing focus;
  and whether the Spanish `lang` actually flips the voice.

**Low vision / zoom / contrast**

- Verified by code: the page is rem/em-based with `html.tsize-large` (112.5%)
  and `html.tsize-xlarge` (125%), toggled with `aria-pressed` and persisted in
  `localStorage`; high contrast toggles `body.contrast` (which only deepens
  colours), also `aria-pressed` and persisted. Viewport allows zoom
  (no `user-scalable=no`). Target sizes: primary controls `min-height: 2.5rem`
  (40px), secondary `1.75rem` (28px) — both ≥ the 24px 2.5.8 minimum. Static
  contrast is covered by the blocking axe/pa11y gate.
- Needs human pass: reflow with no horizontal scroll at 400%, and a subjective
  contrast/readability check in both themes on a real device.

## Result log

| Date | Tool + version | Platform | Pass / fail summary | Follow-ups |
|---|---|---|---|---|
| 2026-07-11 | source inspection + `web.a11y` static gate | n/a (code review) | Structure, keyboard wiring, ARIA, `lang`, target sizes, persistence all correct by inspection; static gate green. Screen-reader *listening* items not yet performed. | Human SR pass still required (rows above) |
| _pending_ | NVDA/Firefox or VoiceOver/Safari | desktop + mobile | not yet performed | |

Until a row here records a real screen-reader pass, do not present the demo as
production-ready for accessibility.
