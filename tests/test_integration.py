"""End-to-end check of the hand-off from cleaning to the zone tables, on synthetic trips.

Runs clean_trips.clean on a small raw file, then zone_features.build_base_tables on
its output, and checks that each trip is counted at the right zone, date and hour
under its own pickup and drop-off eligibility.
"""

import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import clean_trips  # noqa: E402
import zone_features  # noqa: E402
from test_cleaning import trips_frame, write_raw  # noqa: E402

T = pd.Timestamp


def synthetic_month() -> pd.DataFrame:
    return trips_frame(
        # 1. ordinary weekday trip 161 -> 236
        {},
        # 2. return trip 236 -> 161 the same hour
        {"PULocationID": 236, "DOLocationID": 161,
         "tpep_pickup_datetime": T("2025-01-15 10:30"), "tpep_dropoff_datetime": T("2025-01-15 10:45")},
        # 3. picked up in December, dropped off on 1 January: drop-off end only
        {"tpep_pickup_datetime": T("2024-12-31 23:50"), "tpep_dropoff_datetime": T("2025-01-01 00:10")},
        # 4. negative duration: neither end is usable
        {"tpep_pickup_datetime": T("2025-01-16 09:00"), "tpep_dropoff_datetime": T("2025-01-16 08:59")},
        # 5. Flex Fare with systematically missing fields on a holiday: still a pickup
        {"payment_type": 0, "passenger_count": None,
         "tpep_pickup_datetime": T("2025-01-20 12:00"), "tpep_dropoff_datetime": T("2025-01-20 12:15")},
    )


def test_cleaning_output_feeds_zone_tables_with_independent_trip_ends(tmp_path):
    raw, flagged, zones_dir = tmp_path / "raw.parquet", tmp_path / "flagged.parquet", tmp_path / "zones"
    write_raw(raw, synthetic_month())

    summary = clean_trips.clean(raw, flagged)
    assert summary["input_rows"] == summary["output_rows"] == 5
    eligible = pq.read_table(flagged, columns=["eligible_demand"]).column(0).to_pylist()
    assert eligible == [True, True, False, False, True]

    tables = zone_features.build_base_tables(flagged, zones_dir)
    counts = tables["zone_date_hour_counts"]
    totals = counts.groupby("zone_id")[["pickups", "dropoffs"]].sum()
    assert totals.loc[161].tolist() == [2, 1]
    assert totals.loc[236].tolist() == [1, 3]

    cell = counts.set_index(["zone_id", "date", "hour"])
    assert cell.loc[(161, T("2025-01-15"), 10), ["pickups", "dropoffs"]].tolist() == [1, 1]
    # Trip 3's drop-off is counted although its pickup is out of period.
    assert cell.loc[(236, T("2025-01-01"), 0), ["pickups", "dropoffs"]].tolist() == [0, 1]
    assert cell.loc[(161, T("2025-01-20"), 12), "day_type"] == "holiday"

    daily = tables["zone_date_totals"].groupby("zone_id")[["pickups", "flex_pickups"]].sum()
    assert daily.loc[161].tolist() == [2, 1]
    assert daily["pickups"].sum() == sum(eligible)

    # The tables are written for the clustering step and read back unchanged.
    reloaded = zone_features.load_base_tables(zones_dir)
    pd.testing.assert_frame_equal(reloaded["zone_date_hour_counts"], counts)
