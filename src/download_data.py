"""Download the official NYC TLC source files used in this project.

Files are written to data/raw/ and never modified afterwards. A manifest
(data/raw/manifest.json) records the source URL, size and SHA-256 of each
file so later runs can verify the originals are unchanged. A fresh download
is checked against the committed manifest; if TLC has republished a file, the
script reports CHANGED and exits with status 1 instead of overwriting the
recorded checksum.

Usage:
    python src/download_data.py            # download missing files, verify all
    python src/download_data.py --verify   # only verify existing files
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"

# Links taken from https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
SOURCES = {
    "yellow_tripdata_2025-01.parquet": (
        "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2025-01.parquet"
    ),
    "taxi_zone_lookup.csv": (
        "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
    ),
    "data_dictionary_trip_records_yellow.pdf": (
        "https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf"
    ),
}

CHUNK_SIZE = 1024 * 1024


def sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    """Stream a URL to disk via a temporary file so partial downloads never remain."""
    temp_path = destination.with_suffix(destination.suffix + ".part")
    # nyc.gov rejects requests without a browser-like User-Agent (HTTP 403).
    headers = {"User-Agent": "Mozilla/5.0 (nyc-tlc-taxi-analytics data download)"}
    with requests.get(url, stream=True, timeout=60, headers=headers) as response:
        response.raise_for_status()
        with temp_path.open("wb") as handle:
            for chunk in response.iter_content(CHUNK_SIZE):
                handle.write(chunk)
    temp_path.replace(destination)


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def verify(manifest: dict) -> bool:
    """Check every manifest entry exists with the recorded size and checksum."""
    all_ok = True
    for name in SOURCES:
        path = RAW_DIR / name
        entry = manifest.get(name)
        if entry is None or not path.exists():
            print(f"MISSING   {name}")
            all_ok = False
            continue
        ok = path.stat().st_size == entry["size_bytes"] and sha256_of(path) == entry["sha256"]
        print(f"{'OK' if ok else 'CHANGED':<9} {name}")
        all_ok &= ok
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="only verify existing files")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    if not args.verify:
        for name, url in SOURCES.items():
            path = RAW_DIR / name
            if path.exists() and name in manifest:
                print(f"SKIP      {name} (already downloaded)")
                continue
            print(f"DOWNLOAD  {name}")
            download(url, path)
            if name in manifest:
                # Keep the recorded checksum so verify() can detect a republished file.
                continue
            manifest[name] = {
                "url": url,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_of(path),
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            # Save after each file so a later failure doesn't lose earlier entries.
            MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return 0 if verify(manifest) else 1


if __name__ == "__main__":
    sys.exit(main())
