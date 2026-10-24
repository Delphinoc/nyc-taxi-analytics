"""Unit tests for the Phase 1 profiling checks, using small synthetic tables."""

import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import profile_raw  # noqa: E402


def make_trips() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tpep_pickup_datetime": pd.to_datetime(
                ["2025-01-02 10:00", "2025-01-10 12:00", "2024-12-31 23:00", "2025-01-20 08:00"]
            ),
            "tpep_dropoff_datetime": pd.to_datetime(
                ["2025-01-02 10:15", "2025-01-10 11:00", "2025-01-01 00:10", "2025-01-20 08:00"]
            ),
            "VendorID": [1, 2, 2, 9],
            "RatecodeID": [1.0, 99.0, None, 7.0],
            "payment_type": [1, 2, 0, 1],
            "store_and_fwd_flag": ["N", "Y", None, "N"],
            "cbd_congestion_fee": [0.75, 0.75, 0.0, 0.0],
        }
    )


def test_datetime_checks_flags_out_of_period_and_negative_duration():
    result = profile_raw.datetime_checks(make_trips())
    assert result["pickup_outside_jan_2025"] == 1
    assert result["dropoff_before_pickup"] == 1
    assert result["zero_duration"] == 1


def test_code_checks_reports_only_undocumented_codes():
    result = profile_raw.code_checks(make_trips())
    assert result["VendorID"]["undocumented_codes"] == {"9": 1}
    assert result["RatecodeID"]["undocumented_codes"] == {"7.0": 1}
    assert result["payment_type"]["undocumented_codes"] == {}
    assert result["store_and_fwd_flag"]["undocumented_codes"] == {}


def test_cbd_fee_before_start_date_is_flagged():
    result = profile_raw.cbd_fee_checks(make_trips())
    assert result["nonzero_fee_before_2025_01_05"] == 1
    assert result["nonzero_fee_from_2025_01_05"] == 1


def test_amount_reconciliation_counts_mismatches_and_treats_null_as_zero():
    row = {column: 0.0 for column in profile_raw.AMOUNT_COMPONENTS}
    frame = pd.DataFrame([row, row, row])
    frame["fare_amount"] = [10.0, 10.0, 10.0]
    frame["Airport_fee"] = [0.0, None, 0.0]
    frame["total_amount"] = [10.0, 10.0, 12.5]
    result = profile_raw.amount_reconciliation(frame)
    assert result["mismatched_rows"] == 1


def test_read_zones_keeps_literal_na_labels(tmp_path):
    csv = tmp_path / "zones.csv"
    csv.write_text(
        '"LocationID","Borough","Zone","service_zone"\n'
        '1,"EWR","Newark Airport","EWR"\n'
        '264,"Unknown","N/A","N/A"\n'
        '265,"N/A","Outside of NYC","N/A"\n'
    )
    zones = profile_raw.read_zones(csv)
    trips = pd.DataFrame({"PULocationID": [1, 264, 265, 300], "DOLocationID": [1, 1, 1, 1]})
    result = profile_raw.location_checks(trips, zones)
    assert result["unknown_or_outside_nyc_zone_ids"] == [264, 265]
    assert result["PULocationID"]["unknown_or_outside_nyc"] == 2
    assert result["PULocationID"]["not_in_lookup"] == 1


def test_null_profile_and_duplicates_from_parquet(tmp_path):
    path = tmp_path / "trips.parquet"
    table = pa.table({"a": [1, 1, None, 2], "b": ["x", "x", "y", None]})
    pq.write_table(table, path, row_group_size=2)
    nulls = profile_raw.null_profile(path)
    assert nulls["a"] == {"null_count": 1, "null_pct": 25.0}
    assert nulls["b"]["null_count"] == 1
    assert profile_raw.duplicate_row_count(path) == 1


def test_pct_handles_zero_denominator():
    assert profile_raw.pct(5, 0) == 0.0
