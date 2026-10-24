# NYC Taxi Demand & Supply Analytics

Portfolio project analysing NYC TLC Yellow Taxi trip records (January 2025)
to study trip patterns, demand and supply proxies, and fares.

**Status:** Phase 1 complete (environment, data acquisition, raw data profile).
Cleaning, analysis, clustering and presentation are later phases.

## Repository layout

```
data/raw/          Original TLC files, never modified (gitignored; manifest.json is tracked)
data/processed/    Derived datasets from later phases (gitignored)
docs/              Data sources and measurement notes
reports/           Data profile and analysis write-ups
src/               Scripts: download_data.py, profile_raw.py
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
python -m pytest -q                # unit tests for the profiling checks
```

Profiling reads a few columns at a time and peaked at about 670 MB of Arrow
memory on the full month, finishing in roughly 10 seconds on a Ryzen 7 laptop.

## Data and limitations

See [docs/data_sources.md](docs/data_sources.md). In short, the data records
completed metered trips only. It does not measure total ride requests, driver
availability, or surge pricing, so any demand or supply figures in this project
are proxies and will be labelled that way.
