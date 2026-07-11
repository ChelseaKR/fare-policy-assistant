"""Small, dependency-free statistics for eval variance.

Two pure functions, stdlib `math` only, so both the runner (`--replicates`)
and the paired comparison (`evals/compare.py`) can share them:

* `wilson_interval` — a Wilson score confidence interval for a binomial pass
  rate. Better than the normal approximation at the extremes (0% / 100%) and
  on the small suites this harness runs, where a naive ±z·√(p̂q̂/n) can escape
  [0, 1] entirely.
* `mcnemar_exact_p` — the exact (binomial) two-sided McNemar p-value for a
  paired A/B comparison, from the two discordant counts. Exact rather than the
  χ² approximation because the discordant total is usually tiny (a handful of
  cases flip), which is exactly where the approximation is worst.
"""

from __future__ import annotations

import math

# z for a two-sided 95% interval. Kept as a default so callers can widen or
# narrow the band without re-deriving the algebra.
Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for `successes` out of `n` Bernoulli trials.

    Returns `(low, high)` as fractions in [0, 1]. With no trials the interval
    is undefined; we return `(0.0, 0.0)` so callers can render a placeholder
    rather than crash. The result is always clamped to [0, 1].
    """
    if n <= 0:
        return (0.0, 0.0)
    if successes < 0 or successes > n:
        raise ValueError(f"successes {successes} out of range for n {n}")
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    low = center - margin
    high = center + margin
    return (max(0.0, low), min(1.0, high))


def mcnemar_exact_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value from the two discordant counts.

    `b` and `c` are the counts of cases that flipped one way and the other
    (e.g. A-pass/B-fail and A-fail/B-pass). Under the null that a flip is
    equally likely in either direction, the smaller count follows a
    Binomial(b + c, 0.5); the two-sided p-value is twice the lower tail,
    capped at 1.0. With no discordant pairs there is nothing to reject, so the
    p-value is 1.0.
    """
    if b < 0 or c < 0:
        raise ValueError("discordant counts must be non-negative")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)
