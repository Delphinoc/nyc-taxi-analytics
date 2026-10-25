"""Tests for the deck generator's number checks and formatting helpers."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import build_deck  # noqa: E402


def test_signed_uses_true_minus_sign():
    assert build_deck.signed(-1.14) == "−1.1%"
    assert build_deck.signed(11.15) == "+11.2%"


def stable_examples() -> pd.DataFrame:
    rows = [(name, cluster, False) for cluster, names in build_deck.EXAMPLE_ZONES.items()
            for name in names]
    return pd.DataFrame(rows, columns=["zone", "equal_feature", "moved"])


def test_verify_rejects_an_example_zone_that_moves_under_reweighting():
    numbers = valid_numbers()
    build_deck.verify(numbers)
    numbers["weighting"].loc[0, "moved"] = True
    with pytest.raises(AssertionError, match="not a stable member"):
        build_deck.verify(numbers)


def valid_numbers() -> dict:
    numbers = {key: expected for key, (expected, _) in build_deck.EXPECTED.items()}
    numbers["segment_shares"] = {c: share for c, (share, _) in build_deck.EXPECTED_SEGMENTS.items()}
    numbers["segment_zones"] = {c: zones for c, (_, zones) in build_deck.EXPECTED_SEGMENTS.items()}
    numbers["midtown_avg"] = [0] * 8 + [-301.0] + [0] * 11 + [396.0] + [0] * 3
    numbers["below_threshold_pct"] = round(
        100 - sum(numbers["segment_shares"].values()) - numbers["airport_pct"], 6)
    numbers["weighting"] = stable_examples()
    return numbers


def test_verify_rejects_a_number_that_disagrees_with_the_reports():
    numbers = {key: expected for key, (expected, _) in build_deck.EXPECTED.items()}
    numbers["weighting"] = stable_examples()
    numbers["segment_shares"] = {c: share for c, (share, _) in build_deck.EXPECTED_SEGMENTS.items()}
    numbers["segment_zones"] = {c: zones for c, (_, zones) in build_deck.EXPECTED_SEGMENTS.items()}
    numbers["segment_shares"][0], numbers["segment_shares"][1] = 42.98, 35.70
    numbers["midtown_avg"] = [0] * 8 + [-301.0] + [0] * 11 + [396.0] + [0] * 3
    # Shares must sum to 100 with airports and below-threshold zones.
    numbers["below_threshold_pct"] = 100 - sum(numbers["segment_shares"].values()) - numbers["airport_pct"]
    numbers["below_threshold_pct"] = round(numbers["below_threshold_pct"], 6)
    log = build_deck.verify(numbers)
    assert any("78.7" in line for line in log)
    numbers["fare_charge_pm_pct"] = 12.0  # a stale or wrong figure must stop the build
    with pytest.raises(AssertionError):
        build_deck.verify(numbers)
