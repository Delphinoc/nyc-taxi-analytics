"""Validation rules for January 2025 Yellow Taxi trip records.

Rules only *flag* rows; nothing is deleted or imputed. Flags come in three kinds:

- ``dq_*``    data-quality: the record itself is unreliable for some purpose.
- ``scope_*`` analytical scope: the record may be valid but is outside what a
              given analysis studies (wrong period, unknown zone, Flex Fare...).
- ``info_*``  informational only; never used to exclude a row.

Eligibility flags combine these per analytical purpose. Rule thresholds and
their justification are documented in docs/cleaning_rules.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PERIOD_START = pd.Timestamp("2025-01-01")
PERIOD_END = pd.Timestamp("2025-02-01")  # exclusive

VALID_LOCATION_IDS = range(1, 266)  # taxi_zone_lookup.csv: 1..265
UNKNOWN_OR_OUTSIDE_NYC_ZONES = (264, 265)  # 264 = Unknown, 265 = Outside of NYC

MAX_DURATION_MIN = 12 * 60
MAX_AVG_SPEED_MPH = 80.0

FLEX_FARE_PAYMENT_TYPE = 0
# 3 = No charge, 4 = Dispute, 5 = Unknown, 6 = Voided trip
FARE_NOT_SETTLED_PAYMENT_TYPES = (3, 4, 5, 6)

# Standard City Rate (RatecodeID 1), TLC taxi-fare page: $3.00 initial charge,
# $0.70 per 1/5 mile above 12 mph, $0.70 per minute when slow or stopped.
# Used only for an informational range check: a violation cannot tell whether
# the fare, the distance or the rate code is wrong (see docs/cleaning_rules.md).
STANDARD_RATECODE = 1
METER_INITIAL_CHARGE = 3.00
METER_RATE_PER_MILE = 3.50
METER_RATE_PER_MINUTE = 0.70
# The meter charges distance *or* time at any instant, so charging both for the
# whole trip is a strict upper bound. Two extra increments absorb rounding.
METER_BOUND_TOLERANCE = 1.40

AMOUNT_COMPONENTS = [
    "fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
    "improvement_surcharge", "congestion_surcharge", "Airport_fee", "cbd_congestion_fee",
]
REVERSAL_KEY = [
    "VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID",
]

DQ_FLAGS = [
    "dq_invalid_location_id",
    "dq_negative_duration",
    "dq_zero_duration",
    "dq_duration_over_12h",
    "dq_implausible_speed",
    "dq_zero_distance",
    "dq_negative_total_amount",
    "dq_nonpositive_fare",
]
SCOPE_FLAGS = [
    "scope_outside_period",
    "scope_pickup_zone_unknown_or_outside_nyc",
    "scope_dropoff_zone_unknown_or_outside_nyc",
    "scope_flex_fare",
    "scope_fare_not_settled",
]
INFO_FLAGS = [
    "info_negative_row_has_exact_reversal_partner",
    "info_negative_row_match_ambiguous",
    "info_total_differs_from_components",
    "info_fare_outside_standard_meter_range",
]

# Flags that make a row ineligible, per purpose. Order is the waterfall order.
ELIGIBILITY_RULES = {
    "eligible_demand": [
        "scope_outside_period",
        "dq_invalid_location_id",
        "dq_negative_total_amount",
        "dq_negative_duration",
        "scope_pickup_zone_unknown_or_outside_nyc",
    ],
    "eligible_distance_duration": [
        "scope_outside_period",
        "dq_invalid_location_id",
        "dq_negative_total_amount",
        "dq_negative_duration",
        "dq_zero_duration",
        "dq_duration_over_12h",
        "dq_zero_distance",
        "dq_implausible_speed",
    ],
    "eligible_fare": [
        "scope_outside_period",
        "dq_invalid_location_id",
        "dq_negative_total_amount",
        "dq_negative_duration",
        "dq_zero_duration",
        "dq_duration_over_12h",
        "dq_zero_distance",
        "dq_implausible_speed",
        "scope_flex_fare",
        "scope_fare_not_settled",
        "dq_nonpositive_fare",
    ],
    "eligible_clustering": [
        "scope_outside_period",
        "dq_invalid_location_id",
        "dq_negative_total_amount",
        "dq_negative_duration",
        "dq_zero_duration",
        "dq_duration_over_12h",
        "dq_zero_distance",
        "dq_implausible_speed",
        "scope_pickup_zone_unknown_or_outside_nyc",
        "scope_dropoff_zone_unknown_or_outside_nyc",
    ],
}


def find_reversal_pairs(keys: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Match negative-total rows one-to-one with positive rows they exactly negate.

    A candidate pair shares VendorID, both timestamps and both zones, and the
    negative row's total_amount (in whole cents) is the exact negative of the
    positive row's. A pair is accepted only when its (key, amount) group holds
    exactly one negative and one positive row, so no row is ever matched twice.
    Groups with more rows on either side are reported as ambiguous and left
    unmatched. ``keys`` must hold REVERSAL_KEY + total_amount for the whole file
    in raw row order.

    A match is an observed accounting pattern, not a verified reversal.

    Returns three boolean arrays aligned with ``keys``: (negative row matched,
    positive row matched, negative row in an ambiguous group).
    """
    cents = (keys["total_amount"] * 100).round().astype("int64")
    rows = np.arange(len(keys))
    is_negative = (cents < 0).to_numpy()
    # Only positive rows sharing a pickup time with some negative row can match;
    # pre-filtering keeps memory small on the full month.
    candidate = (cents > 0).to_numpy() & keys["tpep_pickup_datetime"].isin(
        keys.loc[is_negative, "tpep_pickup_datetime"]
    ).to_numpy()
    group_key = REVERSAL_KEY + ["_cents"]
    negative = keys.loc[is_negative, REVERSAL_KEY].assign(
        _cents=-cents[is_negative], _row=rows[is_negative]
    )
    positive = keys.loc[candidate, REVERSAL_KEY].assign(
        _cents=cents[candidate], _row=rows[candidate]
    )
    negative["_n_neg"] = negative.groupby(group_key)["_row"].transform("size")
    positive["_n_pos"] = positive.groupby(group_key)["_row"].transform("size")
    matched = negative.merge(positive, on=group_key, suffixes=("_neg", "_pos"))
    one_to_one = (matched["_n_neg"] == 1) & (matched["_n_pos"] == 1)

    has_partner = np.zeros(len(keys), dtype=bool)
    reversed_original = np.zeros(len(keys), dtype=bool)
    ambiguous = np.zeros(len(keys), dtype=bool)
    has_partner[matched.loc[one_to_one, "_row_neg"].to_numpy()] = True
    reversed_original[matched.loc[one_to_one, "_row_pos"].to_numpy()] = True
    ambiguous[matched.loc[~one_to_one, "_row_neg"].to_numpy()] = True
    return has_partner, reversed_original, ambiguous


def to_cents(values: pd.Series) -> pd.Series:
    """Dollar amounts as nullable integer cents, avoiding float comparison error."""
    return (values * 100).round().astype("Int64")


def add_derived_columns(trips: pd.DataFrame) -> pd.DataFrame:
    """Add trip duration (minutes) and average speed (mph; null if duration <= 0)."""
    duration_min = (
        trips["tpep_dropoff_datetime"] - trips["tpep_pickup_datetime"]
    ).dt.total_seconds() / 60
    hours = duration_min / 60
    speed = trips["trip_distance"] / hours.where(hours > 0)
    return trips.assign(trip_duration_min=duration_min, avg_speed_mph=speed)


def add_flags(
    trips: pd.DataFrame,
    reversal_has_partner: np.ndarray,
    reversed_original: np.ndarray,
    reversal_ambiguous: np.ndarray | None = None,
) -> pd.DataFrame:
    """Add every dq_/scope_/info_ flag and the eligibility flags.

    ``trips`` must already have the derived columns from ``add_derived_columns``.
    The reversal arrays come from ``find_reversal_pairs`` sliced to the same
    rows as ``trips``.
    """
    pickup = trips["tpep_pickup_datetime"]
    duration = trips["trip_duration_min"]
    distance = trips["trip_distance"]
    fare = trips["fare_amount"]
    payment = trips["payment_type"]
    is_flex = payment == FLEX_FARE_PAYMENT_TYPE

    meter_upper_bound = (
        METER_INITIAL_CHARGE
        + METER_RATE_PER_MILE * distance
        + METER_RATE_PER_MINUTE * duration.clip(lower=0)
        + METER_BOUND_TOLERANCE
    )
    # The bound needs a real duration; zero/negative durations are flagged separately.
    standard_metered = (trips["RatecodeID"] == STANDARD_RATECODE) & ~is_flex & (duration > 0)
    # Null components count as 0, so a null that hides a charge shows up as a difference.
    component_cents = sum(to_cents(trips[c]).fillna(0) for c in AMOUNT_COMPONENTS)
    if reversal_ambiguous is None:
        reversal_ambiguous = np.zeros(len(trips), dtype=bool)

    flags = {
        # Data quality
        "dq_invalid_location_id": ~trips["PULocationID"].isin(VALID_LOCATION_IDS)
        | ~trips["DOLocationID"].isin(VALID_LOCATION_IDS),
        "dq_negative_duration": duration < 0,
        "dq_zero_duration": duration == 0,
        "dq_duration_over_12h": duration > MAX_DURATION_MIN,
        "dq_implausible_speed": trips["avg_speed_mph"] > MAX_AVG_SPEED_MPH,
        "dq_zero_distance": distance <= 0,
        "dq_negative_total_amount": trips["total_amount"] < 0,
        # Flex Fare fare_amount is not a metered fare (see docs), so it is not checked here.
        "dq_nonpositive_fare": (fare <= 0) & ~is_flex,
        # Analytical scope
        "scope_outside_period": (pickup < PERIOD_START) | (pickup >= PERIOD_END),
        "scope_pickup_zone_unknown_or_outside_nyc": trips["PULocationID"].isin(
            UNKNOWN_OR_OUTSIDE_NYC_ZONES
        ),
        "scope_dropoff_zone_unknown_or_outside_nyc": trips["DOLocationID"].isin(
            UNKNOWN_OR_OUTSIDE_NYC_ZONES
        ),
        "scope_flex_fare": is_flex,
        "scope_fare_not_settled": payment.isin(FARE_NOT_SETTLED_PAYMENT_TYPES)
        | pd.Series(reversed_original, index=trips.index),
        # Informational
        "info_negative_row_has_exact_reversal_partner": pd.Series(
            reversal_has_partner, index=trips.index
        ),
        "info_negative_row_match_ambiguous": pd.Series(reversal_ambiguous, index=trips.index),
        "info_total_differs_from_components": to_cents(trips["total_amount"]) != component_cents,
        "info_fare_outside_standard_meter_range": standard_metered
        & (fare > 0)
        & ((fare < METER_INITIAL_CHARGE) | (fare > meter_upper_bound)),
    }
    flags = {name: values.fillna(False).astype(bool) for name, values in flags.items()}
    flagged = trips.assign(**flags)

    for eligibility, rules in ELIGIBILITY_RULES.items():
        flagged[eligibility] = ~flagged[rules].any(axis=1)
    return flagged
