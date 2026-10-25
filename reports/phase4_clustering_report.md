# Phase 4: K-means segmentation of NYC pickup zones (January 2025)

Design and definitions: [docs/clustering_design.md](../docs/clustering_design.md).
Tables: `data/processed/zones/`. Charts: `reports/figures/fig10–fig15`.

What these segments describe: zones grouped by **completed** Yellow Taxi
trips, meaning when trips start, the direction of passenger flow, trip length
and metered fares. They don't measure ride requests, taxi supply or unmet
demand.

**Shares.** Unless a heading says otherwise, every pickup share in this
report is a share of **all 3,402,692 eligible pickups** in January 2025.
An earlier draft divided by the fitted zones' pickups instead, which
overstated segment shares by about 8%. That has been corrected here and in
fig11.

## 1. Population and coverage (reconciled against the trip file)

Pickups were recounted directly from `eligible_demand` rows in the flagged
trip file (`reconciliation` in `clustering_summary.json`).

| Group | Zones | Eligible pickups | Share of all eligible pickups |
|---|---:|---:|---:|
| Fitted by k-means (5 segments) | 101 | 3,129,693 | 92.0% |
| Airports (JFK 137,522; LaGuardia 87,401): separate category, not fitted | 2 | 224,923 | 6.6% |
| Zones below 1,000 pickups: not segmented | 157 | 48,076 | 1.4% |
| **Total** | 260 | 3,402,692 | 100% |

Reconciliation checks:

- Every fitted zone has exactly one assignment, zone IDs are unique, and
  segment sizes sum to 101.
- Each zone's pickup count matches the trip-file recount exactly.
- JFK and LaGuardia aren't in the fitted set.

**The result is five k-means segments plus a separately defined airport
category. It is not six k-means clusters.**

Other coverage notes:

- The zones below the threshold average about 8 pickups a day (median), with
  a median Flex Fare share of 56%. By borough: Queens 51, Bronx 42, Brooklyn
  36, Staten Island 19, Manhattan 8.
- Model A features are complete for all 101 zones.
- Model B's fare feature is not. Its fare-eligible share of pickups has a
  median of 55% and a minimum of 14%, and 47 of 101 zones are under 50%.
- Flex Fare exclusion bias: in 33 zones, median metered trip distance differs
  from all-trip distance by more than 10% (range −39% to +92%).

## 2. Features

- **Model A (operational behaviour, 8 features):**
  - log daily pickups
  - log weekend ÷ weekday rate
  - weekday pickup shares at 00–05, 06–09, 16–19 and 20–23
  - weekday imbalance (P − D) ÷ (P + D) at 06–09 and at 16–19
- **Model B (trip characteristics, 7 features):**
  - the same 4 timing shares
  - log median distance
  - median duration
  - median metered fare per mile (metered trips ≥ 1 mi; Flex Fare excluded)

Transformations, redundancy checks and missing-value handling are in the
design doc. Persistence was tested as a Model A variant but not used in the
main model: most zones sit at its ceiling (median 0.95–1.0), and it is less
reliable for small zones.

## 3. Choosing k: statistical selection vs analyst choice (fig10)

| Model | k | Silhouette | Smallest cluster | Seed ARI | Day-resampling ARI (median / p10) |
|---|---:|---:|---:|---:|---:|
| A | **2** (pre-specified selection) | 0.334 | 43 | 1.00 | 0.88 / 0.85 |
| A | 3 | 0.334 | 15 | 0.98 | 0.89 / 0.78 |
| A | 4 | 0.262 | 15 | 0.90 | 0.73 / 0.46 |
| A | **5** (analyst-selected) | 0.279 | 13 | 0.95 | 0.88 / 0.76 |
| A | 6 | 0.294 | 7 | 0.90 | 0.84 / 0.74 |
| A | 7–8 | 0.31 / 0.30 | 3 | 0.92 / 0.76 | fail size gate |
| B | **2** (pre-specified selection) | 0.413 | 42 | 0.98 | 0.92 / 0.88 |
| B | **3** (analyst-selected) | 0.340 | 25 | 0.98 | 0.94 / 0.87 |
| B | 4 | 0.340 | 10 | 0.96 | 0.73 / 0.58 |
| B | 5 | 0.305 | 15 | 0.99 | 0.87 / 0.60 |

**Pre-specified statistical selection: Model A k = 2.**

- The rule was fixed before fitting. It applies three gates (smallest cluster
  ≥ 5 zones, median resampling ARI ≥ 0.60, seed ARI ≥ 0.90), then picks the
  highest silhouette, preferring the smaller k within 0.02.
- It returns k = 2, which splits a high-volume core (58 zones, 87.9% of
  pickups) from a low-volume remainder (43 zones, 4.1%).
- A simple volume split largely reproduces this (ARI 0.58).

**Analyst-selected operational segmentation: Model A k = 5.**

- This is a judgment, not a statistical optimum.
  - k = 5 has a *lower* silhouette than k = 2 (0.279 vs 0.334). It isn't the
    best-separated solution.
  - It hasn't been validated against any business outcome: there's no outcome
    data in this project.
- **Why I preferred it:**
  1. Silhouette barely changes across k (0.26–0.33), so it gives only weak
     guidance on k.
  2. k = 2 adds little beyond a volume rule. On held-out days it explains 36%
     of feature variance, against 32% for volume halves.
  3. k = 5 passes every pre-set gate. Its resampling stability (0.88) matches
     k = 2.
  4. It separates timing, flow-direction and weekend patterns that k = 2
     merges.
- k = 5 was chosen from the selection metrics before the segment profiles
  were read. Model B k = 3 was chosen the same way.

## 4. Segments, profiles and representative zones

### Model A, k = 5 (fig11, fig13, fig14)

Values are medians across each segment's zones. Imbalance is weekday;
positive means more pickups than drop-offs. Shares are of all eligible
pickups.

| Segment (descriptive name) | Zones | % of all pickups | Pickups per day | Weekend ÷ weekday | Share 06–09 / 16–19 / 20–23 | Imbalance 06–09 / 16–19 | Median trip | Flex share |
|---|---:|---:|---:|---:|---|---|---|---:|
| 1 High volume, morning outflow | 21 | 43.0 | 2,144 | 0.97 | 19% / 27% / 16% | +0.27 / −0.13 | 1.5 mi, 10 min | 13% |
| 2 High volume, morning inflow | 23 | 35.7 | 1,175 | 0.87 | 9% / 35% / 24% | −0.25 / +0.07 | 1.8 mi, 12 min | 21% |
| 3 Weekend and late night | 13 | 8.9 | 190 | 2.68 | 23% / 22% / 32% | +0.02 / −0.58 | 2.8 mi, 14 min | 58% |
| 4 Mid volume, evening inflow | 24 | 3.3 | 77 | 1.07 | 27% / 27% / 13% | −0.07 / −0.33 | 4.0 mi, 18 min | 55% |
| 5 Low volume, morning-heavy | 20 | 1.1 | 53 | 0.96 | 41% / 14% / 7% | +0.26 / −0.47 | 4.0 mi, 25 min | 55% |
| Airports (separate category) | 2 | 6.6 | 4,436 / 2,819 | 1.06 / 0.80 | 11% / 28% / –; 9% / 25% / – | +0.54 / +0.63; +0.03 / +0.54 | 17.3 / 9.4 mi | 1% |

The airport row shows JFK first, then LaGuardia.

Segment names describe segment **medians**, not every member. For example,
Upper East Side South, the largest zone in segment 1, has a near-balanced
morning imbalance (+0.02). It sits in segment 1 on volume and timing, not on
its own flow direction.

Representative zones: the largest zones, then the zones closest to each
segment's centre.

1. **High volume, morning outflow.** Largest: Upper East Side South and North,
   Penn Station/Madison Sq West, Lincoln Square East. Closest to centre:
   Sutton Place/Turtle Bay North, Lenox Hill West, Upper West Side South,
   Yorkville West, Gramercy.
2. **High volume, morning inflow.** Largest: Midtown Center, Times Square,
   Midtown East, Union Sq. Closest to centre: TriBeCa/Civic Center, Financial
   District North, East Chelsea, Kips Bay.
3. **Weekend and late night.** Largest: East Village, West Village, Greenwich
   Village South, Lower East Side. Closest to centre: Williamsburg (South
   Side), Alphabet City, Greenpoint.
4. **Mid volume, evening inflow.** Largest: East Harlem South, Clinton West,
   Central Harlem. Closest to centre: Boerum Hill, Long Island City, East
   Harlem North, Fort Greene.
5. **Low volume, morning-heavy.** Largest: Hamilton Heights, Astoria, Crown
   Heights North, East New York. Closest to centre: East New York,
   Brownsville, South Ozone Park. 13 of its 20 zones are in Brooklyn.

**What separates the segments [observed]:**

- **Timing.** Segment 5 takes 41% of weekday pickups at 06–09, against 9% for
  segment 2. Segment 3 takes 46% of its weekend pickups at 00–03.
- **Direction.** Segments 1 and 5 send more trips out than they receive in the
  morning. Segment 2 receives more in the morning. Segments 3–5 receive more
  in the evening.
- **Trip length.** Segments 4 and 5 trips are about 4 mi, against 1.5–1.8 mi
  in the core.
- **Fares.** Metered fare per mile falls from $6.82 (segment 1) to $5.39
  (segment 5). That's consistent with the $3.00 initial charge spread over
  longer trips, not a different tariff.

**Low-volume caution.** Segments 4 and 5 hold 44 zones but only 4.4% of all
pickups. Their Flex Fare share is about 55%, and their metered fare figures
rest on about 40% of trips.

### Model B, k = 3 (fig12)

| Segment | Zones | % of all pickups | Median distance | Median duration | Fare per mile | Share 06–09 | Flex share | Fare coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Short trips, high volume | 48 | 86.6 | 1.6 mi | 11 min | $6.71 | 14% | 18% | 80% |
| Longer trips, mixed timing | 25 | 3.9 | 3.8 mi | 18 min | $5.49 | 24% | 56% | 39% |
| Longest trips, morning-heavy | 28 | 1.5 | 4.1 mi | 23 min | $5.32 | 38% | 56% | 39% |

Representative zones: Gramercy, Hudson Sq and Flatiron (short trips); Boerum
Hill, Fort Greene and Downtown Brooklyn (longer trips); Bushwick, Astoria and
Crown Heights North (longest trips).

Model B mostly separates the Manhattan core from everything else. The two
non-core segments differ mainly in timing and duration, and both rest on under
40% fare coverage.

## 5. Feature-weight sensitivity of Model A k = 5

I refitted Model A k = 5 with **equal weight per feature group** (volume,
weekly pattern, timing, direction) instead of **equal weight per feature**,
then matched labels with the Hungarian method
(`model_A_weighting_comparison.csv`).

| Measure | Result |
|---|---|
| Zones whose segment changes | **20 of 101 (19.8%)** |
| Pickup-weighted agreement | **72.4%** of fitted pickups keep their segment |
| ARI | 0.58 |
| Zones kept, by segment | 1: 16 of 21 · 2: 18 of 23 · 3: 7 of 13 · 4: 21 of 24 · 5: 19 of 20 |

Largest movers:

| Zone | Pickups | Equal per feature | Equal per group | Why it is borderline |
|---|---:|---|---|---|
| Upper East Side South | 161,511 | 1 Morning outflow | 2 Morning inflow | Morning imbalance +0.02, pm-rush +0.03: near balanced |
| Upper East Side North | 154,004 | 1 | 2 | Morning +0.20, pm-rush −0.05 |
| Lenox Hill East, Garment District, Central Park | 47,838–74,163 | 1 | 2 | Morning imbalance −0.09 to +0.09 |
| East Village, West Village, Greenwich Village South, Meatpacking | 32,910–80,325 | 3 Weekend | 1 Morning outflow | Weekend ratio 1.7–2.3, but high volume and morning outflow (+0.37/+0.47 in the Villages) |
| Little Italy/NoLiTa, SoHo | 28,596–36,429 | 2 | 1 | Weekend ratio 1.6–2.0 |
| 9 smaller zones | 1,465–22,969 each | various | various | Mostly moving into or out of segment 4 (mid volume, evening inflow) |

**Robust characteristics.** These hold under both weightings:

- **Two high-volume segments with opposite morning flows.**

  | Segment | Equal per feature | Equal per group |
  |---|---|---|
  | Morning outflow | +0.27 morning, −0.13 evening | +0.27, −0.15 |
  | Morning inflow | −0.25 morning, +0.07 evening | −0.19, +0.10 |

  The characteristic morning directions are retained.
- **Low volume, morning-heavy** keeps 19 of 20 zones, with the same medians
  (41% of pickups at 06–09, morning imbalance +0.26 vs +0.29).
- **Mid volume, evening inflow** keeps 21 of 24 zones.

**Unstable memberships:**

- The large, near-balanced Upper East Side zones flip between the two
  high-volume segments.
- The **Weekend and late night** segment is the least robust. Under
  equal-group weighting it shrinks from 13 zones (8.9% of pickups) to 7 small
  zones (1.6%). The high-volume Village zones join the morning-outflow
  segment.

**Does the business interpretation change?**

- The headline story holds: core zones split by morning flow direction, plus
  a small morning-heavy outer group.
- Two things change:
  1. The size of the high-volume segments: 43.0% / 35.7% of pickups under
     equal-feature weighting vs 38.2% / 48.3% under equal-group weighting.
  2. Whether a weekend/late-night segment exists at meaningful volume.
- I'd present the weekend segment as **sensitive to modelling choices**, and
  segment shares as approximate.

## 6. Model A vs Model B

| Criterion | Model A, k = 5 | Model B, k = 3 |
|---|---|---|
| Resampled days / seeds | 0.88 / 0.95 | 0.94 / 0.98 |
| Split-half (odd / even days) | 0.80 / 0.96 | 0.94 / 0.83 |
| Equal weight per feature group | **0.58** (see section 5) | 0.84 |
| Airports included in fit | 1.00 | 0.91 |
| Threshold 500 / 3,000 pickups | 0.78 / 0.72 | **0.57 / 0.57** |
| Feature variant | +persistence 0.74; −volume 0.75 | **−fare 0.55** |
| Fare data quality | Fares are only described, not clustered | Fare feature uses < 50% of trips in 47 zones |
| Business content | Timing, direction and weekend patterns | Mostly core vs non-core trip length |

Agreement between the two solutions is low (ARI 0.27). They capture different
structure.

## 7. Airports and threshold sensitivity

- **Airports.** Fitting with JFK and LaGuardia changes no other Model A
  assignment (ARI 1.00).
  - Assigned afterwards, JFK lands nearest segment 1 and LaGuardia nearest
    segment 2. Both are far from those centres: 1.45× and 1.13× the
    95th-percentile member distance.
  - Their trips are structurally different: medians of 17.3 / 9.4 mi,
    pm-rush imbalance about +0.6, and Flex share 1%.
  - So they're reported as a separate, rule-defined category, not as a
    k-means cluster.
- **Threshold.**
  - Model A is moderately stable: ARI 0.78 at 500 pickups (139 zones) and
    0.72 at 3,000 (62 zones).
  - Model B is not: 0.57 at both thresholds.

## 8. Rule-based baselines

These are held-out comparisons: groups are formed on odd-day features, and
variance explained (R²) is measured on even-day features in Model A's feature
space.

| Groups | Rule | Rule R² | k-means R² (same k) | ARI vs k = 5 solution |
|---:|---|---:|---:|---:|
| 2 | Volume halves | 0.32 | 0.36 | 0.26 |
| 3 | Volume terciles | 0.31 | 0.48 | 0.23 |
| 3 | Flow pattern (imbalance sign) | 0.24 | 0.48 | 0.17 |
| 4 | Dominant weekday period | 0.43 | 0.55 | 0.17 |
| 5 | Weekend-heavy (ratio > 1.5), then dominant weekday period | 0.50 | 0.64 | 0.25 |

**About the five-group baseline.** A five-group rule can be built (groups of
14–29 zones), but it isn't fully fair:

- I designed it after seeing the k = 5 solution: its first split mirrors the
  weekend segment.
- k-means directly optimises the variance-explained metric used to compare
  them. The held-out split limits that advantage but doesn't remove it.

**What the comparisons show:**

- k-means groups summarise the eight features more compactly than these
  simple rules at every group count (4–24 points of held-out R²).
- They do **not** show that k = 5 is better *for business decisions*. That
  would need an external outcome, such as wait times, revenue or supply data.
- So the rule comparisons don't directly establish the superiority of k = 5.

## 9. Recommendation for the executive presentation

**Use Model A k = 5 as an analyst-selected operational segmentation,**
alongside the separately defined airport category (five k-means segments plus
airports).

- **Why:**
  - It is stable across days and seeds.
  - It separates timing and flow-direction patterns that the statistically
    selected k = 2 merges.
  - It doesn't cluster on fares, which are biased by Flex Fare.
- **How to present it:**
  - Lead with the robust contrast: two high-volume segments with opposite
    morning flows (78.7% of all pickups together).
  - Add the small, consistent morning-heavy outer group.
  - Show segment shares as approximate. Label the weekend/late-night segment
    and the Upper East Side memberships as weighting-sensitive.
  - Don't call k = 5 statistically optimal or outcome-validated.
- **Model B** can support trip-length context in an appendix. It shouldn't be
  used as a headline segmentation.

## 10. Reproducibility checks

- 54 tests pass. 19 are new in Phase 4 and cover:
  - histogram medians, shares, imbalance and ratios
  - date weighting and bootstrap within day type
  - standardisation and missing-value filling
  - equal-group weighting and Hungarian label matching
  - deterministic k-means and the selection gates
  - cluster ordering and the rule baselines, including the five-group rule
  - airport placement
  - both pickup-share denominators
  - reconciliation against the trip file
- Fixed seeds are used throughout (`random_state = 20250101`).
- Reruns produce byte-identical tables and charts.
- Phase 2 and Phase 3 outputs are only read, never modified.

## 11. Limitations

- **One month.** Segments may shift in other months, especially with the
  January holiday effects and the growing Flex Fare share.
- **Completed trips only.** No supply, requests or wait times. Imbalance
  describes passenger flow, not taxi availability.
- **k = 5 is an analyst choice.** Its boundaries move with feature weighting:
  20 of 101 zones change, with 72.4% pickup-weighted agreement.
- **The weekend segment and large near-balanced zones** have uncertain
  membership.
- **Fare features are biased** in Flex-heavy zones. Fares are only described
  for Model A, and with caution.
- **Names are descriptive summaries** of segment medians, not claims about
  land use or about every member zone.
