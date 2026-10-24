# Phase 3 exploratory analysis: NYC Yellow Taxi, January 2025

All numbers come from `data/processed/eda/` (built by `src/eda_aggregates.py`),
and the charts are in `reports/figures/`. Metric definitions and denominators
are in [`docs/eda_metrics.md`](../docs/eda_metrics.md).

**What this data can and can't show.** Every count is a *completed* Yellow
Taxi trip. Pickups aren't ride requests, and pickup/drop-off gaps aren't
driver shortages. Nothing here measures how many taxis were available or how
many people wanted one and didn't get one.

Labels used in this report:

- **[Observed]** measured directly from the trip records.
- **[Implication]** a possible business reading. It needs validation before
  anyone acts on it.
- **[Hypothesis]** an explanation that the data is consistent with but
  doesn't prove.

## 1. Samples and denominators

| Analysis | Rows | Excluded from 3,475,226 raw rows |
|---|---:|---:|
| Pickups (`eligible_demand`) | 3,402,692 | 2.1% |
| Drop-offs (`eligible_dropoff`, validated separately) | 3,384,588 | 2.6% |
| Distance / duration (`eligible_distance_duration`) | 3,322,460 | 4.4% |
| Metered fares (`eligible_fare`) | 2,775,340 | 20.1% (mostly Flex Fare, which is out of scope) |

Calendar: 21 weekdays, 8 weekend days and 2 holidays (1 and 20 Jan). Per-day
averages divide by these day counts.

## 2. Demand patterns (completed pickups)

### Verified observations

- **Volume [Observed].** Pickups average 112,838 per weekday, 107,550 per
  weekend day and 86,352 per holiday. Weekend days run about 5% below
  weekdays.
  - Daily counts range from 77,418 (Sun 5 Jan) to 136,372 (Thu 16 Jan).
  - The first week was markedly low: 82k–95k per day on 2–6 Jan. Dropping
    2–3 Jan raises the weekday average by 2.6%.
  - Average pickups by day of week:

    | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
    |---:|---:|---:|---:|---:|---:|---:|
    | 89k | 111k | 120k | 120k | 115k | 117k | 99k |

- **Weekday peaks [Observed]** (fig01).
  - A morning peak at 08:00 averages 5,738 pickups per weekday (5.1% of
    weekday pickups).
  - A larger evening peak at 18:00 averages 9,401 (8.3%).
  - 17:00–18:59 alone is 16.1% of weekday pickups.
- **Weekend shape [Observed].** There's no morning peak. Pickups build from
  10:00 to a flat plateau of about 6,300–6,900 per hour from 13:00 to 19:00.
  - 15.3% of weekend pickups fall between 00:00 and 03:59, compared with 2.4%
    on weekdays.
- **Concentration [Observed]** (fig03).
  - Manhattan holds 89.4% of pickups.
  - 15 of 259 active zones account for 50.6% of pickups, and 31 zones for 80%.
  - The busiest zones are Midtown Center (4.9%), Upper East Side South (4.7%),
    Upper East Side North (4.5%) and JFK Airport (4.0%).
- **Airports [Observed].**
  - JFK is the 4th-busiest pickup zone with 137,522 pickups (4.0%). LaGuardia
    is 14th with 87,401 (2.6%). Newark appears only 367 times, since Yellow
    Taxis rarely pick up in New Jersey.
  - JFK pickups are flat across the week (weekend ÷ weekday ratio 1.06).
    LaGuardia's fall on weekends (0.80).
- **Weekday vs weekend by zone [Observed].** Each ratio below is average
  pickups per weekend day ÷ per weekday.
  - Nightlife zones are weekend-heavy: East Village 2.31 and West Village
    1.83.
  - Office zones are weekday-heavy: Lenox Hill East 0.54, Midtown East 0.55
    and Midtown Center 0.58.
- **Peaks vary geographically [Observed]** (fig06). This covers the 35 zones
  with at least 20,000 weekday pickups. The figures are each zone's share of
  its own weekday pickups.
  - Residential Upper East and Upper West Side zones skew to the morning:
    Yorkville East takes 31% of its pickups at 06:00–09:59.
  - Nightlife and theatre zones skew to late evening: Greenwich Village South
    takes 45% at 20:00–23:59, and Times Square 37%.
  - 19 of the 35 zones peak at 18:00. The East and West Village peak at 22:00 and
    Greenwich Village South at 23:00.

### Potential business implications

- **[Implication]** Completed trips are concentrated by time and zone more
  than by day: weekday 17:00–19:00 in Midtown and late weekend nights in the
  Village and Times Square. Any time- or zone-targeted measure would touch the
  most completed trips there. Whether demand goes *unserved* in those windows
  can't be judged from completed trips alone.
- **[Implication]** JFK is the 4th-busiest pickup zone (137,522 pickups, close
  to the top Manhattan zones), and its volume is steady across the week.

## 3. Pickup/drop-off imbalance (observed passenger flows)

Method:

- Pickups and drop-offs are counted per zone × day type × hour, each end with
  its own validity check.
- Imbalance index I = (P − D) / (P + D). It is reported only for cells with at
  least 100 trip ends over the month, which caps the standard error at 0.10.
- Persistence = share of qualifying days (≥ 10 trip ends that day) on which
  the cell's daily net flow had its majority sign. Days with exactly zero net
  flow stay in the denominator, so they count *against* persistence. The
  share is reported only for cells with ≥ 5 qualifying days.

### Verified observations

- **Midtown Center is the strongest and most regular commuting swing
  [Observed]** (fig04).
  - It is net inbound at 07:00–09:59, with −232 to −301 trips per weekday and
    I from −0.38 to −0.61.
  - It is net outbound at 17:00–21:59, with +312 to +396 per weekday and I from
    +0.34 to +0.57.
  - Every one of these hours had the same sign on **21 of 21 weekdays**.
- **Evening outbound zones [Observed].** Midtown East (17:00–20:00) and Times
  Square/Theatre District (20:00–22:00) are net outbound by +180 to +344 trips
  per weekday, again on 21 of 21 weekdays.
- **Morning inbound zones [Observed].** West Chelsea/Hudson Yards (08:00–09:59)
  is net inbound by about −113 to −121 per weekday (I about −0.55).
- **Evening inbound zones [Observed].** Upper East Side North (−149 and −155
  per weekday at 21:00–22:59) and Upper West Side North (−126 at 21:00) are
  net inbound: trips end there more than they start.
- **Persistence is the rule for strong cells [Observed, independently
  re-verified].** There are 228 weekday cells with |I| ≥ 0.3 and at least 50
  trip ends per weekday on average. 225 of them kept the same sign on ≥ 90%
  of qualifying weekdays, and 209 on every qualifying weekday.
  - The QC pass recomputed this from the daily counts. The majority daily sign
    equals the sign of the monthly total in all 228 cells, and only 10 cell-days
    had zero net flow.
  - The result holds under stricter rules (table in section 5): at least 98%
    of evaluable strong cells stay at ≥ 90% under every combination tested.
  - The three exceptions are Hudson Sq 09:00, Lincoln Square East 23:00 and
    Midtown Center 00:00 (76–86% of days), all near the |I| = 0.3 cut-off.
  - Caveat: the cells were *selected* for a strong monthly imbalance, which
    makes high persistence partly expected. The finding is that strong
    imbalances are not driven by a few unusual days.
- **Airports are net outbound almost all day [Observed]** (fig05).
  - JFK has about +3,345 more pickups than drop-offs per weekday (I = +0.64).
  - LaGuardia has about +1,882 (I = +0.47).
  - The exception is LaGuardia at 03:00–06:59, which is net inbound (I −0.58
    to −0.91). That matches the early-departure bank.
  - **[Hypothesis]** Most travellers reach the airports by other modes, such
    as app rides, transit or private cars. Yellow Taxi drop-offs therefore
    understate inbound airport traffic. The gap says nothing about how many
    taxis were waiting or available.
- **Whole-day net inbound zones [Observed].** East Harlem South (−601 per
  weekday), Clinton West (−490), Lincoln Square West (−446) and East Harlem
  North (−438). Taxi trips end in these zones more than they start.

### Potential business implications

- **[Implication]** Directional passenger flows repeat every weekday at the
  same hours, so they are predictable. For example, completed trips end in
  UES/UWS North and East Harlem in the evening more than they start there,
  while Midtown and Times Square start more than they end.
- **[Hypothesis, not tested]** Vehicles may need repositioning from net-inbound
  to net-outbound zones in these hours. Drop-off counts don't show where
  vacant taxis are; drivers may pick up again nearby, cruise elsewhere or end
  their shift. Pickup/drop-off imbalance also doesn't show unmet demand or a
  driver shortage. Testing this needs vehicle availability data (vacant-cab
  locations, idle times) or ride-request data, neither of which is in the TLC
  trip records.

## 4. Trip and fare characteristics

### Verified observations

- **Trips are short [Observed]** (fig07). On the distance/duration-eligible
  sample:
  - Median 1.70 miles (p90 7.8, p99 19.5).
  - Median 11.8 minutes (p90 27.6).
  - Median average speed 9.6 mph.
  - Weekday speeds are slowest at midday (median 8.0 mph) and in the pm rush
    (8.8 mph), and fastest overnight (14.9 mph).
- **Rate codes [Observed].**
  - Standard rate covers 2.62M fare-eligible trips, with a median fare of
    $12.10 and a median distance of 1.56 miles.
  - JFK flat fares cover 85,338 trips, all at $70.00.
  - Negotiated fares cover 13,521 trips, with a median of $75.00.
  - Newark covers 6,838 trips (median $87.40). Nassau/Westchester covers
    6,108 trips (median $95.40).
  - Medians sit on the $0.70 meter grid, so they move in steps. Means are used
    for comparisons.
- **Base fare vs total charge by time of day [Observed, re-verified]**
  (fig08). Two different measures are compared here, for weekday
  standard-rate trips relative to midday:
  - **Metered base fare** (`fare_amount`) is the meter's time-and-distance
    charge only.
  - **Total charge excluding tips** (`total_amount − tip_amount`) is what the
    passenger was charged before tipping. It adds the documented rush-hour
    ($2.50, weekdays 16:00–20:00) and overnight ($1.00, 20:00–06:00)
    surcharges and the other fees.

  "Adjusted" uses the same distance × duration mix for every period: 24
  common strata with ≥ 30 trips in each period, covering 97.2% of trips. The
  range is the min to max across 12 specifications (4 binning schemes × 3
  sparse-stratum rules).

  | Period | Base fare, raw | Base fare, adjusted (range) | Total charge, raw | Total charge, adjusted (range) |
  |---|---:|---:|---:|---:|
  | Overnight (00–05) | +13.2% | +0.5% (−1.7 to +3.8) | +15.5% | +4.0% (+2.3 to +6.5) |
  | Morning (06–09) | −6.0% | −0.6% (−0.7 to +0.3) | −5.2% | −0.8% (−1.0 to −0.1) |
  | PM rush (16–19) | −5.7% | −1.1% (−1.5 to −1.0) | +6.9% | +11.2% (+10.6 to +12.0) |
  | Evening (20–23) | +0.9% | +0.3% (−1.0 to +0.9) | +5.4% | +4.5% (+3.7 to +4.9) |

  - For morning, pm rush and evening, adjusted **base fares** stay within
    ±1.5% of midday under every specification. The raw gaps come from
    *which* trips happen when, as the meter formula implies.
  - **Overnight** base fares are less certain: −1.7% to +3.8%. Overnight has
    the fewest trips (55,125), and several long-duration strata are sparse.
  - **Total charges** are clearly higher in the pm rush: about +11% at the
    same distance and duration. That's about the size of the $2.50 rush-hour
    surcharge on a ~$20 trip. Evening and overnight are about +4%, in line
    with the $1.00 overnight surcharge.
  - These are documented, scheduled surcharges, not dynamic pricing. Nothing
    here is evidence of surge pricing, and base-fare results shouldn't be read
    as what passengers paid.
- **By borough [Observed].** Median metered fares are Manhattan $11.40,
  Brooklyn $24.50, Queens $50.60 (largely airport trips) and Bronx $36.50.
  Staten Island and EWR have fewer than 100 fare-eligible trips, which are
  too few to interpret.

### Flex Fare composition effects

- **Share grows through the month [Observed]** (fig09).
  - Flex Fare (app-booked upfront fares) was 4.0–5.2% of daily pickups on
    2–6 Jan, then mostly 11–24% per day from 8 Jan.
  - It peaked at 31.9% on Sun 19 Jan and was 23.2% on 31 Jan.
- **Concentrated at night and early morning [Observed].**
  - On weekdays the Flex share is 24–29% at 04:00–08:59 but 5–6% at
    10:00–13:59.
  - On weekends it is 30–36% between 00:00 and 04:59.
- **Flex trips are longer [Observed].** Median 2.25 miles and 13.7 minutes,
  vs 1.62 miles and 11.4 minutes for metered trips.
- **The metered fare sample shifts as Flex grows [Observed].**
  - The fare-eligible share of pickups fell from 92% (2–3 Jan) to 66%
    (19 Jan).
  - Compared with all pickups, the fare sample over-represents midday (34.5%
    vs 31.2%) and under-represents overnight (6.5% vs 7.8%), evening (19.4%
    vs 20.7%), Brooklyn (0.85% vs 1.91%) and the Bronx (0.21% vs 0.42%).
  - Across days (excluding 1 Jan), Flex share and mean metered fare move in
    opposite directions (Spearman ρ = −0.46). Flex share and median metered
    distance barely move together (ρ = −0.18).
- **[Hypothesis]** As Flex absorbs longer, off-peak and outer-borough trips,
  the remaining metered sample becomes shorter and more Manhattan-midday, which
  pulls metered fare averages down. The rank correlation is descriptive, not
  causal. Day-of-week and the post-holiday period also differ across these
  days.
- **[Implication]** Fare trends across January shouldn't be read from metered
  trips alone. Compare fares within the same period, zone and distance band,
  or report Flex and metered separately. Flex `fare_amount` isn't a usable
  fare (Phase 2). Its median `total_amount` is $18.88, vs $20.45 for metered
  trips, but Flex totals omit some fees, so the two aren't directly
  comparable.

## 5. Sensitivity checks

| Check | Result |
|---|---|
| Fare standardisation: 4 binning schemes × min 1/30/100 trips per stratum | Adjusted base fare vs midday: pm rush −1.0% to −1.5%, morning −0.7% to +0.3%, evening −1.0% to +0.9%, overnight −1.7% to +3.8%. Total charge, pm rush: +10.6% to +12.0% |
| Persistence: daily minimum 10/25/50 trip ends × 5/15/21 qualifying days | ≥ 98% of evaluable strong cells stay at ≥ 90% same-sign days (e.g. 144 of 145 at 50 trip ends and all 21 days) |
| Persistence: compare with the monthly sign instead of the majority sign | Same counts in every combination |
| Persistence: leave zero-net days out of the denominator | 226 instead of 225 of 228 |
| Drop zero-distance and zero-duration rows from demand | −2.5% rows. 9 of the top-10 zones are unchanged (Union Sq is replaced by Midtown North) |
| Drop VendorID 7 from demand | −0.04% rows |
| Count holidays as weekdays | Weekday average −2.0% |
| Drop 2–3 Jan (post-holiday) | Weekday average +2.6% |
| Speed cap 50 / 100 mph / none instead of 80 | p99 distance and speed change by ≤ 0.5% |
| Drop informational meter-range rows from fares | Mean fare −0.01% |
| Imbalance from trips valid at both ends only | Pearson r = 0.99993 with the main index, and 99.4% of cells keep their sign |
| Imbalance minimum volume 50 / 100 / 200 / 400 | 37% / 28% / 20% / 14% of cells are reportable, covering 97.9% / 96.3% / 93.6% / 89.6% of trip ends |

The main findings don't depend on these choices. The largest sensitivities
are the weekday average's dependence on the post-holiday days (about ±2.6%)
and the overnight base-fare comparison (−1.7% to +3.8%). Full tables:
`fare_peak_adjustment_sensitivity.csv` and `persistence_sensitivity.csv` in
`data/processed/eda/`.

## 6. Unresolved questions

1. Why was the first week of January so low? Holiday travel, weather and
   school calendars weren't analysed, so comparing with another month would
   help.
2. What drove the Flex Fare jump on 7–8 Jan and the 31.9% spike on 19 Jan?
   Possible causes are app-side promotion, a partner launch or weather. This
   is unverified and needs an external source.
3. Why do 52k Flex trips have zero recorded distance, concentrated in weekday
   16:00–19:00 (Phase 2)? This affects Flex distance analysis.
4. How do Yellow Taxi flows relate to FHV (Uber/Lyft) flows in the same zones?
   That would show whether the imbalances are taxi-specific or reflect all
   for-hire travel.
5. No supply data: imbalance can't be translated into driver availability or
   wait times.

## 7. Recommended clustering unit and candidate features (for review, not implemented)

**Unit: pickup zone.** I'd use the 103 NYC zones with at least 1,000 eligible
pickups in January (98.6% of pickups). Zone-hour cells are too sparse: only
28% are reportable. Zones are also the level at which operational decisions
are made.

**Treat airports separately.** JFK and LaGuardia are structural outliers on
volume and flow. Cluster them on their own, or flag them and exclude them.

Candidate features, all already computed or derivable from the Phase 3
tables:

| Group | Feature | Source table |
|---|---|---|
| Volume | log average weekday pickups; weekend ÷ weekday ratio | `zone_pickups` |
| Timing | Share of weekday pickups in each of the 5 periods; peak hour; weekend 00:00–03:59 share | `zone_peak_profile`, hourly counts |
| Direction | Weekday imbalance index in morning, pm rush and evening; whole-day index | `zone_hour_flow`, `zone_day_flow` |
| Persistence | Share of weekdays with a consistent evening sign | `zone_hour_flow` |
| Trip mix | Median trip distance and duration from the zone; Flex share | to add: zone-level `trip_characteristics` |

Notes for the design:

- Standardise the features before clustering.
- Shares and indices are already scale-free. Volume should go in on a log
  scale or be left out, so it doesn't dominate.
- Avoid metered-fare features. The Flex-driven sample shift (section 4) makes
  them uneven across zones.
- Validate k with silhouette and stability across bootstrap samples.
- Check that the clusters are interpretable against the patterns above.

## 8. Charts

| File | Shows |
|---|---|
| `figures/fig01_hourly_pickups.png` | Average pickups per day by hour, weekday vs weekend |
| `figures/fig02_daily_pickups.png` | Daily pickups by day type |
| `figures/fig03_top_zones.png` | Top 15 pickup zones and their share |
| `figures/fig04_imbalance_heatmap.png` | Weekday imbalance index for the 25 busiest zones × hour |
| `figures/fig05_airport_flows.png` | JFK and LaGuardia pickups vs drop-offs per weekday hour |
| `figures/fig06_peak_geography.png` | Morning vs late-evening pickup share by zone |
| `figures/fig07_trip_distributions.png` | Trip distance and duration distributions |
| `figures/fig08_fare_peak_adjustment.png` | Base fare and total charge vs midday, raw and mix-adjusted, with robustness ranges |
| `figures/fig09_flex_share.png` | Flex Fare share by day and by hour |
