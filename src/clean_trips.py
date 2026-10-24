"""Build the flagged January 2025 trip table from the raw Parquet file.

Every raw row and column is kept unchanged, in raw order. The output adds
derived columns, dq_/scope_/info_ flags, eligibility flags and a readable
``exclusion_reasons`` string. Processing is one batch at a time, so peak
memory is roughly one batch plus the reversal-matching keys.

The raw file is opened read-only; the output is written to a temporary file
and then moved into place, so reruns are idempotent and an interrupted run
never leaves a partial output.

Usage:
    python src/clean_trips.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from cleaning_rules import (
    DQ_FLAGS,
    ELIGIBILITY_RULES,
    REVERSAL_KEY,
    SCOPE_FLAGS,
    add_derived_columns,
    add_flags,
    find_reversal_pairs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "yellow_tripdata_2025-01.parquet"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "yellow_tripdata_2025-01_flagged.parquet"

EXCLUDING_FLAGS = DQ_FLAGS + SCOPE_FLAGS
# Rows processed per batch; smaller batches lower peak memory at a small speed cost.
BATCH_ROWS = 250_000


def clean(raw_path: Path = RAW_PATH, output_path: Path = OUTPUT_PATH) -> dict:
    """Flag every raw row and write the result to ``output_path``."""
    started = time.perf_counter()
    raw_file = pq.ParquetFile(raw_path)

    keys = pq.read_table(raw_path, columns=REVERSAL_KEY + ["total_amount"]).to_pandas()
    has_partner, reversed_original, ambiguous = find_reversal_pairs(keys)
    del keys

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".parquet.part")
    writer = None
    offset = 0
    rows_written = 0
    try:
        for batch in raw_file.iter_batches(batch_size=BATCH_ROWS):
            raw_table = pa.Table.from_batches([batch])
            trips = raw_table.to_pandas()
            rows = slice(offset, offset + len(trips))
            flagged = add_flags(
                add_derived_columns(trips),
                has_partner[rows],
                reversed_original[rows],
                ambiguous[rows],
            )
            flagged["exclusion_reasons"] = build_reason_strings(flagged)
            # Raw columns are copied from Arrow untouched (pandas would turn
            # nullable integer columns into floats); only new columns come from pandas.
            new_columns = [c for c in flagged.columns if c not in raw_table.column_names]
            added = pa.Table.from_pandas(flagged[new_columns], preserve_index=False)
            table = pa.Table.from_arrays(
                raw_table.columns + added.columns,
                names=raw_table.column_names + new_columns,
            )
            if writer is None:
                writer = pq.ParquetWriter(temp_path, table.schema)
            writer.write_table(table)
            offset += len(trips)
            rows_written += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    temp_path.replace(output_path)

    return {
        "input_rows": raw_file.metadata.num_rows,
        "output_rows": rows_written,
        "seconds": round(time.perf_counter() - started, 1),
        "peak_arrow_memory_mb": round(pa.default_memory_pool().max_memory() / 1024**2, 1),
    }


def build_reason_strings(flagged: pd.DataFrame) -> pd.Series:
    """Semicolon-joined names of the dq_/scope_ flags set on each row ('' if none)."""
    reasons = pd.Series("", index=flagged.index, dtype=object)
    for name in EXCLUDING_FLAGS:
        reasons = reasons.where(~flagged[name], reasons + name + ";")
    return reasons.str.rstrip(";")


def main() -> int:
    summary = clean()
    print(summary)
    if summary["input_rows"] != summary["output_rows"]:
        print("ERROR: row count changed")
        return 1
    print(f"Wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("Eligibility flags:", ", ".join(ELIGIBILITY_RULES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
