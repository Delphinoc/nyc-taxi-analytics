"""Metric definitions for the Phase 3 exploratory analysis.

Every function here is pure (no file access) so it can be unit-tested on
small synthetic frames. Definitions, denominators and thresholds are
documented in docs/eda_metrics.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PERIOD_START = pd.Timestamp("2025-01-01")
PERIOD_END = pd.Timestamp("2025-02-01")  # exclusive

# Federal holidays in January 2025 (New Year's Day, Martin Luther King Jr. Day).
# They get their own day type so they don't distort weekday averages.
HOLIDAYS = (pd.Timestamp("2025-01-01").date(), pd.Timestamp("2025-01-20").date())

# Hour-of-day periods. 16-19 matches the TLC weekday rush-hour surcharge window
# (4-8 pm); the others are contiguous blocks chosen for readability.
PERIODS = {
    "overnight (00-05)": range(0, 6),
    "morning (06-09)": range(6, 10),
    "midday (10-15)": range(10, 16),
    "pm rush (16-19)": range(16, 20),
    "evening (20-23)": range(20, 24),
}
PERIOD_ORDER = list(PERIODS)

AIRPORT_ZONES = {1: "Newark Airport", 132: "JFK Airport", 138: "LaGuardia Airport"}
NYC_ZONE_IDS = range(1, 264)  # 264 = Unknown, 265 = Outside of NYC

# A zone-hour cell needs at least this many trip ends (pickups + dropoffs) over
# the month before its imbalance index is reported. For a share-based index
# I = (P - D) / (P + D), the standard error is at most 1 / sqrt(n), so 100
# trip ends keeps it at or below 0.10.
MIN_CELL_TRIP_ENDS = 100
# A single day's cell needs this many trip ends to count towards persistence.
MIN_DAILY_TRIP_ENDS = 10
# Persistence is only reported when at least this many qualifying days exist.
MIN_PERSISTENCE_DAYS = 5

DROPOFF_INVALID_FLAGS = [
    "dq_invalid_location_id",
    "dq_negative_total_amount",
    "dq_negative_duration",
    "dq_zero_duration",
    "dq_duration_over_12h",
]


def day_type(dates: pd.Series) -> pd.Series:
    """Classify each timestamp's date as 'weekday', 'weekend' or 'holiday'."""
    day = dates.dt.date
    weekend = dates.dt.day_of_week >= 5
    result = np.where(day.isin(HOLIDAYS), "holiday", np.where(weekend, "weekend", "weekday"))
    return pd.Series(result, index=dates.index, dtype="object")


def days_per_type(start: pd.Timestamp = PERIOD_START, end: pd.Timestamp = PERIOD_END) -> dict:
    """Number of calendar days of each day type in [start, end)."""
    dates = pd.Series(pd.date_range(start, end, freq="D", inclusive="left"))
    return day_type(dates).value_counts().to_dict()


def hour_period(hours: pd.Series) -> pd.Series:
    """Map hour of day (0-23) to the named period in PERIODS."""
    lookup = {hour: name for name, hours_in in PERIODS.items() for hour in hours_in}
    return hours.map(lookup)


def eligible_dropoff(trips: pd.DataFrame) -> pd.Series:
    """Rows whose drop-off end is usable for zone-hour drop-off counts.

    Validated independently of pickup eligibility: the drop-off time must be
    in January 2025 and recorded (positive duration up to 12 h), the record
    must not be a negative adjustment, and the drop-off zone must be a known
    NYC zone (1-263).
    """
    dropoff = trips["tpep_dropoff_datetime"]
    in_period = (dropoff >= PERIOD_START) & (dropoff < PERIOD_END)
    known_zone = trips["DOLocationID"].isin(NYC_ZONE_IDS)
    invalid = trips[DROPOFF_INVALID_FLAGS].any(axis=1)
    return in_period & known_zone & ~invalid


def imbalance_index(pickups, dropoffs, min_trip_ends: int = MIN_CELL_TRIP_ENDS) -> pd.Series:
    """(P - D) / (P + D), or NaN when P + D is below ``min_trip_ends``.

    Ranges from -1 (only drop-offs: net inbound) to +1 (only pickups: net
    outbound). NaN marks cells too small for a stable estimate, including
    P + D = 0.
    """
    pickups = pd.Series(pickups, dtype="float64")
    dropoffs = pd.Series(dropoffs, dtype="float64").set_axis(pickups.index)
    trip_ends = pickups + dropoffs
    index = (pickups - dropoffs) / trip_ends.where(trip_ends > 0)
    return index.where(trip_ends >= min_trip_ends)


def flow_direction(net_flow: pd.Series) -> pd.Series:
    """Label net flow (pickups - drop-offs) as net outbound, net inbound or balanced."""
    return pd.Series(
        np.select([net_flow > 0, net_flow < 0], ["net outbound", "net inbound"], "balanced"),
        index=net_flow.index,
    )


def persistence(
    daily: pd.DataFrame,
    keys: list[str],
    min_daily_trip_ends: int = MIN_DAILY_TRIP_ENDS,
    min_days: int = MIN_PERSISTENCE_DAYS,
) -> pd.DataFrame:
    """How consistently each cell's net-flow sign repeats across days.

    ``daily`` has one row per cell and date with ``pickups`` and ``dropoffs``.
    Only days with at least ``min_daily_trip_ends`` trip ends qualify. Per
    cell it returns the qualifying days, the shares of those days that were
    net outbound and net inbound, the number of zero-net days, and
    ``same_sign_share``, the share of qualifying days agreeing with the
    majority sign. Zero-net days stay in the denominator, so they count
    against persistence. Cells with fewer than ``min_days`` qualifying days
    get NaN shares.
    """
    trip_ends = daily["pickups"] + daily["dropoffs"]
    qualifying = daily[trip_ends >= min_daily_trip_ends]
    net = qualifying["pickups"] - qualifying["dropoffs"]
    summary = (
        qualifying.assign(_outbound=(net > 0).astype(float), _inbound=(net < 0).astype(float),
                          _zero=(net == 0).astype(int))
        .groupby(keys)
        .agg(qualifying_days=("_outbound", "size"),
             outbound_day_share=("_outbound", "mean"),
             inbound_day_share=("_inbound", "mean"),
             zero_net_days=("_zero", "sum"))
        .reset_index()
    )
    enough = summary["qualifying_days"] >= min_days
    summary["same_sign_share"] = summary[["outbound_day_share", "inbound_day_share"]].max(axis=1)
    for column in ["outbound_day_share", "inbound_day_share", "same_sign_share"]:
        summary[column] = summary[column].where(enough)
    return summary


def standardized_mean(
    frame: pd.DataFrame, value: str, group: str, strata: list[str], min_cell: int = 1
) -> pd.DataFrame:
    """Mean of ``value`` per group, direct-standardised on a common support.

    A stratum (e.g. a distance band x duration band) is kept only if *every*
    group has at least ``min_cell`` rows in it, so all groups are averaged over
    exactly the same strata with exactly the same weights: each kept stratum's
    share of the pooled rows in kept strata. ``weight_covered`` is the share of
    all rows that fall in kept strata, and ``strata_kept`` / ``strata_total``
    count the strata.
    """
    counts = frame.groupby([*strata, group], observed=True).size().unstack(group, fill_value=0)
    keep = (counts >= max(min_cell, 1)).all(axis=1)
    pooled = counts[keep].sum(axis=1)
    weights = pooled / pooled.sum()
    means = frame.groupby([*strata, group], observed=True)[value].mean().unstack(group)
    rows = []
    for name in counts.columns:
        in_group = frame[group] == name
        rows.append({
            group: name,
            "n": int(in_group.sum()),
            "raw_mean": frame.loc[in_group, value].mean(),
            "standardized_mean": float((means.loc[keep[keep].index, name] * weights).sum()),
            "weight_covered": float(pooled.sum() / counts.to_numpy().sum()),
            "strata_kept": int(keep.sum()),
            "strata_total": len(keep),
        })
    return pd.DataFrame(rows)
