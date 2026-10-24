# NYC Taxi Demand & Supply Analytics

Portfolio project analysing NYC TLC Yellow Taxi trip records (January 2025)
to study trip patterns, demand and supply proxies, and fares.

**Status:** Phase 1 (environment, data acquisition, raw profile) and Phase 2
(rule-based flagging and eligibility, no deletion) are complete. Phase 3
(exploratory analysis) is implemented and awaiting review. Clustering and the
presentation are later phases.

## Repository layout

```
data/raw/          Original TLC files, never modified (gitignored; manifest.json is tracked)
data/processed/    Derived datasets from later phases (gitignored)
docs/              Data sources and measurement notes
reports/           Data profile and analysis write-ups
src/               Scripts: download, profiling, cleaning rules, cleaning, validation
tests/             pytest unit tests
requirements.txt   Pinned Python dependencies
```

## Setup (Windows, PowerShell)

Requires Python 3.12 (tested with 3.12.10) and about 100 MB of disk for the data.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, use `python3.12 -m venv .venv` and `source .venv/bin/activate`.

## Reproduce Phase 1

```powershell
python src/download_data.py        # download raw files, record and verify SHA-256
python src/download_data.py --verify   # later: confirm raw files are unchanged
python src/profile_raw.py          # writes reports/phase1_data_profile.{md,json}
python -m pytest -q                # unit tests
```

## Reproduce Phase 2

```powershell
python src/clean_trips.py          # writes data/processed/yellow_tripdata_2025-01_flagged.parquet
python src/validate_cleaned.py     # integrity checks + reports/phase2_quality_report.{md,json}
```

The flagged file keeps every raw row and column unchanged and adds derived
columns, `dq_*` / `scope_*` / `info_*` flags, four eligibility flags and an
`exclusion_reasons` string. Filter on the eligibility flag for your purpose:

```python
import pandas as pd
trips = pd.read_parquet("data/processed/yellow_tripdata_2025-01_flagged.parquet",
                        filters=[("eligible_demand", "==", True)])
```

Rules and their justification: [docs/cleaning_rules.md](docs/cleaning_rules.md).
Each script takes 10 to 15 seconds. Peak memory is about 0.65 GB for cleaning and 1.2 GB for validation.

Profiling reads a few columns at a time and peaked at about 670 MB of Arrow
memory on the full month, finishing in roughly 10 seconds on a Ryzen 7 laptop.

## Reproduce Phase 3 (exploratory analysis)

```powershell
python src/eda_aggregates.py       # aggregate tables -> data/processed/eda/ (gitignored)
python src/eda_figures.py          # charts -> reports/figures/
```

Findings: [reports/phase3_eda_report.md](reports/phase3_eda_report.md).
Metric definitions and denominators: [docs/eda_metrics.md](docs/eda_metrics.md).
Aggregation takes about 25 seconds and peaks around 1.6 GB of memory; charts
take about 5 seconds.

## Data and limitations

See [docs/data_sources.md](docs/data_sources.md). In short, the data records
completed metered trips only. It does not measure total ride requests, driver
availability, or surge pricing, so any demand or supply figures in this project
are proxies and will be labelled that way.
