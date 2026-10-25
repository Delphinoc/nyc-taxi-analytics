# Data sources and measurement notes

## Sources

All files come from the NYC Taxi & Limousine Commission (TLC) trip record page:
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page

| File | URL | Notes |
|---|---|---|
| `yellow_tripdata_2025-01.parquet` | https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2025-01.parquet | 3,475,226 trip records, 20 columns |
| `taxi_zone_lookup.csv` | https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv | 265 zones; 264 = Unknown, 265 = Outside of NYC |
| `data_dictionary_trip_records_yellow.pdf` | https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf | Dated March 18, 2025 |

Sizes and SHA-256 checksums are recorded in `data/raw/manifest.json`, which is
tracked in Git. `src/download_data.py` checks every fresh download against
that manifest and never overwrites a recorded checksum.

### If a checksum changes

TLC can republish monthly files. If it has, `python src/download_data.py`
prints `CHANGED` for that file and exits with status 1, and the pipeline
should not be run on the new file as if it were the original. A mismatch
means the source changed, not necessarily that the download failed. To
proceed:

1. Delete the raw file and download again to rule out a corrupted transfer.
2. If it still says `CHANGED`, the published file is different. Results from
   it may not match the committed reports, figures and deck.
3. To adopt the new file deliberately, remove that file's entry from
   `data/raw/manifest.json`, rerun the download (it records the new checksum),
   rerun the full pipeline, and review every changed output. `build_deck.py`
   stops if any slide number no longer matches the values it expects.

## Terms of use

The trip records and zone lookup are public data published by the NYC Taxi &
Limousine Commission. This repository does not redistribute them; the
download script fetches them from TLC. Use of the data is subject to TLC's and
NYC Open Data's terms. The project is independent and is not endorsed by TLC
or the City of New York.

The repository's MIT License covers only the project's original code, tests
and documentation. It grants no rights to the TLC data, to outputs derived
from it beyond what TLC's terms allow, or to third-party Python packages,
which keep their own licenses.

## What the data does and does not measure

| Concept | Status | Basis |
|---|---|---|
| Completed metered Yellow Taxi trips by pickup zone and time | **Observed** | One record per completed trip |
| Fares, surcharges and card tips charged | **Observed** | Meter fields; cash tips are not recorded |
| Total ride-request demand | **Not observed** | Unfulfilled requests, street hails that never found a cab, and trips taken by other modes (Uber/Lyft, Green taxi) are absent |
| Driver supply or shortages | **Not observed** | No vehicle availability, idle time or driver location data. Pickups minus drop-offs by zone is at most a proxy for net vehicle flow, not a shortage measure |
| Surge pricing | **Not applicable to metered fares** | Metered Yellow Taxi fares use regulated rates; higher fares reflect distance, time, rate codes, tolls and surcharges. Flex Fare (app-booked, payment_type 0) prices are upfront and may include an app price multiplier, but its fare fields are incomplete, so this data can't measure it |

Any demand or supply measure built from this data is an **estimated
proxy** and is labelled as such.

## Known quirks found in Phase 1

See `reports/phase1_data_profile.md` for counts, and `docs/cleaning_rules.md`
for the Phase 2 follow-up analysis of each quirk. Highlights:

- The column is `Airport_fee` in the file but `airport_fee` in the dictionary.
- 540,149 rows (15.5%) have `passenger_count`, `RatecodeID`, `store_and_fwd_flag`,
  `congestion_surcharge` and `Airport_fee` all null, and every one has
  `payment_type = 0` (Flex Fare).
- `total_amount` equals the sum of its listed components for most VendorID 2
  rows but only about 9% of VendorID 1 rows. The common gaps (-2.50, -3.25)
  match the congestion surcharge and CBD fee, which suggests VendorID 1 also
  includes them in `extra`. This is inferred, not documented by TLC.
- Negative fares (4.1% of rows) all come from VendorID 2. Most are Flex Fare,
  dispute, cash or no-charge trips, and 81,402 of them have a non-negative
  `total_amount`. Their meaning is not documented.
