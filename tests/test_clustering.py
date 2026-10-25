"""Tests for Phase 4 zone features and clustering logic, on synthetic data."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import zone_clustering as zc  # noqa: E402
import zone_features as zf  # noqa: E402

WEEKDAY = pd.Timestamp("2025-01-15")  # Wednesday
WEEKDAY_2 = pd.Timestamp("2025-01-16")
WEEKEND = pd.Timestamp("2025-01-18")  # Saturday


def base_tables():
    """Two zones: zone 1 morning-outbound, zone 2 evening-outbound."""
    rows = []
    for date, kind in [(WEEKDAY, "weekday"), (WEEKDAY_2, "weekday"), (WEEKEND, "weekend")]:
        rows += [
            {"zone_id": 1, "date": date, "day_type": kind, "hour": 8, "pickups": 80, "dropoffs": 20},
            {"zone_id": 1, "date": date, "day_type": kind, "hour": 18, "pickups": 20, "dropoffs": 80},
            {"zone_id": 2, "date": date, "day_type": kind, "hour": 8, "pickups": 10, "dropoffs": 90},
            {"zone_id": 2, "date": date, "day_type": kind, "hour": 18, "pickups": 90, "dropoffs": 10},
        ]
    counts = pd.DataFrame(rows)
    totals = pd.DataFrame({
        "zone_id": [1, 1, 1, 2, 2, 2], "date": [WEEKDAY, WEEKDAY_2, WEEKEND] * 2,
        "pickups": [100] * 6, "flex_pickups": [10, 10, 10, 50, 50, 50],
        "fare_eligible": [90, 90, 90, 50, 50, 50], "distance_eligible": [100] * 6,
    })
    hist = []
    for zone, distance_bin in [(1, 15), (2, 40)]:  # 1.5 mi and 4.0 mi bins
        for date in [WEEKDAY, WEEKDAY_2, WEEKEND]:
            hist += [
                {"zone_id": zone, "date": date, "bin": distance_bin, "trips": 10, "metric": "distance_all"},
                {"zone_id": zone, "date": date, "bin": distance_bin, "trips": 10,
                 "metric": "distance_metered"},
                {"zone_id": zone, "date": date, "bin": 40, "trips": 10, "metric": "duration_all"},
                {"zone_id": zone, "date": date, "bin": 120, "trips": 10,
                 "metric": "fare_per_mile_metered"},
            ]
    return {"zone_date_hour_counts": counts, "zone_date_totals": totals,
            "zone_date_histograms": pd.DataFrame(hist)}


def test_histogram_median_interpolates_within_bin():
    hist = pd.DataFrame({"zone_id": [1, 1, 1], "bin": [0, 1, 2], "weight": [1.0, 2.0, 1.0]})
    # Half of 4 = 2 falls halfway through bin 1 -> 1.5 bins -> 0.15 with width 0.1.
    assert zf.weighted_median_from_histogram(hist, 0.1)[1] == pytest.approx(0.15)


def test_features_shares_imbalance_and_ratios():
    f = zf.compute_features(base_tables())
    assert f.loc[1, "weekday_share_morning"] == pytest.approx(0.8)
    assert f.loc[1, "weekday_share_pm_rush"] == pytest.approx(0.2)
    assert f.loc[1, "imbalance_morning"] == pytest.approx((160 - 40) / 200)
    assert f.loc[2, "imbalance_pm_rush"] == pytest.approx((180 - 20) / 200)
    # Weekend per-day rate = weekday per-day rate here.
    assert f.loc[1, "weekend_to_weekday_ratio"] == pytest.approx(1.0)
    assert f.loc[2, "flex_share"] == pytest.approx(0.5)
    assert f.loc[1, "fare_sample_coverage"] == pytest.approx(0.9)
    assert f.loc[1, "median_distance_mi"] == pytest.approx(1.55)  # middle of the 1.5-1.6 bin


def test_features_respect_date_weights():
    base = base_tables()
    # Only the weekend day: no weekday days -> weekday-based features undefined.
    weights = pd.Series([0.0, 0.0, 1.0], index=[WEEKDAY, WEEKDAY_2, WEEKEND])
    f = zf.compute_features(base, weights)
    assert f.loc[1, "pickups"] == pytest.approx(100)
    assert np.isnan(f.loc[1, "weekday_share_morning"])
    # Doubling one weekday doubles its counts.
    weights = pd.Series([2.0, 0.0, 1.0], index=[WEEKDAY, WEEKDAY_2, WEEKEND])
    assert zf.compute_features(base, weights).loc[1, "pickups"] == pytest.approx(300)


def test_imbalance_needs_minimum_trip_ends():
    result = zf.imbalance(pd.Series([60, 10]), pd.Series([40, 5]), minimum=100)
    assert result[0] == pytest.approx(0.2)
    assert np.isnan(result[1])


def test_bootstrap_dates_keep_day_type_counts():
    dates = pd.Series(pd.date_range("2025-01-01", "2025-01-31"))
    day_types = pd.Series(np.where(dates.dt.day_of_week >= 5, "weekend", "weekday"))
    weights = zc.bootstrap_dates(dates, day_types, np.random.default_rng(1))
    assert weights.sum() == 31
    weekend_dates = dates[day_types == "weekend"].to_numpy()
    assert weights[weekend_dates].sum() == len(weekend_dates)


def synthetic_features(n_per_group=12, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for group, (morning, pm, volume) in enumerate([(0.4, 0.15, 1.5), (0.1, 0.35, 3.3), (0.2, 0.25, 2.4)]):
        for i in range(n_per_group):
            rows.append({
                "zone": f"z{group}_{i}", "pickups": 10 ** (volume + 1.5),
                "avg_daily_pickups": 10 ** volume,
                "log10_avg_daily_pickups": volume + rng.normal(0, 0.05),
                "weekend_to_weekday_ratio": 1 + rng.normal(0, 0.02),
                "weekday_share_overnight": 0.05 + rng.normal(0, 0.005),
                "weekday_share_morning": morning + rng.normal(0, 0.01),
                "weekday_share_pm_rush": pm + rng.normal(0, 0.01),
                "weekday_share_evening": 0.15 + rng.normal(0, 0.005),
                "imbalance_morning": (0.3 if group == 0 else -0.3) + rng.normal(0, 0.02),
                "imbalance_pm_rush": (-0.3 if group == 0 else 0.3) + rng.normal(0, 0.02),
                "group": group,
            })
    return pd.DataFrame(rows)


def test_model_inputs_standardise_and_fill_missing():
    f = synthetic_features()
    f.loc[0, "imbalance_morning"] = np.nan
    matrix, columns, missing = zc.model_inputs(f, zc.MODELS["A"])
    assert matrix.shape == (len(f), len(columns))
    assert np.allclose(matrix.mean(axis=0), 0, atol=1e-9)
    assert np.allclose(matrix.std(axis=0), 1, atol=1e-9)
    assert missing["imbalance_morning"] == 1
    grouped, _, _ = zc.model_inputs(f, zc.MODELS["A"], "equal_group")
    # Timing has 4 features -> each scaled by 1/2, so the group's total variance is 1.
    timing = [columns.index(c) for c in zc.MODELS["A"]["timing"]]
    assert grouped[:, timing].var(axis=0).sum() == pytest.approx(1.0)


def test_kmeans_is_deterministic_and_recovers_groups():
    f = synthetic_features()
    matrix, _, _ = zc.model_inputs(f, zc.MODELS["A"])
    first, second = zc.fit(matrix, 3), zc.fit(matrix, 3)
    assert (first == second).all()
    assert zc.adjusted_rand_score(f["group"], first) == pytest.approx(1.0)
    assert zc.variance_explained(matrix, first) > 0.6  # 3 of 9 features carry no signal


def test_choose_k_applies_gates_before_silhouette():
    table = pd.DataFrame({
        "k": [2, 3, 4],
        "silhouette": [0.60, 0.59, 0.50],
        "min_cluster_zones": [2, 10, 10],    # k=2 fails the size gate
        "bootstrap_ari_median": [0.9, 0.9, 0.9],
        "seed_ari_mean": [1.0, 1.0, 1.0],
    })
    k, checked = zc.choose_k(table)
    assert k == 3
    assert checked["eligible"].tolist() == [False, True, True]
    unstable = table.assign(bootstrap_ari_median=0.1)
    assert zc.choose_k(unstable)[0] is None


def test_order_clusters_ranks_by_total_pickups():
    f = pd.DataFrame({"pickups": [10, 10, 500, 500, 50]})
    labels = zc.order_clusters(np.array([2, 2, 0, 0, 1]), f)
    assert labels.tolist() == [2, 2, 0, 0, 1]
    labels = zc.order_clusters(np.array([0, 0, 1, 1, 2]), f)
    assert labels.tolist() == [2, 2, 0, 0, 1]


def test_rule_based_groupings():
    f = pd.DataFrame({
        "imbalance_morning": [0.3, -0.3, 0.0],
        "imbalance_pm_rush": [-0.3, 0.3, 0.0],
        "weekday_share_overnight": [0.02, 0.02, 0.10],
        "weekday_share_morning": [0.40, 0.10, 0.20],
        "weekday_share_pm_rush": [0.20, 0.40, 0.20],
        "weekday_share_evening": [0.10, 0.20, 0.20],
    })
    assert zc.rule_flow_pattern(f).tolist() == ["out AM / in PM", "in AM / out PM", "mixed or balanced"]
    assert zc.rule_dominant_period(f).tolist() == ["morning", "pm_rush", "overnight"]


def test_airport_placement_reports_nearest_cluster():
    f = synthetic_features()
    matrix, _, _ = zc.model_inputs(f, zc.MODELS["A"])
    labels = zc.fit(matrix, 3)
    probe = f.iloc[[0]].copy()
    result = zc.airport_placement(f, probe.set_axis([999]), zc.MODELS["A"], labels)
    assert result[999]["nearest_cluster"] == labels[0]
    assert result[999]["distance_vs_member_p95"] < 1.5


def test_match_labels_finds_best_permutation():
    reference = np.array([0, 0, 1, 1, 2, 2])
    other = np.array([2, 2, 0, 0, 1, 0])  # same groups, permuted, one zone moved
    matched = zc.match_labels(reference, other)
    assert matched.tolist() == [0, 0, 1, 1, 2, 1]
    assert (matched != reference).sum() == 1


def test_weekend_then_period_rule_has_five_groups():
    f = pd.DataFrame({
        "weekend_to_weekday_ratio": [2.0, 1.0, 1.0, 1.0, 1.0],
        "weekday_share_overnight": [0.02, 0.20, 0.02, 0.02, 0.02],
        "weekday_share_morning": [0.40, 0.10, 0.40, 0.10, 0.10],
        "weekday_share_pm_rush": [0.20, 0.20, 0.20, 0.45, 0.20],
        "weekday_share_evening": [0.10, 0.10, 0.10, 0.10, 0.40],
    })
    assert zc.rule_weekend_then_period(f).tolist() == [
        "weekend_heavy", "overnight", "morning", "pm_rush", "evening"]


def test_profiles_report_both_pickup_shares():
    f = pd.DataFrame({"pickups": [60.0, 20.0, 20.0],
                      **{c: [1.0, 2.0, 3.0] for c in zc.PROFILE_COLUMNS}})
    table = zc.profiles(f, pd.Series([0, 1, 1]), all_eligible_pickups=200.0)
    assert table["zones"].tolist() == [1, 2]
    assert table["pickup_share_of_fitted_pct"].tolist() == pytest.approx([60.0, 40.0])
    assert table["pickup_share_of_all_pct"].tolist() == pytest.approx([30.0, 20.0])


def test_reconcile_recounts_pickups_from_trip_file(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    trips = pa.table({"PULocationID": pa.array([1, 1, 2, 132, 9, 1], pa.int32()),
                      "eligible_demand": [True, True, True, True, True, False]})
    path = tmp_path / "flagged.parquet"
    pq.write_table(trips, path)
    clusters = pd.DataFrame({"pickups": [2, 1], "cluster_A_k5": [0, 1]}, index=[1, 2])
    full = pd.DataFrame({"pickups": [2, 1, 1, 1]}, index=[1, 2, 9, 132])
    result = zc.reconcile(clusters, full, path)
    assert result["eligible_pickups_source"] == 5
    assert result["zone_pickups_match_source"]
    assert not result["airports_in_fit"]
    assert result["fitted_share_pct"] == pytest.approx(60.0)
    assert result["airport_share_pct"] == pytest.approx(20.0)
    assert result["below_threshold_share_pct"] == pytest.approx(20.0)
    assert result["solutions"]["cluster_A_k5"]["sizes_sum"] == 2
