# Phase 4 segmentation design

Code: `src/zone_features.py` (features), `src/zone_clustering.py` (models,
selection, sensitivity) and `src/cluster_figures.py` (charts). Outputs go to
`data/processed/zones/` (gitignored) and `reports/figures/fig10–fig15`.

## Unit and population

- **Unit:** a TLC pickup zone. Zones 264 (Unknown) and 265 (Outside NYC) are
  excluded.
- **Population:** zones with ≥ 1,000 eligible pickups in January 2025. That's
  103 zones covering 98.6% of eligible pickups.
  - The 157 zones below the threshold average about 8 pickups a day (median),
    with a median Flex Fare share of 56%. They are mostly in Queens (51),
    the Bronx (42), Brooklyn (36) and Staten Island (19).
- **Airports:** JFK and LaGuardia are held out of the fit and placed afterwards
  at the nearest segment centre, using the population's scaling.
  - Including them in the fit changes no other zone's Model A assignment
    (adjusted Rand index (ARI) 1.00).
  - Both sit far from any segment centre: JFK is 1.45× and LaGuardia 1.13× the
    95th-percentile member distance for Model A at k = 5.
  - Their trips are also structurally different. Median distance is 17.3 mi
    for JFK and 9.4 mi for LaGuardia, against 1.5–4.0 mi for the segments.
  - So they're reported as their own segment, not forced into a cluster.
- **Fitted population:** 101 zones, 92.0% of eligible pickups.

## Features

All features come from Phase 2 eligibility flags and the Phase 3 definitions:

- Pickups use `eligible_demand`.
- Drop-offs use `eligible_dropoff`.
- Trip length and duration use `eligible_distance_duration`.
- Fares use `eligible_fare` (metered only).

Weekday features use the 21 non-holiday weekdays.

| Feature | Definition | Transform |
|---|---|---|
| `log10_avg_daily_pickups` | Eligible pickups ÷ 31 days | log10 |
| `log2_weekend_to_weekday_ratio` | Average pickups per weekend day ÷ per weekday | log2 (skewed; max 5.3) |
| `weekday_share_{overnight, morning, pm_rush, evening}` | Share of weekday pickups at 00–05, 06–09, 16–19, 20–23 | none. Midday is omitted because the shares sum to 1 |
| `imbalance_{morning, pm_rush}` | (P − D) ÷ (P + D) over weekday 06–09 or 16–19 | none. NaN if P + D < 100 (none in the population) |
| `persistence_{morning, pm_rush}` | Share of weekdays (≥ 10 trip ends that day) with the majority net-flow sign | variant only (see below) |
| `log2_median_distance_mi` | Median trip distance from the zone | log2 |
| `median_duration_min` | Median trip duration from the zone | none |
| `median_fare_per_mile` | Median `fare_amount ÷ trip_distance`, metered trips ≥ 1 mi | none |

- **Medians** are read off fine histograms per zone × date (0.1 mi, 0.25 min,
  $0.05/mi bins). This makes resampling days cheap, and the bin width bounds
  the error.
- **Missing values** are filled with the population median, and the counts
  are reported. None were needed in the fitted population.
- **Standardisation:** z-scores within the fitted population. Every feature
  has equal weight unless stated otherwise.

**Model A (operational behaviour):** volume, weekend ratio, the 4 timing
shares and the 2 imbalances (8 features).

**Model B (trip characteristics):** the 4 timing shares, distance, duration
and fare per mile (7 features).

**Redundancy.**
- I dropped the weekend late-night share (r = 0.84 with the weekend ratio) and
  the evening 20–23 imbalance (r = 0.83 with the pm-rush imbalance).
- Distance, duration and fare per mile are correlated (|r| ≈ 0.8). They're
  kept because the brief asks for each, but I checked the equal-weight-per-
  group variant.

**Model B coverage and Flex Fare bias.**
- `fare_sample_coverage` = fare-eligible ÷ eligible pickups. The median is
  55% and the minimum 14%. 47 of 101 zones have under 50% coverage, mostly
  because of Flex Fare.
- `metered_vs_all_distance_pct` compares median metered distance with median
  distance over all trips. 33 zones differ by more than 10%.
- Raw Flex `fare_amount` is never used.

## Model selection

Fits use k = 2–8, `KMeans(n_init=20, random_state=20250101)`.

| Metric | What it measures |
|---|---|
| Silhouette, Calinski–Harabasz, Davies–Bouldin, inertia | Separation and compactness |
| Smallest cluster | Degenerate solutions (< 5 zones is flagged) |
| Seed stability | Mean ARI between the reference fit and 20 other seeds |
| Day-resampling stability | Median ARI across 30 bootstrap resamples of dates, drawn within day type |

**Pre-registered rule.** A k is eligible if its smallest cluster has ≥ 5
zones, its median bootstrap ARI is ≥ 0.60 and its seed ARI is ≥ 0.90. Among
eligible k, the rule picks the highest silhouette, preferring the smaller k
within 0.02.

- **Result: the pre-specified statistical selection is k = 2** for both
  models. It splits a high-volume Manhattan core from a low-volume remainder,
  which a simple volume split largely reproduces (ARI 0.58 for Model A, 0.61
  for Model B).
- **Analyst-selected operational segmentation: Model A k = 5** (and Model B
  k = 3). This is a judgment, not a statistical optimum.
  - k = 5 has a lower silhouette than k = 2 (0.279 vs 0.334).
  - It hasn't been validated against any business outcome.
  - Why I preferred it:
    - Silhouette is nearly flat across k (0.26–0.33), so it gives weak
      guidance.
    - k = 2 adds little beyond a volume rule: held-out R² 0.36 vs 0.32.
    - k = 5 passes every pre-set gate. Bootstrap ARI is 0.88, seed ARI 0.95,
      and the smallest cluster has 13 zones.
    - It separates timing, direction and weekend patterns that k = 2 merges.
  - It was chosen from the selection metrics before the profiles were read.
- **Airports** are a separate, rule-defined category. The result is five
  k-means segments plus airports, not six clusters.
- **Pickup shares** are reported against all eligible pickups
  (`pickup_share_of_all_pct`) and against the fitted zones
  (`pickup_share_of_fitted_pct`). Business-facing material uses the first.

## Validation kept out of selection

These checks were computed after choosing k and never used to pick it:

- **Split-half:** features from odd days only or even days only, refitted and
  compared with the full-month labels.
- **Held-out rule comparison:** groups are formed on odd-day features, and
  variance explained is measured on even-day features. This removes the
  in-sample advantage k-means has from optimising variance explained.
- **Weighting:** equal weight per feature group. For Model A k = 5, labels
  are matched with the Hungarian method, and zone moves and pickup-weighted
  agreement are reported (`model_A_weighting_comparison.csv`,
  `model_A_k5_equal_group_profiles.csv`).
- **Reconciliation:** assignments, segment sizes and pickup totals are
  rechecked against a direct recount of `eligible_demand` in the flagged trip
  file (`reconciliation` in `clustering_summary.json`).
- **Airports:** included in the fit.
- **Thresholds:** 500 and 3,000 minimum pickups (ARI on shared zones).
- **Feature variants:**
  - Model A plus the persistence features.
  - Model A without the volume feature.
  - Model B without the fare feature.

## Rule-based baselines

| Rule | Groups |
|---|---|
| Volume halves | 2 |
| Volume terciles | 3 |
| Flow pattern (morning/evening imbalance sign, ±0.1 band) | 3 |
| Dominant weekday period (highest share relative to the citywide share) | 4 |
| Weekend-heavy (weekend ÷ weekday > 1.5), then dominant weekday period | 5 |

Each rule is compared with k-means using the same number of groups.

The five-group rule was built after the k = 5 solution was seen, and k-means
directly optimises the variance-explained metric used to compare them. So the
comparisons show that k-means summarises the features more compactly. They
don't show that k = 5 is better for business decisions.

## Limitations

- **One month of data.** Segments describe January 2025, and January has
  holiday effects (Phase 3).
- **Model A is sensitive to weighting.** Under equal-group weighting, 20 of
  101 zones (19.8%) change segment, and 72.4% of pickups keep theirs.
  - The two high-volume segments keep their opposite morning flows.
  - The weekend segment shrinks from 13 zones to 7, and the Upper East Side
    zones switch segment.
- **Model B depends on its fare feature** (ARI 0.55 without it) and on the
  pickup threshold (0.57). The fare feature also rests on under 50% of trips
  in 47 zones.
- **Features describe completed trips only.** Imbalance and timing say nothing
  about taxi supply or unmet demand.
- **Segment names are descriptive labels** of the medians in
  `model_*_profiles.csv`, not claims about land use.
