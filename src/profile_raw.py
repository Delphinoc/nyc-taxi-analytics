"""Profile the raw January 2025 Yellow Taxi file without modifying it.

The file is read a few columns at a time so peak memory stays well below the
size of the full table. Results are written to reports/phase1_data_profile.md
and reports/phase1_data_profile.json. Every check here only *flags* records;
no cleaning decisions are made in Phase 1.

Usage:
    python src/profile_raw.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
TRIPS_PATH = RAW_DIR / "yellow_tripdata_2025-01.parquet"
ZONES_PATH = RAW_DIR / "taxi_zone_lookup.csv"
REPORTS_DIR = PROJECT_ROOT / "reports"

PERIOD_START = pd.Timestamp("2025-01-01")
PERIOD_END = pd.Timestamp("2025-02-01")  # exclusive
CBD_FEE_START = pd.Timestamp("2025-01-05")  # per the TLC data dictionary

# Valid codes as listed in the TLC Yellow Taxi data dictionary (March 18, 2025).
VALID_CODES = {
    "VendorID": {1, 2, 6, 7},
    "RatecodeID": {1, 2, 3, 4, 5, 6, 99},
    "payment_type": {0, 1, 2, 3, 4, 5, 6},
    "store_and_fwd_flag": {"Y", "N"},
}

# Components that should add up to total_amount (tips here are card tips only).
AMOUNT_COMPONENTS = [
    "fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
    "improvement_surcharge", "congestion_surcharge", "Airport_fee", "cbd_congestion_fee",
]
QUANTILES = [0.0, 0.01, 0.25, 0.5, 0.75, 0.99, 0.999, 1.0]


def read_columns(path: Path, columns: list[str]) -> pd.DataFrame:
    """Read only the requested columns from a Parquet file."""
    return pq.read_table(path, columns=columns).to_pandas()


def read_zones(path: Path) -> pd.DataFrame:
    """Read the zone lookup, keeping the literal "N/A" labels rather than NaN.

    pandas parses "N/A" as missing by default, which would hide zone 265
    ("Outside of NYC", borough "N/A") from the unknown-zone check.
    """
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


def pct(count: int, total: int) -> float:
    """Percentage rounded to 3 decimals; 0 when the denominator is 0."""
    return round(100 * count / total, 3) if total else 0.0


def null_profile(path: Path) -> dict[str, dict[str, float]]:
    """Null count and rate per column, computed from Parquet column statistics."""
    meta = pq.ParquetFile(path).metadata
    total_rows = meta.num_rows
    nulls: dict[str, int] = {}
    for rg in range(meta.num_row_groups):
        row_group = meta.row_group(rg)
        for col in range(row_group.num_columns):
            column = row_group.column(col)
            stats = column.statistics
            if stats is None or not stats.has_null_count:
                raise ValueError(f"No null statistics for {column.path_in_schema}")
            nulls[column.path_in_schema] = nulls.get(column.path_in_schema, 0) + stats.null_count
    return {name: {"null_count": n, "null_pct": pct(n, total_rows)} for name, n in nulls.items()}


def datetime_checks(trips: pd.DataFrame) -> dict:
    """Coverage and ordering checks on pickup/dropoff timestamps."""
    pickup = trips["tpep_pickup_datetime"]
    dropoff = trips["tpep_dropoff_datetime"]
    duration_min = (dropoff - pickup).dt.total_seconds() / 60
    total = len(trips)
    outside = (pickup < PERIOD_START) | (pickup >= PERIOD_END)
    checks = {
        "pickup_min": str(pickup.min()),
        "pickup_max": str(pickup.max()),
        "dropoff_min": str(dropoff.min()),
        "dropoff_max": str(dropoff.max()),
        "pickup_outside_jan_2025": int(outside.sum()),
        "dropoff_before_pickup": int((duration_min < 0).sum()),
        "zero_duration": int((duration_min == 0).sum()),
        "duration_over_3h": int((duration_min > 180).sum()),
        "duration_over_24h": int((duration_min > 1440).sum()),
        "duration_minutes_quantiles": quantiles(duration_min),
        "pickups_by_date_min": int(pickup[~outside].dt.date.value_counts().min()),
        "pickups_by_date_max": int(pickup[~outside].dt.date.value_counts().max()),
        "distinct_pickup_dates_in_jan": int(pickup[~outside].dt.date.nunique()),
    }
    checks["outside_period_pct"] = pct(checks["pickup_outside_jan_2025"], total)
    return checks


def quantiles(series: pd.Series) -> dict[str, float]:
    """Selected quantiles of a numeric series, ignoring nulls."""
    values = series.dropna().quantile(QUANTILES)
    return {f"p{q * 100:g}": round(float(v), 3) for q, v in values.items()}


def code_checks(trips: pd.DataFrame) -> dict:
    """Value counts for coded columns and counts of codes not in the dictionary."""
    result = {}
    for column, valid in VALID_CODES.items():
        counts = trips[column].value_counts(dropna=False)
        invalid = {str(k): int(v) for k, v in counts.items() if pd.notna(k) and k not in valid}
        result[column] = {
            "value_counts": {str(k): int(v) for k, v in counts.sort_index().items()},
            "undocumented_codes": invalid,
        }
    return result


def numeric_checks(trips: pd.DataFrame) -> dict:
    """Range checks on distance, passenger count and money columns."""
    total = len(trips)
    distance = trips["trip_distance"]
    passengers = trips["passenger_count"]
    result = {
        "trip_distance": {
            "quantiles": quantiles(distance),
            "zero": int((distance == 0).sum()),
            "negative": int((distance < 0).sum()),
            "over_100_miles": int((distance > 100).sum()),
        },
        "passenger_count": {
            "zero": int((passengers == 0).sum()),
            "over_6": int((passengers > 6).sum()),
        },
    }
    for column in AMOUNT_COMPONENTS + ["total_amount"]:
        values = trips[column]
        result[column] = {
            "quantiles": quantiles(values),
            "negative": int((values < 0).sum()),
            "negative_pct": pct(int((values < 0).sum()), total),
        }
    result["fare_amount"]["zero"] = int((trips["fare_amount"] == 0).sum())
    return result


def amount_reconciliation(trips: pd.DataFrame, tolerance: float = 0.01) -> dict:
    """Compare total_amount with the sum of its documented components.

    Null components are treated as 0 so the check is defined for every row;
    the mismatch count therefore includes rows where a null hides a charge.
    """
    component_sum = trips[AMOUNT_COMPONENTS].fillna(0).sum(axis=1)
    difference = (trips["total_amount"] - component_sum).round(2)
    mismatched = difference.abs() > tolerance
    result = {
        "tolerance": tolerance,
        "mismatched_rows": int(mismatched.sum()),
        "mismatched_pct": pct(int(mismatched.sum()), len(trips)),
        "difference_quantiles_when_mismatched": quantiles(difference[mismatched]),
        "most_common_differences": {
            str(k): int(v) for k, v in difference[mismatched].value_counts().head(5).items()
        },
    }
    if "VendorID" in trips:
        by_vendor = mismatched.groupby(trips["VendorID"]).mean() * 100
        result["mismatched_pct_by_vendor"] = {str(k): round(v, 2) for k, v in by_vendor.items()}
    return result


def cbd_fee_checks(trips: pd.DataFrame) -> dict:
    """The CBD congestion fee started on 5 Jan 2025; charges before then are suspicious."""
    before = trips["tpep_pickup_datetime"] < CBD_FEE_START
    fee = trips["cbd_congestion_fee"]
    return {
        "nonzero_fee_before_2025_01_05": int((before & (fee > 0)).sum()),
        "nonzero_fee_from_2025_01_05": int((~before & (fee > 0)).sum()),
        "trips_from_2025_01_05": int((~before).sum()),
    }


def location_checks(trips: pd.DataFrame, zones: pd.DataFrame) -> dict:
    """Location IDs missing from the zone lookup, and unknown/outside-NYC zones."""
    known_ids = set(zones["LocationID"])
    unknown_zone_ids = set(
        zones.loc[zones["Borough"].isin(["Unknown", "N/A"]) | zones["Zone"].isna(), "LocationID"]
    )
    total = len(trips)
    result = {"unknown_or_outside_nyc_zone_ids": sorted(int(i) for i in unknown_zone_ids)}
    for column in ["PULocationID", "DOLocationID"]:
        ids = trips[column]
        not_in_lookup = ~ids.isin(known_ids)
        in_unknown = ids.isin(unknown_zone_ids)
        result[column] = {
            "distinct_ids": int(ids.nunique()),
            "not_in_lookup": int(not_in_lookup.sum()),
            "unknown_or_outside_nyc": int(in_unknown.sum()),
            "unknown_or_outside_nyc_pct": pct(int(in_unknown.sum()), total),
        }
    return result


def duplicate_row_count(path: Path) -> int:
    """Count rows whose full-row 64-bit hash repeats an earlier row.

    Rows are hashed one row group at a time so the whole table is never held
    in memory. A hash collision could inflate the count, but at ~3.5M rows
    the probability is negligible.
    """
    parquet_file = pq.ParquetFile(path)
    hashes = [
        pd.util.hash_pandas_object(parquet_file.read_row_group(rg).to_pandas(), index=False).to_numpy()
        for rg in range(parquet_file.num_row_groups)
    ]
    all_hashes = np.concatenate(hashes)
    return int(len(all_hashes) - len(np.unique(all_hashes)))


def null_pattern(path: Path) -> dict:
    """Check whether the optional columns are null on exactly the same rows."""
    columns = ["passenger_count", "RatecodeID", "store_and_fwd_flag",
               "congestion_surcharge", "Airport_fee"]
    frame = read_columns(path, columns + ["payment_type"])
    any_null = frame[columns].isna().any(axis=1)
    all_null = frame[columns].isna().all(axis=1)
    return {
        "columns": columns,
        "rows_with_any_null": int(any_null.sum()),
        "rows_with_all_null": int(all_null.sum()),
        "payment_type_counts_on_null_rows": {
            str(k): int(v) for k, v in frame.loc[any_null, "payment_type"].value_counts().items()
        },
    }


def build_profile(trips_path: Path = TRIPS_PATH, zones_path: Path = ZONES_PATH) -> dict:
    """Run every Phase 1 check, reading only the columns each check needs."""
    parquet_file = pq.ParquetFile(trips_path)
    meta = parquet_file.metadata
    total_rows = meta.num_rows
    zones = read_zones(zones_path)

    profile: dict = {
        "file": {
            "name": trips_path.name,
            "size_bytes": trips_path.stat().st_size,
            "rows": total_rows,
            "columns": meta.num_columns,
            "row_groups": meta.num_row_groups,
            "created_by": meta.created_by,
        },
        "schema": {field.name: str(field.type) for field in parquet_file.schema_arrow},
        "nulls": null_profile(trips_path),
        "null_pattern": null_pattern(trips_path),
    }

    times = read_columns(trips_path, ["tpep_pickup_datetime", "tpep_dropoff_datetime"])
    profile["datetime"] = datetime_checks(times)
    del times

    codes = read_columns(trips_path, list(VALID_CODES))
    profile["codes"] = code_checks(codes)
    del codes

    numeric = read_columns(trips_path, ["VendorID", "trip_distance", "passenger_count"]
                           + AMOUNT_COMPONENTS + ["total_amount"])
    profile["numeric"] = numeric_checks(numeric)
    profile["amount_reconciliation"] = amount_reconciliation(numeric)
    del numeric

    fees = read_columns(trips_path, ["tpep_pickup_datetime", "cbd_congestion_fee"])
    profile["cbd_congestion_fee"] = cbd_fee_checks(fees)
    del fees

    locations = read_columns(trips_path, ["PULocationID", "DOLocationID"])
    profile["locations"] = location_checks(locations, zones)
    del locations

    profile["duplicates"] = {"exact_duplicate_rows": duplicate_row_count(trips_path)}
    profile["zones_lookup"] = {
        "rows": len(zones),
        "columns": list(zones.columns),
        "location_id_range": [int(zones["LocationID"].min()), int(zones["LocationID"].max())],
        "duplicate_location_ids": int(zones["LocationID"].duplicated().sum()),
    }
    profile["peak_arrow_memory_mb"] = round(pa.default_memory_pool().max_memory() / 1024**2, 1)
    return profile


def to_markdown(profile: dict) -> str:
    """Render the profile as a readable Markdown report."""
    f = profile["file"]
    dt = profile["datetime"]
    lines = [
        "# Phase 1 raw data profile: Yellow Taxi, January 2025",
        "",
        "Generated by `src/profile_raw.py` from the unmodified raw file. "
        "Counts are observations; nothing has been removed or corrected.",
        "",
        "## File",
        "",
        f"- File: `{f['name']}` ({f['size_bytes'] / 1024**2:.1f} MB, {f['row_groups']} row groups)",
        f"- Rows: {f['rows']:,}; columns: {f['columns']}",
        f"- Pickup range: {dt['pickup_min']} to {dt['pickup_max']}",
        f"- Dropoff range: {dt['dropoff_min']} to {dt['dropoff_max']}",
        f"- Peak Arrow memory while profiling: {profile['peak_arrow_memory_mb']} MB",
        "",
        "## Schema and null rates",
        "",
        "| Column | Type | Nulls | Null % |",
        "|---|---|---:|---:|",
    ]
    for name, dtype in profile["schema"].items():
        n = profile["nulls"][name]
        lines.append(f"| {name} | {dtype} | {n['null_count']:,} | {n['null_pct']} |")

    num = profile["numeric"]
    rec = profile["amount_reconciliation"]
    nulls = profile["null_pattern"]
    loc = profile["locations"]
    cbd = profile["cbd_congestion_fee"]
    rows = f["rows"]
    lines += [
        "",
        "## Data-quality flags (counts of raw rows)",
        "",
        "| Check | Rows | % of rows |",
        "|---|---:|---:|",
        f"| Optional fields all null together (all with payment_type 0) "
        f"| {nulls['rows_with_all_null']:,} | {pct(nulls['rows_with_all_null'], rows)} |",
        f"| Pickup outside January 2025 | {dt['pickup_outside_jan_2025']:,} "
        f"| {dt['outside_period_pct']} |",
        f"| Dropoff before pickup | {dt['dropoff_before_pickup']:,} "
        f"| {pct(dt['dropoff_before_pickup'], rows)} |",
        f"| Zero duration | {dt['zero_duration']:,} | {pct(dt['zero_duration'], rows)} |",
        f"| Duration over 24 h | {dt['duration_over_24h']:,} | {pct(dt['duration_over_24h'], rows)} |",
        f"| Zero trip distance | {num['trip_distance']['zero']:,} "
        f"| {pct(num['trip_distance']['zero'], rows)} |",
        f"| Trip distance over 100 miles | {num['trip_distance']['over_100_miles']:,} "
        f"| {pct(num['trip_distance']['over_100_miles'], rows)} |",
        f"| Passenger count 0 | {num['passenger_count']['zero']:,} "
        f"| {pct(num['passenger_count']['zero'], rows)} |",
        f"| Negative fare_amount | {num['fare_amount']['negative']:,} "
        f"| {num['fare_amount']['negative_pct']} |",
        f"| Negative total_amount | {num['total_amount']['negative']:,} "
        f"| {num['total_amount']['negative_pct']} |",
        f"| total_amount differs from sum of components by > $0.01 | {rec['mismatched_rows']:,} "
        f"| {rec['mismatched_pct']} |",
        f"| CBD fee charged before 2025-01-05 | {cbd['nonzero_fee_before_2025_01_05']:,} "
        f"| {pct(cbd['nonzero_fee_before_2025_01_05'], rows)} |",
        f"| Pickup in unknown/outside-NYC zone ({', '.join(map(str, loc['unknown_or_outside_nyc_zone_ids']))}) "
        f"| {loc['PULocationID']['unknown_or_outside_nyc']:,} "
        f"| {loc['PULocationID']['unknown_or_outside_nyc_pct']} |",
        f"| Dropoff in unknown/outside-NYC zone | {loc['DOLocationID']['unknown_or_outside_nyc']:,} "
        f"| {loc['DOLocationID']['unknown_or_outside_nyc_pct']} |",
        f"| Exact duplicate rows | {profile['duplicates']['exact_duplicate_rows']:,} "
        f"| {pct(profile['duplicates']['exact_duplicate_rows'], rows)} |",
        "",
        f"Reconciliation mismatch rate by VendorID (%): {rec.get('mismatched_pct_by_vendor')}",
        "",
        "## Full JSON",
        "",
        "See `reports/phase1_data_profile.json` for every check, including quantiles.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    profile = build_profile()
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "phase1_data_profile.json").write_text(
        json.dumps(profile, indent=2) + "\n", encoding="utf-8"
    )
    (REPORTS_DIR / "phase1_data_profile.md").write_text(to_markdown(profile), encoding="utf-8")
    print(json.dumps(profile, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
