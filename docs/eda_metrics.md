# Phase 3 metric definitions

Code: `src/eda_metrics.py` (definitions), `src/eda_aggregates.py` (tables in
`data/processed/eda/`), `src/eda_figures.py` (charts in `reports/figures/`).
All metrics are computed from the Phase 2 flagged file; raw and flagged data
are only read.

## What the counts measure

| Term | Meaning | It is **not** |
|---|---|---|
| Pickup | A completed, metered or Flex Fare Yellow Taxi trip starting in a zone (`eligible_demand`) | Total ride requests. Unmet hails, app requests that were never fulfilled and other modes (Uber/Lyft, Green taxi, transit) are absent |
| Drop-off | A completed trip ending in a zone, with a usable drop-off time and location (`eligible_dropoff`, below) | Passenger arrivals by any mode |
| Net flow | Pickups − drop-offs for a zone and time cell | A count of idle or missing drivers. A positive value means more taxi trips left than arrived; it says nothing about how many taxis were available or how many people wanted one |

## Calendar

- **Day type.** `weekday` (21 days), `weekend` (8 days) or `holiday` (1 Jan
  New Year's Day and 20 Jan Martin Luther King Jr. Day, 2 days). Holidays have
  their own type so they don't pull down weekday averages.
- **Per-day averages** divide a total by the number of calendar days of that
  type (`days` column), never by the number of days with trips.
- **Periods** (pickup hour): overnight 00–05, morning 06–09, midday 10–15,
  pm rush 16–19 (matches the TLC weekday rush-hour surcharge window) and
  evening 20–23.
- Hours are local NYC clock hours from the TLC timestamps.

## Drop-off eligibility (`eligible_dropoff`)

`eligible_demand` is defined around the pickup, so drop-offs are validated
separately. A drop-off counts when all of these hold:

- The drop-off timestamp is in January 2025. A trip picked up on 31 Jan that
  ends in February counts as a pickup only.
- The drop-off time was recorded: duration is positive and ≤ 12 h. This
  excludes VendorID 7, whose drop-off time always equals the pickup time.
- The record is not a negative-total adjustment.
- The drop-off zone is a known NYC zone (1–263).

Counts: 3,384,588 drop-offs vs 3,402,692 pickups. 22,349 rows are valid only
at the pickup end and 4,245 only at the drop-off end.

## Imbalance metrics

- **Cell** = pickup or drop-off zone × day type × hour, totalled over the month.
- `net_flow = pickups − drop-offs`, also shown as `avg_net_flow_per_day`.
- **Imbalance index** `I = (P − D) / (P + D)`, which ranges from −1 (all
  drop-offs, net inbound) to +1 (all pickups, net outbound). It scales by the
  cell's own activity, so busy and quiet zones can be compared.
- **Stability rule:** I is reported only when `P + D ≥ 100`. The index is a
  difference of two shares, and its standard error is at most `1/√n`, so 100
  trip ends keeps it at or below 0.10. Smaller cells are `low_volume` and left
  blank (NaN) rather than shown as extreme ratios. 4,578 of 16,641 cells pass,
  and they hold 96.3% of all trip ends.
- **Persistence:** for each cell, I count the qualifying days (≥ 10 trip ends
  that day) on which net flow had the cell's majority daily sign.
  `same_sign_share` is reported when there are at least 5 qualifying days.
  - Zero-net days stay in the denominator, so they count against persistence.
    `zero_net_days` reports how many there were.
  - `persistence_sensitivity.csv` repeats the check for strongly imbalanced
    weekday cells (|I| ≥ 0.3 and ≥ 50 trip ends per day) with stricter daily
    and day-count minimums. It also compares against the monthly-total sign
    and against leaving zero days out.
- **Inbound / outbound** describe observed passenger-trip direction only.

## Fare metrics

- **Population:** `eligible_fare`, which covers metered trips only. Flex Fare,
  unsettled payments and data-quality failures are already excluded.
- **Measure:** `fare_amount`, the meter's time-and-distance fare. Surcharges
  sit in `extra`, and VendorIDs record `extra` differently (Phase 2), so
  `extra` and `total_amount` aren't used for cross-period comparisons.
- **Medians move in steps.** Medians sit on the $3.00 + k × $0.70 meter grid,
  so they shift in $0.70 jumps. Comparisons therefore use means as well.
- **Fare per mile** is computed only for trips ≥ 1 mile, because the $3.00
  initial charge dominates shorter ones. **Fare per minute** is computed only
  for trips ≥ 5 minutes.
- **Two price measures.**
  - *Metered base fare* = `fare_amount`.
  - *Total charge excluding tips* = `total_amount − tip_amount`. It includes
    the rush-hour ($2.50, weekdays 16:00–20:00) and overnight ($1.00,
    20:00–06:00) surcharges, congestion fees, tolls and other fees.
  - Tips are excluded because only card tips are recorded.
- **Peak vs off-peak after adjustment:** weekday, standard-rate trips only.
  - Means are direct-standardised on a *common support*. Strata come from
    distance bands (0–1, 1–2, 2–3, 3–5, 5–10, 10+ mi) × duration bands (0–5,
    5–10, 10–15, 15–20, 20–30, 30–45, 45–60, 60+ min). A stratum is kept only
    if every period has ≥ 30 trips in it.
  - Every period is then averaged over the same strata with the same weights:
    each stratum's share of pooled trips in kept strata. That's 24 of 45
    strata, covering 97.2% of trips.
  - `weight_covered`, `strata_kept` and `strata_total` report the support.
  - Robustness is checked with fine, coarse and decile bins, and with 1, 30
    and 100 minimum trips per stratum (`fare_peak_adjustment_sensitivity.csv`).
  - This is descriptive. It shows how much of a raw difference comes from
    trip mix. It is not a causal estimate, and it says nothing about surge
    pricing.
- **Small samples:** groups with fewer than 1,000 trips are flagged
  `small_sample`.

## Flex Fare composition

- **Flex share** = Flex pickups ÷ eligible pickups, per day or per day type ×
  hour.
- **Representativeness** compares the period and borough mix of three samples:
  all eligible pickups, the fare-eligible metered sample and Flex pickups.

## Sensitivity checks (`sensitivity.csv`, `imbalance_threshold_sensitivity.csv`)

1. Demand without zero-distance and zero-duration rows.
2. Demand without VendorID 7.
3. Weekday averages with holidays counted as weekdays.
4. Weekday averages without the post-holiday days of 2–3 Jan.
5. Speed caps of 50 mph, 100 mph or none, instead of 80 mph.
6. Fares without the informational meter-range rows.
7. Imbalance computed only from trips valid at both ends.
8. Imbalance minimum-volume thresholds of 50, 100, 200 and 400 trip ends.
9. Fare standardisation under 4 binning schemes and 3 minimum-stratum rules.
10. Persistence under stricter daily and qualifying-day minimums, with an
    alternative sign definition and zero-day handling.
