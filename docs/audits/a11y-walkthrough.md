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

## Result log

| Date | Tool + version | Platform | Pass / fail summary | Follow-ups |
|---|---|---|---|---|
| _pending_ | | | not yet performed | |

Until a row here records a real pass, do not present the demo as
production-ready for accessibility.
