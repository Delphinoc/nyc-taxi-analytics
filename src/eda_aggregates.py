"""Build the Phase 3 aggregate tables from the flagged trip file.

Reads only the columns it needs from
data/processed/yellow_tripdata_2025-01_flagged.parquet (never the raw file,
never modifying either) and writes small CSV tables plus key_metrics.json to
data/processed/eda/. Charts and the report are built from these tables.

Every table states its denominator in its column names (e.g. ``pickups``
is a count of eligible rows, ``avg_pickups_per_day`` divides by the number of
calendar days of that day type).

Usage:
    python src/eda_aggregates.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from cleaning_rules import ELIGIBILITY_RULES
from eda_metrics import (
    AIRPORT_ZONES,
    MIN_CELL_TRIP_ENDS,
    PERIOD_ORDER,
    day_type,
    days_per_type,
    eligible_dropoff,
    flow_direction,
    hour_period,
    imbalance_index,
    persistence,
    standardized_mean,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLAGGED_PATH = PROJECT_ROOT / "data" / "processed" / "yellow_tripdata_2025-01_flagged.parquet"
ZONES_PATH = PROJECT_ROOT / "data" / "raw" / "taxi_zone_lookup.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "eda"

COLUMNS = [
    "VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID",
    "DOLocationID", "RatecodeID", "trip_distance", "fare_amount",
    "total_amount", "tip_amount", "trip_duration_min", "avg_speed_mph",
    "dq_invalid_location_id", "dq_negative_total_amount", "dq_negative_duration",
    "dq_zero_duration", "dq_duration_over_12h", "dq_zero_distance",
    "dq_implausible_speed", "scope_outside_period",
    "scope_flex_fare", "info_fare_outside_standard_meter_range",
    "eligible_demand", "eligible_distance_duration", "eligible_fare",
]
# Column subsets used when filtering, so no step copies the whole frame.
DEMAND_COLUMNS = ["pu_date", "pu_hour", "pu_day_type", "pu_period", "PULocationID",
                  "scope_flex_fare"]
TRIP_COLUMNS = ["pu_date", "pu_day_type", "pu_period", "trip_distance", "trip_duration_min",
                "avg_speed_mph", "scope_flex_fare"]
FARE_COLUMNS = ["pu_date", "pu_day_type", "pu_period", "PULocationID", "RatecodeID",
                "trip_distance", "trip_duration_min", "avg_speed_mph", "fare_amount",
                "total_amount", "tip_amount"]
DISTANCE_BANDS = [0, 1, 2, 3, 5, 10, np.inf]
FINE_DISTANCE_BANDS = [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 10, 15, 20, np.inf]
COARSE_DISTANCE_BANDS = [0, 2, 5, np.inf]
DURATION_BANDS = [0, 5, 10, 15, 20, 30, 45, 60, np.inf]
FINE_DURATION_BANDS = [0, 3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 90, np.inf]
COARSE_DURATION_BANDS = [0, 10, 20, 40, np.inf]
# A distance x duration stratum is used only if every period has this many trips in it.
MIN_STRATUM_TRIPS = 30
MIN_GROUP_TRIPS = 1_000  # fare/trip comparisons below this are flagged small-sample
# "Strongly imbalanced" weekday cell for the persistence checks.
STRONG_INDEX = 0.3
STRONG_DAILY_TRIP_ENDS = 50


def categorical_day_type(dates: pd.Series) -> pd.Categorical:
    """Day type per row, computed once per distinct date and stored as a category."""
    distinct = pd.Series(dates.unique())
    lookup = dict(zip(distinct, day_type(distinct)))
    return pd.Categorical(dates.map(lookup), categories=["weekday", "weekend", "holiday"])


def load_trips(path: Path = FLAGGED_PATH) -> pd.DataFrame:
    """Load the needed columns with compact dtypes and add calendar fields.

    Day types and periods are categoricals and the raw timestamps are dropped
    once derived, to keep the frame small on a low-memory machine.
    """
    trips = pq.read_table(path, columns=COLUMNS).to_pandas(self_destruct=True, split_blocks=True)
    for column in ["PULocationID", "DOLocationID", "VendorID"]:
        trips[column] = trips[column].astype("int16")
    pickup = trips["tpep_pickup_datetime"]
    dropoff = trips["tpep_dropoff_datetime"]
    trips["pu_date"] = pickup.dt.normalize()
    trips["pu_hour"] = pickup.dt.hour.astype("int8")
    trips["pu_day_type"] = categorical_day_type(trips["pu_date"])
    trips["do_date"] = dropoff.dt.normalize()
    trips["do_hour"] = dropoff.dt.hour.astype("int8")
    trips["do_day_type"] = categorical_day_type(trips["do_date"])
    trips["pu_period"] = pd.Categorical(hour_period(trips["pu_hour"]), categories=PERIOD_ORDER)
    trips["eligible_dropoff"] = eligible_dropoff(trips)
    return trips.drop(columns=["tpep_pickup_datetime", "tpep_dropoff_datetime"])


def read_zones(path: Path = ZONES_PATH) -> pd.DataFrame:
    zones = pd.read_csv(path, keep_default_na=False, na_values=[""])
    return zones.rename(columns={"LocationID": "zone_id", "Borough": "borough", "Zone": "zone"})


def zone_borough_lookup(zones: pd.DataFrame) -> dict:
    return dict(zip(zones["zone_id"], zones["borough"]))


def with_zone_names(frame: pd.DataFrame, zones: pd.DataFrame, key: str = "zone_id") -> pd.DataFrame:
    named = frame.merge(zones[["zone_id", "zone", "borough"]], left_on=key, right_on="zone_id",
                        how="left")
    return named.drop(columns="zone_id") if key != "zone_id" else named


# --------------------------------------------------------------------------- demand

def demand_tables(trips: pd.DataFrame, zones: pd.DataFrame, n_days: dict) -> dict:
    demand = trips.loc[trips["eligible_demand"], DEMAND_COLUMNS]
    total = len(demand)

    daily = (demand.groupby("pu_date").size().rename("pickups").reset_index()
             .rename(columns={"pu_date": "date"}))
    daily["day_type"] = day_type(daily["date"])
    daily["weekday_name"] = daily["date"].dt.day_name()
    daily["flex_pickups"] = demand.groupby("pu_date")["scope_flex_fare"].sum().to_numpy()
    daily["flex_share_pct"] = (100 * daily["flex_pickups"] / daily["pickups"]).round(2)

    hourly = (demand.groupby(["pu_day_type", "pu_hour"], observed=True).size()
              .rename("pickups").reset_index()
              .rename(columns={"pu_day_type": "day_type", "pu_hour": "hour"}))
    hourly["day_type"] = hourly["day_type"].astype(str)
    hourly["days"] = hourly["day_type"].map(n_days)
    hourly["avg_pickups_per_day"] = hourly["pickups"] / hourly["days"]
    hourly["share_of_day_type_pct"] = (
        100 * hourly["pickups"] / hourly.groupby("day_type")["pickups"].transform("sum")
    ).round(3)

    by_zone = demand.groupby("PULocationID").size().rename("pickups").reset_index()
    by_zone = with_zone_names(by_zone.rename(columns={"PULocationID": "zone_id"}), zones)
    by_zone = by_zone.sort_values("pickups", ascending=False).reset_index(drop=True)
    by_zone["rank"] = np.arange(1, len(by_zone) + 1)
    by_zone["share_pct"] = 100 * by_zone["pickups"] / total
    by_zone["cumulative_share_pct"] = by_zone["share_pct"].cumsum()
    for kind in ["weekday", "weekend"]:
        counts = demand[demand["pu_day_type"] == kind].groupby("PULocationID").size()
        by_zone[f"avg_pickups_per_{kind}_day"] = by_zone["zone_id"].map(counts).fillna(0) / n_days[kind]
    by_zone["weekend_to_weekday_ratio"] = (
        by_zone["avg_pickups_per_weekend_day"] / by_zone["avg_pickups_per_weekday_day"]
    )
    by_zone["is_airport"] = by_zone["zone_id"].isin(AIRPORT_ZONES)

    borough = (by_zone.groupby("borough")["pickups"].sum().sort_values(ascending=False)
               .rename("pickups").reset_index())
    borough["share_pct"] = 100 * borough["pickups"] / total

    # Peak geography: each zone's share of weekday pickups in each period and its peak hour.
    weekday = demand[demand["pu_day_type"] == "weekday"]
    zone_hour = weekday.groupby(["PULocationID", "pu_hour"]).size().unstack(fill_value=0)
    zone_period = weekday.groupby(["PULocationID", "pu_period"], observed=True).size().unstack(
        fill_value=0)
    peaks = pd.DataFrame({
        "zone_id": zone_hour.index,
        "weekday_pickups": zone_hour.sum(axis=1).to_numpy(),
        "peak_hour": zone_hour.idxmax(axis=1).to_numpy(),
        "peak_hour_share_pct": (100 * zone_hour.max(axis=1) / zone_hour.sum(axis=1)).to_numpy(),
    })
    for period in PERIOD_ORDER:
        peaks[f"share_{period.split(' ')[0]}_pct"] = (
            100 * zone_period[period] / zone_period.sum(axis=1)).reindex(peaks["zone_id"]).to_numpy()
    peaks = with_zone_names(peaks, zones)
    peaks["small_sample"] = peaks["weekday_pickups"] < MIN_GROUP_TRIPS

    borough_hour = demand[demand["pu_day_type"] == "weekday"].merge(
        zones[["zone_id", "borough"]], left_on="PULocationID", right_on="zone_id")
    borough_hour = (borough_hour.groupby(["borough", "pu_hour"]).size().rename("pickups")
                    .reset_index().rename(columns={"pu_hour": "hour"}))
    borough_hour["share_of_borough_pct"] = (
        100 * borough_hour["pickups"] / borough_hour.groupby("borough")["pickups"].transform("sum"))

    airports = demand[demand["PULocationID"].isin(AIRPORT_ZONES)]
    airport_hourly = (airports.groupby(["PULocationID", "pu_day_type", "pu_hour"], observed=True)
                      .size().rename("pickups").reset_index()
                      .rename(columns={"PULocationID": "zone_id", "pu_day_type": "day_type",
                                       "pu_hour": "hour"}))
    airport_hourly["day_type"] = airport_hourly["day_type"].astype(str)
    airport_hourly["avg_pickups_per_day"] = (
        airport_hourly["pickups"] / airport_hourly["day_type"].map(n_days))
    airport_hourly["zone"] = airport_hourly["zone_id"].map(AIRPORT_ZONES)

    return {
        "daily_pickups": daily,
        "hourly_pickups": hourly,
        "zone_pickups": by_zone,
        "borough_pickups": borough,
        "zone_peak_profile": peaks,
        "borough_hourly_weekday": borough_hour,
        "airport_hourly_pickups": airport_hourly,
    }


# --------------------------------------------------------------------------- flows

def zone_hour_counts(trips: pd.DataFrame, pickup_mask: pd.Series, dropoff_mask: pd.Series,
                     by_date: bool = False) -> pd.DataFrame:
    """Pickup and drop-off counts per zone x day type x hour (optionally x date)."""
    time_keys = ["date", "day_type", "hour"] if by_date else ["day_type", "hour"]
    pu_keys = ["PULocationID"] + (["pu_date"] if by_date else []) + ["pu_day_type", "pu_hour"]
    do_keys = ["DOLocationID"] + (["do_date"] if by_date else []) + ["do_day_type", "do_hour"]
    pu = trips.loc[pickup_mask, pu_keys]
    do = trips.loc[dropoff_mask, do_keys]
    pickups = pu.groupby(pu_keys, observed=True).size()
    pickups.index = pickups.index.set_names(["zone_id", *time_keys])
    dropoffs = do.groupby(do_keys, observed=True).size()
    dropoffs.index = dropoffs.index.set_names(["zone_id", *time_keys])
    counts = pd.concat([pickups.rename("pickups"), dropoffs.rename("dropoffs")], axis=1)
    counts = counts.fillna(0).astype("int64").reset_index()
    counts["day_type"] = counts["day_type"].astype(str)
    return counts


def persistence_checks(cells: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Persistence of strongly imbalanced weekday cells under stricter rules.

    "Strong" = weekday cell with |I| >= STRONG_INDEX and at least
    STRONG_DAILY_TRIP_ENDS trip ends per weekday on average. The sign each day
    is compared with both the majority daily sign and the sign of the monthly
    total, and zero-net days are either counted against persistence or left out.
    """
    keys = ["zone_id", "day_type", "hour"]
    weekday = cells[cells["day_type"] == "weekday"]
    strong = weekday[(weekday["imbalance_index"].abs() >= STRONG_INDEX)
                     & (weekday["avg_trip_ends_per_day"] >= STRONG_DAILY_TRIP_ENDS)]
    monthly_sign = np.sign(strong.set_index(keys)["net_flow"])
    rows = []
    for min_daily in [10, 25, 50]:
        for min_days in [5, 15, 21]:
            result = persistence(daily, keys, min_daily, min_days).set_index(keys)
            result = result.reindex(monthly_sign.index)
            days = result["qualifying_days"]
            zeros = result["zero_net_days"]
            outbound = result["outbound_day_share"] * days
            inbound = result["inbound_day_share"] * days
            agree_monthly = np.where(monthly_sign > 0, outbound, inbound)
            share_monthly = pd.Series(agree_monthly, index=result.index) / days
            share_no_zero = result[["outbound_day_share", "inbound_day_share"]].max(axis=1) * days / (
                days - zeros)
            majority_sign = np.sign(result["outbound_day_share"] - result["inbound_day_share"])
            evaluable = result["same_sign_share"].notna()
            rows.append({
                "min_daily_trip_ends": min_daily,
                "min_qualifying_days": min_days,
                "strong_cells": len(strong),
                "evaluable_cells": int(evaluable.sum()),
                "share_ge_90pct_majority_sign": int((result["same_sign_share"] >= 0.9).sum()),
                "share_ge_90pct_monthly_sign": int(((share_monthly >= 0.9) & evaluable).sum()),
                "share_ge_90pct_zero_days_excluded": int(((share_no_zero >= 0.9) & evaluable).sum()),
                "same_sign_every_day": int((result["same_sign_share"] == 1).sum()),
                "majority_equals_monthly_sign": int(((majority_sign == monthly_sign) & evaluable).sum()),
                "zero_net_days_total": int(zeros[evaluable].sum()),
            })
    return pd.DataFrame(rows)


def flow_tables(trips: pd.DataFrame, zones: pd.DataFrame, n_days: dict) -> dict:
    pickup_mask = trips["eligible_demand"]
    dropoff_mask = trips["eligible_dropoff"]

    cells = zone_hour_counts(trips, pickup_mask, dropoff_mask)
    cells["days"] = cells["day_type"].map(n_days)
    cells["trip_ends"] = cells["pickups"] + cells["dropoffs"]
    cells["net_flow"] = cells["pickups"] - cells["dropoffs"]
    cells["avg_net_flow_per_day"] = cells["net_flow"] / cells["days"]
    cells["avg_trip_ends_per_day"] = cells["trip_ends"] / cells["days"]
    cells["imbalance_index"] = imbalance_index(cells["pickups"], cells["dropoffs"])
    cells["low_volume"] = cells["trip_ends"] < MIN_CELL_TRIP_ENDS
    cells["direction"] = flow_direction(cells["net_flow"])

    daily = zone_hour_counts(trips, pickup_mask, dropoff_mask, by_date=True)
    persist = persistence(daily, ["zone_id", "day_type", "hour"])
    persistence_sensitivity = persistence_checks(cells, daily)
    cells = cells.merge(persist, on=["zone_id", "day_type", "hour"], how="left")
    cells = with_zone_names(cells, zones)

    # Zone-day totals: whole-day net flow per zone, averaged per day of each type.
    zone_day = cells.groupby(["zone_id", "zone", "borough", "day_type"], as_index=False)[
        ["pickups", "dropoffs", "trip_ends", "net_flow"]].sum()
    zone_day["days"] = zone_day["day_type"].map(n_days)
    zone_day["avg_net_flow_per_day"] = zone_day["net_flow"] / zone_day["days"]
    zone_day["imbalance_index"] = imbalance_index(zone_day["pickups"], zone_day["dropoffs"])

    # Sensitivity: count both ends only for trips valid at both ends.
    both = pickup_mask & dropoff_mask
    both_cells = zone_hour_counts(trips, both, both)
    both_cells["imbalance_index_both_ends"] = imbalance_index(both_cells["pickups"],
                                                              both_cells["dropoffs"])
    compare = cells[["zone_id", "day_type", "hour", "imbalance_index"]].merge(
        both_cells[["zone_id", "day_type", "hour", "imbalance_index_both_ends"]],
        on=["zone_id", "day_type", "hour"], how="inner").dropna()

    totals = {
        "pickup_eligible_rows": int(pickup_mask.sum()),
        "dropoff_eligible_rows": int(dropoff_mask.sum()),
        "both_ends_eligible_rows": int(both.sum()),
        "pickup_only_rows": int((pickup_mask & ~dropoff_mask).sum()),
        "dropoff_only_rows": int((dropoff_mask & ~pickup_mask).sum()),
        "cells": len(cells),
        "stable_cells": int((~cells["low_volume"]).sum()),
        "trip_ends_in_stable_cells_pct": float(
            100 * cells.loc[~cells["low_volume"], "trip_ends"].sum() / cells["trip_ends"].sum()),
        "both_ends_sensitivity": {
            "cells_compared": len(compare),
            "pearson_r": float(compare["imbalance_index"].corr(compare["imbalance_index_both_ends"])),
            "median_abs_difference": float(
                (compare["imbalance_index"] - compare["imbalance_index_both_ends"]).abs().median()),
            "sign_agreement_pct": float(100 * (np.sign(compare["imbalance_index"])
                                               == np.sign(compare["imbalance_index_both_ends"])).mean()),
        },
    }
    return ({"zone_hour_flow": cells, "zone_day_flow": zone_day,
             "persistence_sensitivity": persistence_sensitivity}, totals)


# --------------------------------------------------------------------------- trips & fares

def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation (Pearson on average ranks), without scipy."""
    return float(a.rank().corr(b.rank()))


def describe(series: pd.Series) -> dict:
    q = series.quantile([0.1, 0.25, 0.5, 0.75, 0.9, 0.99])
    return {"n": int(series.size), "p10": q[0.1], "p25": q[0.25], "median": q[0.5],
            "p75": q[0.75], "p90": q[0.9], "p99": q[0.99], "mean": series.mean()}


def trip_tables(trips: pd.DataFrame) -> dict:
    dd = trips.loc[trips["eligible_distance_duration"], TRIP_COLUMNS]
    rows = []
    for (period, kind), group in dd.groupby(["pu_period", "pu_day_type"], observed=True):
        for metric in ["trip_distance", "trip_duration_min", "avg_speed_mph"]:
            rows.append({"period": period, "day_type": kind, "metric": metric,
                         **describe(group[metric])})
    for metric in ["trip_distance", "trip_duration_min", "avg_speed_mph"]:
        rows.append({"period": "all", "day_type": "all", "metric": metric, **describe(dd[metric])})
        for flex, group in dd.groupby("scope_flex_fare"):
            rows.append({"period": "all", "day_type": "flex" if flex else "metered",
                         "metric": metric, **describe(group[metric])})
    table = pd.DataFrame(rows)
    table["small_sample"] = table["n"] < MIN_GROUP_TRIPS

    hist_distance = np.histogram(dd["trip_distance"].clip(upper=30), bins=np.arange(0, 30.5, 0.5))
    hist_duration = np.histogram(dd["trip_duration_min"].clip(upper=90), bins=np.arange(0, 91, 2))
    hist = pd.concat([
        pd.DataFrame({"metric": "trip_distance", "bin_start": hist_distance[1][:-1],
                      "bin_end": hist_distance[1][1:], "trips": hist_distance[0]}),
        pd.DataFrame({"metric": "trip_duration_min", "bin_start": hist_duration[1][:-1],
                      "bin_end": hist_duration[1][1:], "trips": hist_duration[0]}),
    ])
    return {"trip_characteristics": table, "trip_histograms": hist}


def adjust_by_period(std: pd.DataFrame, value: str, distance_bins, duration_bins,
                     min_cell: int) -> pd.DataFrame:
    """Raw and distance x duration standardised means of ``value`` per period."""
    banded = std.assign(
        distance_band=pd.cut(std["trip_distance"], distance_bins, right=False),
        duration_band=pd.cut(std["trip_duration_min"], duration_bins, right=False),
    )
    result = standardized_mean(banded, value, "pu_period", ["distance_band", "duration_band"],
                               min_cell=min_cell)
    midday = result.set_index("pu_period").loc["midday (10-15)"]
    result["raw_vs_midday_pct"] = 100 * (result["raw_mean"] / midday["raw_mean"] - 1)
    result["adjusted_vs_midday_pct"] = 100 * (
        result["standardized_mean"] / midday["standardized_mean"] - 1)
    return result


def peak_adjustment(std: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Main peak-vs-midday table and its sensitivity to binning and sparse strata."""
    def quantile_bins(series: pd.Series) -> list:
        edges = np.unique(series.quantile(np.linspace(0, 0.9, 10)).to_numpy())
        return [*edges, np.inf]

    schemes = {
        "main": (DISTANCE_BANDS, DURATION_BANDS),
        "fine": (FINE_DISTANCE_BANDS, FINE_DURATION_BANDS),
        "coarse": (COARSE_DISTANCE_BANDS, COARSE_DURATION_BANDS),
        "deciles": (quantile_bins(std["trip_distance"]), quantile_bins(std["trip_duration_min"])),
    }
    rows = []
    for value in ["fare_amount", "charge_excl_tip"]:
        for scheme, (distance_bins, duration_bins) in schemes.items():
            for min_cell in [1, MIN_STRATUM_TRIPS, 100]:
                table = adjust_by_period(std, value, distance_bins, duration_bins, min_cell)
                rows.append(table.assign(measure=value, bins=scheme, min_cell=min_cell))
    sensitivity_table = pd.concat(rows, ignore_index=True)

    main = sensitivity_table[(sensitivity_table["bins"] == "main")
                             & (sensitivity_table["min_cell"] == MIN_STRATUM_TRIPS)]
    distance_only = standardized_mean(
        std.assign(distance_band=pd.cut(std["trip_distance"], DISTANCE_BANDS, right=False)),
        "fare_amount", "pu_period", ["distance_band"], min_cell=MIN_STRATUM_TRIPS,
    ).set_index("pu_period")
    base = main[main["measure"] == "fare_amount"].set_index("pu_period")
    charge = main[main["measure"] == "charge_excl_tip"].set_index("pu_period")
    adjusted = pd.DataFrame({
        "n": base["n"],
        "base_fare_raw_mean": base["raw_mean"],
        "base_fare_distance_adjusted_mean": distance_only["standardized_mean"],
        "base_fare_adjusted_mean": base["standardized_mean"],
        "charge_raw_mean": charge["raw_mean"],
        "charge_adjusted_mean": charge["standardized_mean"],
        "weight_covered": base["weight_covered"],
        "strata_kept": base["strata_kept"],
        "strata_total": base["strata_total"],
    })
    midday = adjusted.loc["midday (10-15)"]
    for column in ["base_fare_raw_mean", "base_fare_distance_adjusted_mean",
                   "base_fare_adjusted_mean", "charge_raw_mean", "charge_adjusted_mean"]:
        adjusted[f"{column}_vs_midday_pct"] = 100 * (adjusted[column] / midday[column] - 1)
    adjusted = adjusted.reindex(PERIOD_ORDER).reset_index(names="pu_period")

    summary = (sensitivity_table.groupby(["measure", "pu_period"])["adjusted_vs_midday_pct"]
               .agg(["min", "max"]).reset_index())
    return adjusted, sensitivity_table.merge(summary, on=["measure", "pu_period"], how="left")


def fare_tables(trips: pd.DataFrame, zones: pd.DataFrame) -> tuple[dict, dict]:
    fare = trips.loc[trips["eligible_fare"], FARE_COLUMNS].copy()
    fare["fare_per_mile"] = (fare["fare_amount"] / fare["trip_distance"]).where(
        fare["trip_distance"] >= 1)
    fare["fare_per_minute"] = (fare["fare_amount"] / fare["trip_duration_min"]).where(
        fare["trip_duration_min"] >= 5)
    fare["ratecode"] = fare["RatecodeID"].map(
        {1: "1 standard", 2: "2 JFK flat", 3: "3 Newark", 4: "4 Nassau/Westchester",
         5: "5 negotiated", 6: "6 group", 99: "99 unknown"})
    borough_of = zone_borough_lookup(zones)
    fare["borough"] = fare["PULocationID"].map(borough_of).astype("category")

    def summarise(group_keys: list[str], frame: pd.DataFrame) -> pd.DataFrame:
        grouped = frame.groupby(group_keys, observed=True)
        out = grouped.agg(
            trips=("fare_amount", "size"),
            fare_mean=("fare_amount", "mean"),
            fare_median=("fare_amount", "median"),
            distance_median=("trip_distance", "median"),
            duration_median=("trip_duration_min", "median"),
            speed_median=("avg_speed_mph", "median"),
            fare_per_mile_median=("fare_per_mile", "median"),
            fare_per_minute_median=("fare_per_minute", "median"),
        )
        spread = grouped["fare_amount"].quantile([0.25, 0.75, 0.9]).unstack()
        spread.columns = ["fare_p25", "fare_p75", "fare_p90"]
        out = out.join(spread).reset_index()
        out["small_sample"] = out["trips"] < MIN_GROUP_TRIPS
        return out

    by_period = summarise(["pu_day_type", "pu_period"], fare)
    by_ratecode = summarise(["ratecode"], fare)
    by_borough = summarise(["borough"], fare)
    by_zone = summarise(["PULocationID"], fare).rename(columns={"PULocationID": "zone_id"})
    by_zone = with_zone_names(by_zone, zones).sort_values("trips", ascending=False)

    # Peak vs off-peak after accounting for distance and duration (standard rate,
    # weekdays only, since the rush-hour window is a weekday concept). Two
    # measures: the metered base fare (fare_amount) and what the passenger was
    # charged excluding tips (total_amount - tip_amount), which adds the
    # rush-hour, overnight, congestion and other surcharges.
    std = fare.loc[(fare["RatecodeID"] == 1) & (fare["pu_day_type"] == "weekday"),
                   ["pu_period", "trip_distance", "trip_duration_min", "fare_amount",
                    "total_amount", "tip_amount"]].copy()
    std["charge_excl_tip"] = std["total_amount"] - std["tip_amount"]
    std["pu_period"] = std["pu_period"].astype(str)
    adjusted, sensitivity_table = peak_adjustment(std)

    # Flex Fare composition: what share of each day's / hour's eligible pickups
    # are Flex, and how the fare-eligible sample's mix shifts with it.
    demand = trips.loc[trips["eligible_demand"], DEMAND_COLUMNS]
    flex_hourly = (demand.groupby(["pu_day_type", "pu_hour"], observed=True)["scope_flex_fare"]
                   .agg(["size", "sum"]).reset_index()
                   .rename(columns={"size": "pickups", "sum": "flex_pickups",
                                    "pu_day_type": "day_type", "pu_hour": "hour"}))
    flex_hourly["day_type"] = flex_hourly["day_type"].astype(str)
    flex_hourly["flex_share_pct"] = 100 * flex_hourly["flex_pickups"] / flex_hourly["pickups"]

    dd = trips.loc[trips["eligible_distance_duration"], TRIP_COLUMNS]
    daily_mix = dd.groupby(["pu_date", "scope_flex_fare"]).agg(
        trips=("trip_distance", "size"), distance_median=("trip_distance", "median"),
        duration_median=("trip_duration_min", "median")).reset_index()
    daily_mix["segment"] = np.where(daily_mix["scope_flex_fare"], "flex", "metered")
    daily_fare = fare.groupby("pu_date").agg(
        fare_eligible_trips=("fare_amount", "size"), fare_median=("fare_amount", "median"),
        fare_mean=("fare_amount", "mean"),
        distance_median=("trip_distance", "median")).reset_index()
    demand_daily = demand.groupby("pu_date").agg(
        pickups=("scope_flex_fare", "size"), flex_pickups=("scope_flex_fare", "sum")).reset_index()
    daily_fare = daily_fare.merge(demand_daily, on="pu_date")
    daily_fare["fare_eligible_pct_of_pickups"] = (
        100 * daily_fare["fare_eligible_trips"] / daily_fare["pickups"])
    daily_fare["flex_share_pct"] = 100 * daily_fare["flex_pickups"] / daily_fare["pickups"]

    # Representativeness: which pickup mix (hour, borough, distance) the fare
    # sample covers compared with all eligible pickups.
    demand_b = demand[["pu_period", "scope_flex_fare"]].assign(
        borough=demand["PULocationID"].map(borough_of))
    mix = []
    for label, frame in [("all eligible pickups", demand_b), ("fare-eligible (metered)", fare),
                         ("flex pickups", demand_b[demand_b["scope_flex_fare"]])]:
        for dim in ["pu_period", "borough"]:
            share = frame[dim].astype(str).value_counts(normalize=True) * 100
            mix += [{"sample": label, "dimension": dim, "value": k, "share_pct": v}
                    for k, v in share.items()]
    representativeness = pd.DataFrame(mix)

    corr = daily_fare.loc[daily_fare["pu_date"].dt.day > 1]
    metrics = {
        "fare_eligible_trips": len(fare),
        "fare_median_overall": float(fare["fare_amount"].median()),
        "daily_corr_flex_share_vs_metered_fare_mean": float(
            spearman(corr["flex_share_pct"], corr["fare_mean"])),
        "daily_corr_flex_share_vs_metered_distance_median": float(
            spearman(corr["flex_share_pct"], corr["distance_median"])),
        "flex_total_amount_median": float(
            trips.loc[trips["eligible_distance_duration"] & trips["scope_flex_fare"],
                      "total_amount"].median()),
        "metered_total_amount_median": float(fare["total_amount"].median()),
    }
    tables = {
        "fare_by_period": by_period,
        "fare_by_ratecode": by_ratecode,
        "fare_by_borough": by_borough,
        "fare_by_zone": by_zone,
        "fare_peak_adjusted": adjusted,
        "fare_peak_adjustment_sensitivity": sensitivity_table,
        "flex_share_hourly": flex_hourly,
        "flex_daily_trip_mix": daily_mix.drop(columns="scope_flex_fare"),
        "fare_daily": daily_fare,
        "sample_representativeness": representativeness,
    }
    return tables, metrics


# --------------------------------------------------------------------------- sensitivity

def sensitivity(trips: pd.DataFrame, n_days: dict) -> pd.DataFrame:
    """How key metrics move under alternative cleaning or definitional choices."""
    rows = []
    demand = trips["eligible_demand"]

    def top_zones(mask, n=10):
        return list(trips.loc[mask].groupby("PULocationID").size().nlargest(n).index)

    def add(check, baseline, alternative, note=""):
        rows.append({"check": check, "baseline": baseline, "alternative": alternative,
                     "change_pct": 100 * (alternative / baseline - 1), "note": note})

    base_top = top_zones(demand)
    alt_mask = demand & ~trips["dq_zero_distance"] & ~trips["dq_zero_duration"]
    alt_top = top_zones(alt_mask)
    add("demand rows: also drop zero-distance and zero-duration rows",
        int(demand.sum()), int(alt_mask.sum()),
        f"top-10 zones shared: {len(set(alt_top) & set(base_top))}/10; "
        f"entering {sorted(set(alt_top) - set(base_top))}, leaving {sorted(set(base_top) - set(alt_top))}")
    add("demand rows: drop VendorID 7 (no dropoff times)",
        int(demand.sum()), int((demand & (trips["VendorID"] != 7)).sum()))

    weekday = demand & (trips["pu_day_type"] == "weekday")
    holiday = demand & (trips["pu_day_type"] == "holiday")
    base_avg = weekday.sum() / n_days["weekday"]
    add("weekday avg pickups/day: count holidays as weekdays", base_avg,
        (weekday.sum() + holiday.sum()) / (n_days["weekday"] + n_days["holiday"]),
        "holidays = 1 and 20 Jan")
    first_week = trips["pu_date"] < pd.Timestamp("2025-01-06")
    later_weekdays = weekday & ~first_week
    n_later = trips.loc[later_weekdays, "pu_date"].nunique()
    add("weekday avg pickups/day: drop 2-3 Jan (post-holiday week)", base_avg,
        later_weekdays.sum() / n_later, f"{n_later} weekdays remain")

    # Speed cap: rebuild distance/duration eligibility without the speed rule,
    # then apply alternative caps.
    other_rules = [r for r in ELIGIBILITY_RULES["eligible_distance_duration"]
                   if r != "dq_implausible_speed"]
    no_speed = ~trips[other_rules].any(axis=1)
    base = trips.loc[trips["eligible_distance_duration"], ["trip_distance", "avg_speed_mph"]]
    for cap in [50, 100, None]:
        alt = no_speed & ~(trips["avg_speed_mph"] > cap) if cap else no_speed
        label = f"{cap} mph" if cap else "no cap"
        for metric in ["trip_distance", "avg_speed_mph"]:
            add(f"{metric} p99: speed cap {label} instead of 80 mph",
                base[metric].quantile(0.99), trips.loc[alt, metric].quantile(0.99),
                f"rows {len(base):,} -> {int(alt.sum()):,}")

    fare = trips["eligible_fare"]
    alt = fare & ~trips["info_fare_outside_standard_meter_range"]
    add("fare_amount mean: also drop meter-range info flag",
        trips.loc[fare, "fare_amount"].mean(), trips.loc[alt, "fare_amount"].mean(),
        f"rows {int(fare.sum()):,} -> {int(alt.sum()):,}")
    return pd.DataFrame(rows)


def threshold_sensitivity(flow: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for threshold in [50, 100, 200, 400]:
        stable = flow["trip_ends"] >= threshold
        index = imbalance_index(flow["pickups"], flow["dropoffs"], threshold)
        rows.append({
            "min_trip_ends": threshold,
            "stable_cells": int(stable.sum()),
            "stable_cells_pct": 100 * stable.mean(),
            "trip_ends_covered_pct": 100 * flow.loc[stable, "trip_ends"].sum() / flow["trip_ends"].sum(),
            "weekday_cells_abs_index_over_0_5": int(
                ((index.abs() > 0.5) & (flow["day_type"] == "weekday")).sum()),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- main

def build(flagged_path: Path = FLAGGED_PATH, output_dir: Path = OUTPUT_DIR) -> dict:
    started = time.perf_counter()
    trips = load_trips(flagged_path)
    zones = read_zones()
    n_days = days_per_type()

    tables = {}
    tables.update(demand_tables(trips, zones, n_days))
    flows, flow_metrics = flow_tables(trips, zones, n_days)
    tables.update(flows)
    tables.update(trip_tables(trips))
    fares, fare_metrics = fare_tables(trips, zones)
    tables.update(fares)
    tables["sensitivity"] = sensitivity(trips, n_days)
    tables["imbalance_threshold_sensitivity"] = threshold_sensitivity(tables["zone_hour_flow"])

    demand_rows = int(trips["eligible_demand"].sum())
    metrics = {
        "rows_total": len(trips),
        "days_per_type": n_days,
        "eligible_rows": {c: int(trips[c].sum()) for c in
                          ["eligible_demand", "eligible_dropoff", "eligible_distance_duration",
                           "eligible_fare"]},
        "exclusion_rate_pct": {c: 100 * (1 - trips[c].mean()) for c in
                               ["eligible_demand", "eligible_dropoff",
                                "eligible_distance_duration", "eligible_fare"]},
        "demand_rows": demand_rows,
        "flows": flow_metrics,
        "fares": fare_metrics,
    }
    del trips

    output_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(output_dir / f"{name}.csv", index=False, float_format="%.6g",
                     lineterminator="\n")
    (output_dir / "key_metrics.json").write_text(
        json.dumps(metrics, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    metrics["seconds"] = round(time.perf_counter() - started, 1)
    metrics["peak_arrow_memory_mb"] = round(pa.default_memory_pool().max_memory() / 1024**2, 1)
    return metrics


def main() -> int:
    metrics = build()
    print(json.dumps({k: metrics[k] for k in ["eligible_rows", "seconds", "peak_arrow_memory_mb"]},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
