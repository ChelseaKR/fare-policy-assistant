"""Tests for the shared eval-variance statistics (evals/stats.py)."""

from __future__ import annotations

import math

import pytest

from evals.stats import mcnemar_exact_p, wilson_interval

# ── Wilson interval ──────────────────────────────────────────────────────────


def test_wilson_symmetric_midpoint_known_value():
    # 50/100 at 95%: the textbook Wilson band is ~[0.404, 0.596], centered on 0.5.
    low, high = wilson_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-3)
    assert high == pytest.approx(0.5962, abs=1e-3)
    assert (low + high) / 2 == pytest.approx(0.5, abs=1e-9)


def test_wilson_zero_successes_floor_is_clamped_to_zero():
    # Unlike the normal approximation, Wilson stays inside [0, 1] at the extreme.
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert 0.0 < high < 1.0
    assert high == pytest.approx(0.2775, abs=1e-3)


def test_wilson_all_successes_ceiling_is_clamped_to_one():
    low, high = wilson_interval(10, 10)
    assert high == pytest.approx(1.0, abs=1e-9)
    assert 0.0 < low < 1.0


def test_wilson_no_trials_is_a_degenerate_zero_band():
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_wilson_wider_z_gives_wider_band():
    narrow = wilson_interval(30, 100, z=1.0)
    wide = wilson_interval(30, 100, z=2.5)
    assert wide[0] < narrow[0]
    assert wide[1] > narrow[1]


def test_wilson_rejects_out_of_range_successes():
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


# ── McNemar exact p-value ────────────────────────────────────────────────────


def test_mcnemar_no_discordant_pairs_is_p_one():
    # Nothing flipped either way: no evidence of a difference.
    assert mcnemar_exact_p(0, 0) == 1.0


def test_mcnemar_all_flips_one_direction_small():
    # b+c=5, all in one direction: 2 * C(5,0)/2^5 = 2/32 = 0.0625.
    assert mcnemar_exact_p(0, 5) == pytest.approx(0.0625)


def test_mcnemar_is_symmetric_in_its_arguments():
    assert mcnemar_exact_p(5, 0) == mcnemar_exact_p(0, 5)
    assert mcnemar_exact_p(2, 8) == mcnemar_exact_p(8, 2)


def test_mcnemar_balanced_flips_cannot_reject():
    # b == c: the two-sided tail doubles past 1 and is capped.
    assert mcnemar_exact_p(3, 3) == 1.0
    assert mcnemar_exact_p(1, 1) == 1.0


def test_mcnemar_single_flip_is_p_one():
    # One discordant pair: 2 * C(1,0)/2 = 1.0. Never significant.
    assert mcnemar_exact_p(1, 0) == 1.0


def test_mcnemar_matches_direct_binomial_sum():
    b, c = 2, 8
    n = b + c
    expected = min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n)
    assert mcnemar_exact_p(b, c) == pytest.approx(expected)


def test_mcnemar_rejects_negative_counts():
    with pytest.raises(ValueError):
        mcnemar_exact_p(-1, 3)
