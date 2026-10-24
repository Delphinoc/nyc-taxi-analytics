"""Tests for the Phase 2 flagging rules and pipeline, using synthetic trips."""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import clean_trips  # noqa: E402
import cleaning_rules as rules  # noqa: E402

BASE_TRIP = {
    "VendorID": 2,
    "tpep_pickup_datetime": pd.Timestamp("2025-01-15 10:00:00"),
    "tpep_dropoff_datetime": pd.Timestamp("2025-01-15 10:15:00"),
    "passenger_count": 1,
    "trip_distance": 2.0,
    "RatecodeID": 1,
    "store_and_fwd_flag": "N",
    "PULocationID": 161,
    "DOLocationID": 236,
    "payment_type": 1,
    "fare_amount": 15.0,
    "extra": 0.0,
    "mta_tax": 0.5,
    "tip_amount": 3.0,
    "tolls_amount": 0.0,
    "improvement_surcharge": 1.0,
    "total_amount": 22.75,
    "congestion_surcharge": 2.5,
    "Airport_fee": 0.0,
    "cbd_congestion_fee": 0.75,
}


def trips_frame(*overrides: dict) -> pd.DataFrame:
    """One synthetic trip per override dict, starting from a clean BASE_TRIP."""
    return pd.DataFrame([{**BASE_TRIP, **o} for o in overrides])


def flag(frame: pd.DataFrame) -> pd.DataFrame:
    has_partner, reversed_original, ambiguous = rules.find_reversal_pairs(
        frame[rules.REVERSAL_KEY + ["total_amount"]]
    )
    return rules.add_flags(
        rules.add_derived_columns(frame), has_partner, reversed_original, ambiguous
    )


def test_clean_trip_has_no_flags_and_is_eligible_everywhere():
    result = flag(trips_frame({}))
    assert not result[rules.DQ_FLAGS + rules.SCOPE_FLAGS + rules.INFO_FLAGS].any(axis=None)
    assert result[list(rules.ELIGIBILITY_RULES)].all(axis=None)


@pytest.mark.parametrize(
    ("pickup", "outside"),
    [
        ("2024-12-31 23:59:59", True),
        ("2025-01-01 00:00:00", False),
        ("2025-01-31 23:59:59", False),
        ("2025-02-01 00:00:00", True),
    ],
)
def test_period_boundaries_use_pickup_time(pickup, outside):
    start = pd.Timestamp(pickup)
    result = flag(trips_frame({"tpep_pickup_datetime": start,
                               "tpep_dropoff_datetime": start + pd.Timedelta(minutes=10)}))
    assert result.loc[0, "scope_outside_period"] == outside
    assert result.loc[0, "eligible_demand"] == (not outside)


def test_duration_rules_negative_zero_and_long():
    pickup = BASE_TRIP["tpep_pickup_datetime"]
    result = flag(trips_frame(
        {"tpep_dropoff_datetime": pickup - pd.Timedelta(minutes=5)},
        {"tpep_dropoff_datetime": pickup},
        {"tpep_dropoff_datetime": pickup + pd.Timedelta(hours=12)},
        {"tpep_dropoff_datetime": pickup + pd.Timedelta(hours=12, seconds=1), "trip_distance": 5.0},
    ))
    assert result["dq_negative_duration"].tolist() == [True, False, False, False]
    assert result["dq_zero_duration"].tolist() == [False, True, False, False]
    assert result["dq_duration_over_12h"].tolist() == [False, False, False, True]
    # Speed is undefined (null) without a positive duration, never infinite.
    assert result["avg_speed_mph"].isna().tolist() == [True, True, False, False]
    # A zero duration keeps the pickup usable for demand; a negative one does not.
    assert result["eligible_demand"].tolist() == [False, True, True, True]
    assert not result["eligible_distance_duration"].iloc[[0, 1, 3]].any()


def test_speed_threshold_is_exclusive():
    pickup = BASE_TRIP["tpep_pickup_datetime"]
    result = flag(trips_frame(
        {"trip_distance": 80.0, "tpep_dropoff_datetime": pickup + pd.Timedelta(hours=1)},
        {"trip_distance": 80.1, "tpep_dropoff_datetime": pickup + pd.Timedelta(hours=1)},
    ))
    assert result["dq_implausible_speed"].tolist() == [False, True]


def test_zero_distance_excludes_distance_and_fare_but_not_demand():
    result = flag(trips_frame({"trip_distance": 0.0}))
    row = result.loc[0]
    assert row["dq_zero_distance"]
    assert row["eligible_demand"]
    assert not row["eligible_distance_duration"]
    assert not row["eligible_fare"]


def test_flex_fare_nulls_are_not_treated_as_invalid():
    result = flag(trips_frame({
        "payment_type": 0, "passenger_count": None, "RatecodeID": None,
        "store_and_fwd_flag": None, "congestion_surcharge": None, "Airport_fee": None,
        "fare_amount": -4.75, "total_amount": 3.00,
    }))
    row = result.loc[0]
    assert row["scope_flex_fare"]
    # Flex fare_amount is not a metered fare, so it is not a fare data-quality error.
    assert not row["dq_nonpositive_fare"]
    assert not row["info_fare_outside_standard_meter_range"]
    assert row["eligible_demand"] and row["eligible_distance_duration"] and row["eligible_clustering"]
    assert not row["eligible_fare"]


def test_negative_total_reversal_pair_is_matched():
    original = {}
    reversal = {"payment_type": 4, "fare_amount": -15.0, "mta_tax": -0.5, "tip_amount": -3.0,
                "improvement_surcharge": -1.0, "congestion_surcharge": -2.5,
                "cbd_congestion_fee": -0.75, "total_amount": -22.75}
    unrelated = {"PULocationID": 50}
    result = flag(trips_frame(original, reversal, unrelated))
    assert result["dq_negative_total_amount"].tolist() == [False, True, False]
    assert result["info_negative_row_has_exact_reversal_partner"].tolist() == [False, True, False]
    # The reversed original is a real trip (demand) but its fare was not collected.
    assert result.loc[0, "scope_fare_not_settled"]
    assert result.loc[0, "eligible_demand"] and not result.loc[0, "eligible_fare"]
    assert not result.loc[1, "eligible_demand"]
    assert result.loc[2, "eligible_fare"]


def test_negative_fare_on_metered_trip_is_flagged():
    result = flag(trips_frame({"fare_amount": -15.0, "total_amount": -9.75, "payment_type": 2}))
    assert result.loc[0, "dq_nonpositive_fare"]
    assert result.loc[0, "dq_negative_total_amount"]
    assert not result.loc[0, "info_negative_row_has_exact_reversal_partner"]


def test_meter_range_is_informational_for_standard_rate_only():
    # 2 miles, 15 minutes: bound = 3 + 7 + 10.5 + 1.4 = 21.90
    result = flag(trips_frame(
        {"fare_amount": 21.90},
        {"fare_amount": 21.91},
        {"fare_amount": 2.99},
        {"fare_amount": 70.0, "RatecodeID": 2},  # JFK flat fare: range not applied
    ))
    assert result["info_fare_outside_standard_meter_range"].tolist() == [False, True, True, False]
    # The check cannot tell a wrong fare from a wrong rate code, so it never excludes.
    assert result["eligible_fare"].all()
    assert "info_fare_outside_standard_meter_range" not in rules.DQ_FLAGS


def test_component_reconciliation_uses_exact_cents():
    # 0.1 + 0.2 style float error must not register as a difference.
    result = flag(trips_frame(
        {"fare_amount": 10.1, "tip_amount": 2.2, "total_amount": 17.05},
        {"fare_amount": 10.1, "tip_amount": 2.2, "total_amount": 17.06},
    ))
    assert result["info_total_differs_from_components"].tolist() == [False, True]


def test_null_component_is_reported_not_filled():
    # A null congestion surcharge counts as 0, so a total that includes it is reported.
    result = flag(trips_frame({"congestion_surcharge": None}))
    assert result.loc[0, "info_total_differs_from_components"]
    assert pd.isna(result.loc[0, "congestion_surcharge"])


def test_reversal_matching_is_one_to_one_and_reports_ambiguity():
    reversal = {"payment_type": 4, "total_amount": -22.75}
    # Two identical positive rows and one negative: ambiguous, nothing matched.
    result = flag(trips_frame({}, {}, reversal))
    assert not result["info_negative_row_has_exact_reversal_partner"].any()
    assert result["info_negative_row_match_ambiguous"].tolist() == [False, False, True]
    assert not result["scope_fare_not_settled"].iloc[:2].any()
    # Two negatives and two positives with the same key are also ambiguous.
    result = flag(trips_frame({}, {}, reversal, reversal))
    assert result["info_negative_row_match_ambiguous"].sum() == 2
    assert not result["info_negative_row_has_exact_reversal_partner"].any()
    # A different amount never matches.
    result = flag(trips_frame({}, {**reversal, "total_amount": -20.00}))
    assert not result["info_negative_row_has_exact_reversal_partner"].any()
    assert not result["info_negative_row_match_ambiguous"].any()


def test_multiple_flags_are_all_recorded():
    pickup = pd.Timestamp("2025-02-01 00:00:00")
    frame = trips_frame({
        "tpep_pickup_datetime": pickup,
        "tpep_dropoff_datetime": pickup - pd.Timedelta(minutes=1),
        "trip_distance": 0.0,
        "PULocationID": 264,
        "DOLocationID": 300,
        "payment_type": 3,
    })
    result = flag(frame)
    expected = {"scope_outside_period", "dq_negative_duration", "dq_zero_distance",
                "scope_pickup_zone_unknown_or_outside_nyc", "dq_invalid_location_id",
                "scope_fare_not_settled"}
    flagged = {name for name in rules.DQ_FLAGS + rules.SCOPE_FLAGS if result.loc[0, name]}
    assert flagged == expected
    reasons = clean_trips.build_reason_strings(result).iloc[0].split(";")
    assert set(reasons) == expected
    assert not result[list(rules.ELIGIBILITY_RULES)].any(axis=None)


def test_reason_string_empty_when_no_flags():
    assert clean_trips.build_reason_strings(flag(trips_frame({}))).iloc[0] == ""


def write_raw(path: Path, frame: pd.DataFrame) -> None:
    schema = pa.schema([
        ("VendorID", pa.int32()), ("tpep_pickup_datetime", pa.timestamp("us")),
        ("tpep_dropoff_datetime", pa.timestamp("us")), ("passenger_count", pa.int64()),
        ("trip_distance", pa.float64()), ("RatecodeID", pa.int64()),
        ("store_and_fwd_flag", pa.large_string()), ("PULocationID", pa.int32()),
        ("DOLocationID", pa.int32()), ("payment_type", pa.int64()),
    ] + [(c, pa.float64()) for c in [
        "fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
        "improvement_surcharge", "total_amount", "congestion_surcharge", "Airport_fee",
        "cbd_congestion_fee"]])
    pq.write_table(pa.Table.from_pandas(frame[schema.names], schema=schema, preserve_index=False),
                   path, row_group_size=2)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pipeline_preserves_raw_and_is_idempotent(tmp_path):
    raw = tmp_path / "raw.parquet"
    out = tmp_path / "out.parquet"
    frame = trips_frame({}, {"passenger_count": None, "payment_type": 0},
                        {"total_amount": -24.75}, {"trip_distance": 0.0}, {"VendorID": 7})
    write_raw(raw, frame)
    raw_hash = sha256(raw)

    first = clean_trips.clean(raw, out)
    first_hash = sha256(out)
    second = clean_trips.clean(raw, out)

    assert sha256(raw) == raw_hash  # raw file untouched
    assert sha256(out) == first_hash  # rerun gives a byte-identical output
    assert first["input_rows"] == first["output_rows"] == second["output_rows"] == 5
    raw_table = pq.read_table(raw)
    out_table = pq.read_table(out)
    # Raw columns keep their exact types (nullable int64 stays int64) and values.
    assert out_table.select(raw_table.column_names).equals(raw_table)
    assert not (tmp_path / "out.parquet.part").exists()
