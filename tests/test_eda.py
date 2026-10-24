"""Tests for the Phase 3 metric definitions and aggregation helpers."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import eda_aggregates as agg  # noqa: E402
import eda_metrics as m  # noqa: E402


def test_day_types_and_counts_for_january_2025():
    dates = pd.Series(pd.to_datetime(["2025-01-01", "2025-01-03", "2025-01-04", "2025-01-05",
                                      "2025-01-20", "2025-01-21"]))
    assert m.day_type(dates).tolist() == ["holiday", "weekday", "weekend", "weekend",
                                          "holiday", "weekday"]
    assert m.days_per_type() == {"weekday": 21, "weekend": 8, "holiday": 2}


def test_hour_periods_cover_every_hour_once():
    periods = m.hour_period(pd.Series(range(24)))
    assert periods.notna().all()
    assert periods.value_counts().sum() == 24
    assert periods[16] == periods[19] == "pm rush (16-19)"
    assert periods[20] == "evening (20-23)"


def flagged_trip(**overrides):
    base = {
        "tpep_pickup_datetime": pd.Timestamp("2025-01-15 10:00"),
        "tpep_dropoff_datetime": pd.Timestamp("2025-01-15 10:20"),
        "DOLocationID": 161,
        **{flag: False for flag in m.DROPOFF_INVALID_FLAGS},
    }
    return {**base, **overrides}


def test_dropoff_eligibility_is_validated_independently():
    trips = pd.DataFrame([
        flagged_trip(),
        # Pickup in January, drop-off in February: drop-off end is out of period.
        flagged_trip(tpep_pickup_datetime=pd.Timestamp("2025-01-31 23:50"),
                     tpep_dropoff_datetime=pd.Timestamp("2025-02-01 00:05")),
        # Pickup on 31 Dec, drop-off on 1 Jan: drop-off end is in period.
        flagged_trip(tpep_pickup_datetime=pd.Timestamp("2024-12-31 23:50"),
                     tpep_dropoff_datetime=pd.Timestamp("2025-01-01 00:05")),
        flagged_trip(DOLocationID=264),
        flagged_trip(DOLocationID=265),
        flagged_trip(dq_zero_duration=True),
        flagged_trip(dq_duration_over_12h=True),
        flagged_trip(dq_negative_total_amount=True),
    ])
    assert m.eligible_dropoff(trips).tolist() == [True, False, True, False, False, False, False,
                                                 False]


def test_imbalance_index_bounds_and_minimum_volume():
    index = m.imbalance_index([100, 0, 60, 20, 0], [0, 100, 40, 20, 0], min_trip_ends=50)
    assert index[0] == 1.0 and index[1] == -1.0
    assert index[2] == pytest.approx(0.2)
    assert np.isnan(index[3])  # 40 trip ends is below the 50 minimum
    assert np.isnan(index[4])  # zero denominator never divides


def test_imbalance_index_threshold_is_inclusive():
    index = m.imbalance_index([60, 59], [40, 40], min_trip_ends=100)
    assert index[0] == pytest.approx(0.2)
    assert np.isnan(index[1])


def test_flow_direction_labels():
    labels = m.flow_direction(pd.Series([5, -3, 0]))
    assert labels.tolist() == ["net outbound", "net inbound", "balanced"]


def test_persistence_ignores_small_days_and_needs_enough_days():
    rows = []
    # Cell A: 6 qualifying days, 5 outbound and 1 inbound, plus 1 tiny day.
    for day, (p, d) in enumerate([(20, 5)] * 5 + [(5, 20), (2, 1)]):
        rows.append({"cell": "A", "date": day, "pickups": p, "dropoffs": d})
    # Cell B: only 3 qualifying days.
    for day in range(3):
        rows.append({"cell": "B", "date": day, "pickups": 20, "dropoffs": 5})
    result = m.persistence(pd.DataFrame(rows), ["cell"]).set_index("cell")
    assert result.loc["A", "qualifying_days"] == 6
    assert result.loc["A", "same_sign_share"] == pytest.approx(5 / 6)
    assert result.loc["B", "qualifying_days"] == 3
    assert np.isnan(result.loc["B", "same_sign_share"])


def test_standardized_mean_removes_mix_differences():
    # Peak trips are mostly long, off-peak mostly short; within each stratum
    # the fare is identical, so the standardised means must be equal.
    frame = pd.DataFrame({
        "period": ["peak"] * 10 + ["off"] * 10,
        "band": ["long"] * 8 + ["short"] * 2 + ["long"] * 2 + ["short"] * 8,
        "fare": [30.0] * 8 + [10.0] * 2 + [30.0] * 2 + [10.0] * 8,
    })
    result = m.standardized_mean(frame, "fare", "period", ["band"]).set_index("period")
    assert result.loc["peak", "raw_mean"] == pytest.approx(26.0)
    assert result.loc["off", "raw_mean"] == pytest.approx(14.0)
    assert result.loc["peak", "standardized_mean"] == pytest.approx(20.0)
    assert result.loc["off", "standardized_mean"] == pytest.approx(20.0)
    assert result["weight_covered"].tolist() == [1.0, 1.0]


def test_standardized_mean_uses_common_support_and_weights():
    # Stratum "rare" exists only in the peak group; it must be dropped for
    # every group, and weights must come from the pooled rows in kept strata.
    frame = pd.DataFrame({
        "period": ["peak"] * 4 + ["off"] * 4 + ["peak"],
        "band": ["a", "a", "b", "b", "a", "b", "b", "b", "rare"],
        "fare": [10.0, 10.0, 20.0, 20.0, 12.0, 22.0, 22.0, 22.0, 99.0],
    })
    result = m.standardized_mean(frame, "fare", "period", ["band"]).set_index("period")
    # Pooled kept rows: a = 3, b = 5 -> weights 3/8 and 5/8.
    assert result.loc["peak", "standardized_mean"] == pytest.approx(10 * 3 / 8 + 20 * 5 / 8)
    assert result.loc["off", "standardized_mean"] == pytest.approx(12 * 3 / 8 + 22 * 5 / 8)
    assert result.loc["peak", "weight_covered"] == pytest.approx(8 / 9)
    assert result.loc["peak", "strata_kept"] == 2 and result.loc["peak", "strata_total"] == 3


def test_standardized_mean_min_cell_drops_sparse_strata():
    frame = pd.DataFrame({
        "period": ["peak"] * 3 + ["off"] * 3,
        "band": ["a", "a", "b", "a", "a", "a"],
        "fare": [10.0, 10.0, 50.0, 12.0, 12.0, 12.0],
    })
    # Band b has 1 peak trip and 0 off-peak trips: dropped at any min_cell.
    result = m.standardized_mean(frame, "fare", "period", ["band"], min_cell=2).set_index("period")
    assert result.loc["peak", "standardized_mean"] == pytest.approx(10.0)
    assert result.loc["peak", "strata_kept"] == 1


def test_persistence_counts_zero_net_days_against_persistence():
    daily = pd.DataFrame({"cell": ["A"] * 6, "date": range(6),
                          "pickups": [20, 20, 20, 20, 20, 10],
                          "dropoffs": [5, 5, 5, 5, 5, 10]})
    result = m.persistence(daily, ["cell"]).set_index("cell")
    assert result.loc["A", "zero_net_days"] == 1
    assert result.loc["A", "same_sign_share"] == pytest.approx(5 / 6)


def test_persistence_thresholds_are_parameters():
    daily = pd.DataFrame({"cell": ["A"] * 6, "date": range(6),
                          "pickups": [40] * 3 + [8] * 3, "dropoffs": [10] * 3 + [12] * 3})
    strict = m.persistence(daily, ["cell"], min_daily_trip_ends=25, min_days=3).set_index("cell")
    assert strict.loc["A", "qualifying_days"] == 3
    assert strict.loc["A", "same_sign_share"] == pytest.approx(1.0)
    too_few = m.persistence(daily, ["cell"], min_daily_trip_ends=25, min_days=4)
    assert too_few["same_sign_share"].isna().all()


def test_zone_hour_counts_count_each_end_with_its_own_validity():
    trips = pd.DataFrame({
        "PULocationID": [161, 161, 236],
        "DOLocationID": [236, 236, 161],
        "pu_date": pd.to_datetime(["2025-01-15"] * 3),
        "do_date": pd.to_datetime(["2025-01-15"] * 3),
        "pu_day_type": ["weekday"] * 3,
        "do_day_type": ["weekday"] * 3,
        "pu_hour": [8, 8, 18],
        "do_hour": [9, 9, 18],
    })
    pickup_ok = pd.Series([True, True, False])
    dropoff_ok = pd.Series([True, False, True])
    counts = agg.zone_hour_counts(trips, pickup_ok, dropoff_ok).set_index(["zone_id", "hour"])
    assert counts.loc[(161, 8), "pickups"] == 2
    assert counts.loc[(236, 9), "dropoffs"] == 1  # second trip's drop-off is not valid
    assert counts.loc[(161, 18), "dropoffs"] == 1  # third trip's drop-off counts alone
    assert (236, 18) not in counts.index  # its pickup was not valid
    assert counts[["pickups", "dropoffs"]].sum().tolist() == [2, 2]


def test_spearman_matches_rank_definition():
    a = pd.Series([1, 2, 3, 4, 5])
    assert agg.spearman(a, a ** 3) == pytest.approx(1.0)
    assert agg.spearman(a, -a) == pytest.approx(-1.0)
