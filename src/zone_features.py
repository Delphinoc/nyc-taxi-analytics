"""Zone-level feature engineering for the Phase 4 segmentation.

Two steps:

1. ``build_base_tables`` reads the Phase 2 flagged file once (through the
   Phase 3 loader) and writes compact zone x date tables to
   data/processed/zones/: hourly pickup/drop-off counts and histograms of trip
   distance, duration and metered fare per mile. Everything later is computed
   from these small tables, never from trip rows.
2. ``compute_features`` turns the base tables into one row per pickup zone.
   It accepts per-date weights, so the same code computes features on the full
   month and on bootstrap resamples of days (a date drawn twice gets weight 2).

Medians are read off fine histograms (0.1 mi, 0.25 min, $0.05/mi bins), which
keeps resampling cheap; the bin width bounds the error. Definitions are
documented in docs/clustering_design.md.

Usage:
    python src/zone_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from eda_aggregates import FLAGGED_PATH, load_trips, read_zones, zone_hour_counts
from eda_metrics import AIRPORT_ZONES, NYC_ZONE_IDS, PERIODS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ZONES_DIR = PROJECT_ROOT / "data" / "processed" / "zones"

HISTOGRAMS = {
    # metric: (bin width, upper edge of the last regular bin)
    "distance_all": (0.1, 50.0),
    "distance_metered": (0.1, 50.0),
    "duration_all": (0.25, 180.0),
    "fare_per_mile_metered": (0.05, 40.0),
}
WEEKDAY_PERIODS = {
    "overnight": PERIODS["overnight (00-05)"],
    "morning": PERIODS["morning (06-09)"],
    "midday": PERIODS["midday (10-15)"],
    "pm_rush": PERIODS["pm rush (16-19)"],
    "evening": PERIODS["evening (20-23)"],
}
WEEKEND_LATE_NIGHT_HOURS = range(0, 4)
MIN_PERIOD_TRIP_ENDS = 100  # same stability rule as Phase 3 cells
MIN_DAILY_PERIOD_TRIP_ENDS = 10
MIN_FARE_PER_MILE_DISTANCE = 1.0  # fare per mile only for trips >= 1 mile


# --------------------------------------------------------------------------- base tables

def histogram_counts(frame: pd.DataFrame, value: str, metric: str) -> pd.DataFrame:
    """Counts per zone x date x bin; values above the cap go to the last bin."""
    width, cap = HISTOGRAMS[metric]
    bins = np.minimum(np.floor(frame[value].clip(lower=0) / width), cap / width).astype("int32")
    counts = (frame.assign(bin=bins).groupby(["PULocationID", "pu_date", "bin"]).size()
              .rename("trips").reset_index())
    return counts.rename(columns={"PULocationID": "zone_id", "pu_date": "date"}).assign(metric=metric)


def build_base_tables(flagged_path: Path = FLAGGED_PATH, output_dir: Path = ZONES_DIR) -> dict:
    trips = load_trips(flagged_path)
    counts = zone_hour_counts(trips, trips["eligible_demand"], trips["eligible_dropoff"],
                              by_date=True)

    demand = trips["eligible_demand"]
    daily = trips.loc[demand].groupby(["PULocationID", "pu_date"]).agg(
        pickups=("scope_flex_fare", "size"), flex_pickups=("scope_flex_fare", "sum"),
        fare_eligible=("eligible_fare", "sum"),
        distance_eligible=("eligible_distance_duration", "sum"),
    ).reset_index().rename(columns={"PULocationID": "zone_id", "pu_date": "date"})

    dd = trips.loc[trips["eligible_distance_duration"],
                   ["PULocationID", "pu_date", "trip_distance", "trip_duration_min"]]
    fare = trips.loc[trips["eligible_fare"],
                     ["PULocationID", "pu_date", "trip_distance", "fare_amount"]]
    del trips
    long_fare = fare[fare["trip_distance"] >= MIN_FARE_PER_MILE_DISTANCE]
    histograms = pd.concat([
        histogram_counts(dd, "trip_distance", "distance_all"),
        histogram_counts(dd, "trip_duration_min", "duration_all"),
        histogram_counts(fare, "trip_distance", "distance_metered"),
        histogram_counts(long_fare.assign(
            fare_per_mile=long_fare["fare_amount"] / long_fare["trip_distance"]),
            "fare_per_mile", "fare_per_mile_metered"),
    ], ignore_index=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    tables = {"zone_date_hour_counts": counts, "zone_date_totals": daily,
              "zone_date_histograms": histograms}
    for name, table in tables.items():
        table.to_parquet(output_dir / f"{name}.parquet", index=False)
    return tables


def load_base_tables(directory: Path = ZONES_DIR) -> dict:
    return {name: pd.read_parquet(directory / f"{name}.parquet")
            for name in ["zone_date_hour_counts", "zone_date_totals", "zone_date_histograms"]}


# --------------------------------------------------------------------------- features

def weighted_median_from_histogram(hist: pd.DataFrame, width: float) -> pd.Series:
    """Median per zone from (zone_id, bin, weight) rows, interpolated within the bin."""
    hist = hist.sort_values(["zone_id", "bin"])
    total = hist.groupby("zone_id")["weight"].transform("sum")
    cumulative = hist.groupby("zone_id")["weight"].cumsum()
    before = cumulative - hist["weight"]
    crossing = hist[(cumulative >= total / 2) & (before < total / 2)]
    crossing = crossing.groupby("zone_id").head(1)
    half = total.loc[crossing.index] / 2
    fraction = (half - before.loc[crossing.index]) / crossing["weight"]
    return pd.Series((crossing["bin"] + fraction).to_numpy() * width,
                     index=crossing["zone_id"].to_numpy())


def imbalance(pickups: pd.Series, dropoffs: pd.Series, minimum: int) -> pd.Series:
    trip_ends = pickups + dropoffs
    return ((pickups - dropoffs) / trip_ends.where(trip_ends > 0)).where(trip_ends >= minimum)


def period_persistence(counts: pd.DataFrame, hours, weights: pd.Series) -> pd.Series:
    """Weighted share of qualifying weekdays whose period net flow has the majority sign."""
    period = counts[counts["day_type"].eq("weekday") & counts["hour"].isin(hours)]
    daily = period.groupby(["zone_id", "date"])[["pickups", "dropoffs"]].sum().reset_index()
    daily["w"] = daily["date"].map(weights).fillna(0)
    daily = daily[(daily["pickups"] + daily["dropoffs"] >= MIN_DAILY_PERIOD_TRIP_ENDS)
                  & (daily["w"] > 0)]
    net = daily["pickups"] - daily["dropoffs"]
    grouped = daily.assign(pos=(net > 0) * daily["w"], neg=(net < 0) * daily["w"]).groupby("zone_id")
    days = grouped["w"].sum()
    share = grouped[["pos", "neg"]].sum().max(axis=1) / days
    return share.where(days >= 5)


def compute_features(base: dict, date_weights: pd.Series | None = None,
                     zone_names: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per NYC pickup zone (1-263) with every candidate feature.

    ``date_weights`` maps each date to how many times it is counted (1 for
    the full month; bootstrap multiplicities when resampling). Per-day
    averages divide by the weighted number of days of each type.
    """
    counts = base["zone_date_hour_counts"]
    totals = base["zone_date_totals"]
    hist = base["zone_date_histograms"]
    all_dates = pd.Series(sorted(counts["date"].unique()))
    if date_weights is None:
        date_weights = pd.Series(1.0, index=all_dates)
    w = counts["date"].map(date_weights).fillna(0)
    counts = counts.assign(pickups=counts["pickups"] * w, dropoffs=counts["dropoffs"] * w)
    counts = counts[counts["zone_id"].isin(NYC_ZONE_IDS)]

    day_types = counts.drop_duplicates("date").set_index("date")["day_type"]
    days = date_weights.groupby(day_types.reindex(date_weights.index)).sum()

    weekday = counts[counts["day_type"] == "weekday"]
    weekend = counts[counts["day_type"] == "weekend"]
    pickups = counts.groupby("zone_id")["pickups"].sum()
    wd_pickups = weekday.groupby("zone_id")["pickups"].sum()
    we_pickups = weekend.groupby("zone_id")["pickups"].sum()

    f = pd.DataFrame(index=pd.Index(sorted(pickups.index), name="zone_id"))
    f["pickups"] = pickups
    f["avg_daily_pickups"] = pickups / date_weights.sum()
    f["log10_avg_daily_pickups"] = np.log10(f["avg_daily_pickups"].clip(lower=0.1))
    wd_rate = wd_pickups.reindex(f.index).fillna(0) / days.get("weekday", np.nan)
    we_rate = we_pickups.reindex(f.index).fillna(0) / days.get("weekend", np.nan)
    f["weekend_to_weekday_ratio"] = we_rate / wd_rate.where(wd_rate > 0)

    wd_by_hour = weekday.groupby(["zone_id", "hour"])[["pickups", "dropoffs"]].sum()
    for name, hours in WEEKDAY_PERIODS.items():
        in_period = wd_by_hour[wd_by_hour.index.get_level_values("hour").isin(hours)]
        sums = in_period.groupby("zone_id").sum().reindex(f.index).fillna(0)
        f[f"weekday_share_{name}"] = sums["pickups"] / wd_pickups.reindex(f.index)
        f[f"imbalance_{name}"] = imbalance(sums["pickups"], sums["dropoffs"], MIN_PERIOD_TRIP_ENDS)
    late = weekend[weekend["hour"].isin(WEEKEND_LATE_NIGHT_HOURS)].groupby("zone_id")["pickups"].sum()
    f["weekend_late_night_share"] = late.reindex(f.index).fillna(0) / we_pickups.reindex(f.index)
    for name in ["morning", "pm_rush"]:
        f[f"persistence_{name}"] = period_persistence(
            base["zone_date_hour_counts"], WEEKDAY_PERIODS[name], date_weights).reindex(f.index)

    tw = totals.assign(w=totals["date"].map(date_weights).fillna(0))
    sums = tw.assign(**{c: tw[c] * tw["w"] for c in
                        ["pickups", "flex_pickups", "fare_eligible", "distance_eligible"]}
                     ).groupby("zone_id")[["pickups", "flex_pickups", "fare_eligible",
                                           "distance_eligible"]].sum().reindex(f.index)
    f["flex_share"] = sums["flex_pickups"] / sums["pickups"]
    f["fare_sample_coverage"] = sums["fare_eligible"] / sums["pickups"]
    f["fare_eligible_trips"] = sums["fare_eligible"]
    f["distance_eligible_trips"] = sums["distance_eligible"]

    hw = hist.assign(weight=hist["trips"] * hist["date"].map(date_weights).fillna(0))
    hw = hw[hw["weight"] > 0].groupby(["metric", "zone_id", "bin"], as_index=False)["weight"].sum()
    for metric, column in [("distance_all", "median_distance_mi"),
                           ("duration_all", "median_duration_min"),
                           ("distance_metered", "median_metered_distance_mi"),
                           ("fare_per_mile_metered", "median_fare_per_mile")]:
        width = HISTOGRAMS[metric][0]
        f[column] = weighted_median_from_histogram(hw[hw["metric"] == metric], width).reindex(f.index)
    # Flex exclusion bias: how much shorter metered trips are than all trips from the zone.
    f["metered_vs_all_distance_pct"] = 100 * (f["median_metered_distance_mi"]
                                              / f["median_distance_mi"] - 1)
    f["is_airport"] = f.index.isin(AIRPORT_ZONES)
    if zone_names is not None:
        f = f.join(zone_names.set_index("zone_id")[["zone", "borough"]])
    return f


def main() -> int:
    tables = build_base_tables()
    features = compute_features(tables, zone_names=read_zones())
    features.to_csv(ZONES_DIR / "zone_features_all.csv", float_format="%.6g", lineterminator="\n")
    print(f"zones with features: {len(features)}; with >= 1,000 pickups: "
          f"{int((features['pickups'] >= 1000).sum())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
