# Phase 2 cleaning rules

Pipeline: `src/cleaning_rules.py` (rules), `src/clean_trips.py` (writes
`data/processed/yellow_tripdata_2025-01_flagged.parquet`) and
`src/validate_cleaned.py` (writes `reports/phase2_quality_report.{md,json}`).

## Principles

- **No deletion, imputation or winsorisation.** All 3,475,226 raw rows and all
  20 raw columns are kept byte-for-byte (types and nulls included). Rules add
  boolean flags; analyses filter on eligibility flags.
- **Two kinds of exclusion.** `dq_*` flags mean the record is unreliable for a
  purpose. `scope_*` flags mean the record may be fine but is outside what a
  given analysis studies. `info_*` flags never exclude anything.
- **Thresholds come from documentation or from visible breaks in the data**,
  with the evidence stated below. Counts are from the January 2025 file.
- **Evidence labels.** *Documented* = stated by TLC. *Observed* = measured in
  this file. *Hypothesis* = an explanation consistent with the data but not
  confirmed by TLC.

## Rules

| Flag | Kind | Definition | Rows | Justification |
|---|---|---|---:|---|
| `scope_outside_period` | scope | pickup < 2025-01-01 or >= 2025-02-01 | 22 | Analytical period is defined on pickup time. The 22 rows are 21 New Year's Eve pickups and one at 2025-02-01 00:00:44. |
| `dq_invalid_location_id` | DQ | PU or DO ID not in 1–265 | 0 | IDs must exist in `taxi_zone_lookup.csv`. Kept as a guard for other months. |
| `dq_negative_duration` | DQ | dropoff < pickup | 124 | Timestamps in the wrong order. The pickup time itself is unreliable. |
| `dq_zero_duration` | DQ | dropoff = pickup | 1,927 | No measurable trip time. **1,206 of these are every VendorID 7 (Helix) record**: distances and fares look normal, so Helix appears not to record a dropoff time (observed). The pickup is still credible, so these rows stay eligible for demand. |
| `dq_duration_over_12h` | DQ | duration > 720 min | 1,040 | Observed break: only 337 trips last 3–12 h, then 1,020 sit at 12–24 h with a median of 23.4 h and a median speed of 0.08 mph. That pattern looks like a meter left running, not a trip. |
| `dq_implausible_speed` | DQ | distance / duration > 80 mph | 982 | The 99.9th percentile is 47 mph and the 99.99th is 1,229 mph. 80 mph is above any posted NYC-area limit (65 mph), so legitimate long highway trips such as JFK to New Jersey at 50–70 mph average are kept. Sensitivity: >50 mph flags 2,243 rows, >100 mph flags 899. |
| `dq_zero_distance` | DQ | trip_distance = 0 | 90,893 | No usable distance. Not treated as "no trip": 64k have a duration ≥ 1 min, and 86% of rush-hour cases end in a different zone. See the Flex Fare finding below. |
| `dq_negative_total_amount` | DQ | total_amount < 0 | 63,037 | A negative charge doesn't describe a trip as recorded. All are VendorID 2. Excluded from every purpose, including demand, so a trip with an exactly negating row isn't counted twice. |
| `dq_nonpositive_fare` | DQ | fare_amount ≤ 0, non-Flex | 60,655 | A metered fare can't be zero or negative for a completed charged trip. Flex Fare rows are exempt (see below). |
| `scope_pickup_zone_unknown_or_outside_nyc` | scope | PULocationID in {264, 265} | 9,521 | Zone 264 is "Unknown" and 265 is "Outside of NYC". The trip can be valid, but it can't be placed in an NYC zone. |
| `scope_dropoff_zone_unknown_or_outside_nyc` | scope | DOLocationID in {264, 265} | 24,062 | Same reasoning. Trips to New Jersey are legitimate. |
| `scope_flex_fare` | scope | payment_type = 0 | 540,149 | Different pricing mechanism (see below). |
| `scope_fare_not_settled` | scope | payment_type in {3 No charge, 4 Dispute, 5 Unknown, 6 Voided}, or a positive row matched one-to-one by an exactly negating row | 116,361 | The trip may have happened, but the recorded charge appears not to have been collected as charged. |
| `info_negative_row_has_exact_reversal_partner` | info | negative row matched one-to-one with a positive row it exactly negates | 51,840 | An observed accounting pattern, not a verified reversal. |
| `info_negative_row_match_ambiguous` | info | negative row whose (key, amount) group has more than one candidate on either side | 0 | Ambiguous groups are left unmatched rather than guessed. |
| `info_fare_outside_standard_meter_range` | info | standard rate, non-Flex, duration > 0, fare > 0, and fare < $3.00 or fare > $3.00 + $3.50/mile + $0.70/min + $1.40 | 1,128 | Demoted from an exclusion in the correction pass. See "Metered-fare range check" below. |
| `info_total_differs_from_components` | info | total ≠ sum of components, compared in integer cents (nulls count as 0) | 1,111,807 | Vendor recording difference (see below). |

## Eligibility flags

Each flag is true when none of the listed rules apply. Waterfall order follows
the order shown.

| Flag | Excluded by | Intended use |
|---|---|---|
| `eligible_demand` | outside period, invalid ID, negative total, negative duration, unknown/outside pickup zone | Pickup counts by NYC zone and time. Zero distance, zero duration, long duration and high speed are **kept**, because they affect distance or time, not whether a pickup occurred. |
| `eligible_distance_duration` | outside period, invalid ID, negative total, negative/zero duration, > 12 h, zero distance, > 80 mph | Trip length, duration and speed. Zones are not required. |
| `eligible_fare` | everything in distance/duration, plus Flex Fare, fare not settled, non-positive fare | Metered fare analysis. Use `fare_amount`, not `extra` or `total_amount` (see vendor finding). |
| `eligible_clustering` | everything in distance/duration, plus unknown/outside pickup or dropoff zone | Provisional trip-level feature set (time, zones, distance, duration). If fare features are used, combine it with `eligible_fare`. Revisit once the Phase 3 clustering design is fixed. |

## Findings behind the rules

### Flex Fare (payment_type 0): 540,149 rows, 15.5%

- **Documented:** TLC defines a Flex Fare trip as an e-hail app trip with an
  upfront, non-metered fixed fare that includes taxes, fees *and any price
  multiplier* ([TLC Flex Fare rules](https://www.nyc.gov/assets/tlc/downloads/pdf/flex_fare_rules_package_promulgation.pdf)).
  Because of that multiplier, Flex Fare prices may vary with app-side pricing.
  That is still not evidence of surge pricing in metered Yellow Taxi fares.
- **Observed:**
  - In every Flex row, `passenger_count`, `RatecodeID`, `store_and_fwd_flag`,
    `congestion_surcharge` and `Airport_fee` are null. The pattern is
    structural, not random missingness, so missing values alone do not make a
    row invalid.
  - Fare components don't add up: 88% of Flex rows have `total_amount` $2.50
    above the listed components, consistent with an unrecorded congestion
    surcharge (hypothesis).
  - 81,401 Flex rows have a negative `fare_amount` but a non-negative total,
    so `fare_amount` doesn't represent the fare paid.
  - Fare tracks distance far less closely than metered fares: Spearman ρ is
    0.47 for Flex versus 0.91 for credit-card trips.
- **Observed:** Flex rows come from VendorID 2 (451k), 1 (88k) and 6 (all 489
  of its rows).
- **Observed:** the Flex share changes during the month, from about 4% on
  2–6 Jan to 11–31% per day from 8 Jan onward (17.5% on 1 Jan). Fare-eligible rows therefore aren't a
  stable sample over time, so compare fares within periods with care.
- **Observed:** 52k Flex rows (9.6%) have zero distance, concentrated in
  weekday 16:00–19:00. That drives the 18:00 peak in distance/duration
  exclusions. Distance appears unrecorded on these trips rather than truly
  zero (hypothesis).
- **Decision:** Flex rows stay eligible for demand, distance/duration and
  clustering (subject to the other rules). They're out of scope for fare
  analysis because the fare fields don't describe a metered fare.

### Negative amounts: all VendorID 2

- **Matching audit (observed):**
  - The match key is VendorID, both timestamps and both zones. The key isn't
    unique: 105,076 rows share a key with another row (52,535 pairs and 2
    triples), almost all of them negative/positive pairs.
  - Matching therefore uses key + exact amount in integer cents, and a pair
    is accepted only when that group has exactly one negative and one positive
    row. No row can be matched twice.
  - Result: 51,840 negatives match one-to-one (51,840 distinct positives),
    0 are ambiguous, and 11,197 are unmatched.
  - Of the unmatched rows, 691 share the key with a positive row of a different
    amount. By payment type they are dispute 5,078, Flex 3,421, cash 1,771,
    no charge 926 and card 1.
  - Matched pairs mostly keep their payment type: 29k disputes, 12k cash and
    6k no-charge.
- **Interpretation:** the exact negations are consistent with reversal or
  adjustment records. That's a hypothesis, since TLC doesn't document how
  vendors record refunds. A match is an accounting pattern, not proof that the
  trip was reversed.
- **Decision:**
  - Negative rows are excluded everywhere.
  - Matched positive rows stay for demand but are out of scope for fare,
    because their charge appears to have been negated.
  - Unmatched negative rows are excluded from demand too. Whether any of them
    stand for a trip with no positive record in this file is unknown, so their
    effect on pickup counts can't be quantified.

### Total vs components: vendor recording difference

- **Verified identity (observed):** the check was recomputed from the raw
  columns in integer cents. Every money field is a whole number of cents, so
  no floating-point tolerance is involved.
  - All 665,467 non-Flex VendorID 1 rows satisfy `total_amount − sum of the
    nine components = −(congestion_surcharge + cbd_congestion_fee +
    Airport_fee)`, with 0 exceptions.
  - None of these rows has a null amount. The 59,679 rows with a zero residual
    are exactly the rows where all three fees are zero.
- **Supporting pattern (observed):** for VendorID 1, `extra` minus those three
  fees is $0, $1.00, $2.50, $5.00, $6.00 or $7.50 on all but 33 rows. Those
  values match the documented overnight ($1.00), rush-hour ($2.50) and
  LaGuardia ($5.00) surcharges and their combinations. The 33 exceptions are
  −$0.75.
- **Interpretation:** this is consistent with VendorID 1 recording the
  congestion, CBD and airport fees inside `extra` as well as in their own
  columns, and counting them once in the total. That's a hypothesis; the TLC
  dictionary doesn't describe it.
- **Observed:** VendorID 2 non-Flex rows reconcile exactly in 98.6% of cases.
  The most common exception is a total $0.75 below the components (27,080
  rows), the size of the CBD fee. That's unexplained.
- **Decision:** nothing is recalculated. Don't compare `extra` or component
  sums across vendors. Use `fare_amount` for fare analysis.

### Metered-fare range check (demoted to informational)

- **Rate used:** $3.00 initial charge, plus $0.70 per 1/5 mile above 12 mph or
  $0.70 per 60 seconds when slow or stopped. That's the
  [TLC taxi-fare page](https://www.nyc.gov/site/tlc/passengers/taxi-fare.page)
  as fetched on 2026-10-24. I couldn't retrieve an archived January 2025
  version, but the data confirms the rate: 99.97% of fare-eligible
  standard-rate fares fall exactly on the $3.00 + k × $0.70 grid.
- **Effects checked:**
  - Waiting time is covered by the per-minute term. The meter charges distance
    *or* time, so charging both is an upper bound.
  - Surcharges sit in `extra`, not `fare_amount`, so they don't belong in the
    bound.
  - Rounding to the next 1/5 mile and the next minute adds at most $0.70 each,
    which the $1.40 tolerance covers.
  - Non-standard rate codes (2–6, 99) aren't checked.
- **Why it no longer excludes rows:** a violation can't show *which* field is
  wrong.
  - 85% of the high-side cases have zero recorded distance.
  - Only 16% of flagged fares fall on the meter grid. The rest include
    flat-looking amounts ($45, $70, $85) and off-grid cents ($18.19, $22.67).
    That suggests the fare wasn't produced by the standard meter, so the rate
    code is as likely to be wrong as the fare.
- **Effect:** 1,128 rows carry the info flag. 120 of them return to fare
  eligibility, which moves from 2,775,220 to 2,775,340. The other 1,008 are
  already excluded by other rules.

### CBD congestion fee before 5 January

401 trips were charged the fee with a pickup on 4 Jan (21:58–23:59), and 397
of them were dropped off after midnight on 5 Jan. That's consistent with the
fee being assessed at trip end, so no flag was added.

## Limitations

- Thresholds (12 h, 80 mph) are judgment calls backed by the
  evidence above, and the report includes sensitivity counts. Other analysts
  could reasonably choose differently.
- Demand eligibility measures completed metered pickups. It is not ride-request
  demand: unmet requests, other modes and Flex Fare bookings that were never
  completed are all absent.
- Exclusions aren't random. VendorID 7 is entirely ineligible for
  distance/duration and fare. Flex Fare is entirely out of fare scope and
  increases through the month. Zero-distance exclusions cluster in weekday
  rush hours. See the by-group tables in the Phase 2 report.
