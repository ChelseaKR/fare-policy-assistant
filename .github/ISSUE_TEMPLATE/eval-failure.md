---
name: Eval failure
about: A case in evals/suites is failing or should be added
title: "[eval] <case-id> (<suite>): <one line>"
labels: bug
---

## Case
- **Suite:**
- **Case id:**
- **Expected behavior:** answer / partial / refuse-and-redirect
- **Question / conversation:**

## Failing check(s)
<!-- e.g. judge/groundedness, required_facts_present, as_of_disclosure -->

## Root cause
<!-- generation faithfulness, retrieval miss, judge strictness, guard over-block -->

## Proposed remediation
<!-- the smallest change that fixes it; name the file(s) -->

## Validation
<!-- Most generation/judge fixes need a live `make eval` with a green regression
gate. State whether this can be validated offline (deterministic checks only)
or needs credentials. -->

## Refs
<!-- EVALS.md, docs/research, prompts/, src/assistant/ -->
