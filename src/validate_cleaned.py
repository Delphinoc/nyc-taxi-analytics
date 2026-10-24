"""Validate the flagged trip table and write the Phase 2 quality report.

Checks that the raw file is unchanged, that the output keeps every raw row and
column exactly, that flags behave as documented, and summarises flag overlaps,
row-count waterfalls, eligibility counts and whether any rule excludes a
vendor, payment type or period disproportionately.

Usage:
    python src/validate_cleaned.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from cleaning_rules import (
    AMOUNT_COMPONENTS,
    DQ_FLAGS,
    ELIGIBILITY_RULES,
    INFO_FLAGS,
    REVERSAL_KEY,
    SCOPE_FLAGS,
    find_reversal_pairs,
    to_cents,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "yellow_tripdata_2025-01.parquet"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
FLAGGED_PATH = PROJECT_ROOT / "data" / "processed" / "yellow_tripdata_2025-01_flagged.parquet"
REPORTS_DIR = PROJECT_ROOT / "reports"

EXCLUDING_FLAGS = DQ_FLAGS + SCOPE_FLAGS
ELIGIBILITY = list(ELIGIBILITY_RULES)
PAYMENT_LABELS = {0: "Flex Fare", 1: "Credit card", 2: "Cash", 3: "No charge",
                  4: "Dispute", 5: "Unknown", 6: "Voided"}


def pct(count: int | float, total: int | float) -> float:
    return round(100 * count / total, 3) if total else 0.0


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integrity_checks(raw_path: Path, flagged_path: Path) -> dict:
    """Raw file unchanged, raw columns preserved exactly, nulls and flags well-formed."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    raw_file = pq.ParquetFile(raw_path)
    out_file = pq.ParquetFile(flagged_path)
    raw_columns = raw_file.schema_arrow.names
    out_schema = out_file.schema_arrow

    # Compare raw columns batch by batch so the full tables never sit in memory together.
    raw_values_identical = True
    out_batches = out_file.iter_batches(batch_size=500_000, columns=raw_columns)
    for raw_batch in raw_file.iter_batches(batch_size=500_000):
        if not raw_batch.equals(next(out_batches)):
            raw_values_identical = False
            break

    flag_columns = EXCLUDING_FLAGS + INFO_FLAGS + ELIGIBILITY
    flags = pq.read_table(flagged_path, columns=flag_columns + ["trip_duration_min", "avg_speed_mph"])
    duration = flags.column("trip_duration_min").to_pandas()
    speed = flags.column("avg_speed_mph").to_pandas()
    raw_nulls = {name: sum(raw_file.metadata.row_group(rg).column(i).statistics.null_count
                           for rg in range(raw_file.num_row_groups))
                 for i, name in enumerate(raw_columns)}
    out_nulls = {name: sum(out_file.metadata.row_group(rg).column(out_schema.get_field_index(name))
                           .statistics.null_count for rg in range(out_file.num_row_groups))
                 for name in raw_columns}

    return {
        "raw_sha256_matches_manifest": sha256_of(raw_path)
        == manifest[raw_path.name]["sha256"],
        "input_rows": raw_file.metadata.num_rows,
        "output_rows": out_file.metadata.num_rows,
        "row_counts_equal": raw_file.metadata.num_rows == out_file.metadata.num_rows,
        "raw_schema_preserved": all(
            out_schema.field(f.name).type == f.type for f in raw_file.schema_arrow
        ),
        "raw_values_identical": raw_values_identical,
        "raw_null_counts_unchanged": raw_nulls == out_nulls,
        "flag_columns_boolean": all(
            pa.types.is_boolean(out_schema.field(c).type) for c in flag_columns
        ),
        "flag_columns_null_free": all(flags.column(c).null_count == 0 for c in flag_columns),
        "speed_null_exactly_when_duration_nonpositive": bool(
            (speed.isna() == (duration <= 0)).all()
        ),
        "output_columns": out_schema.names[len(raw_columns):],
    }


def reversal_audit(raw_path: Path) -> dict:
    """Cardinality of the negative/positive matching, recomputed from the raw file.

    Key uniqueness is counted on a 64-bit hash of the key columns, which needs
    far less memory than grouping on them; a collision is negligible at ~3.5M rows.
    """
    keys = pq.read_table(raw_path, columns=REVERSAL_KEY + ["total_amount", "payment_type"]).to_pandas()
    has_partner, reversed_original, ambiguous = find_reversal_pairs(keys)
    negative = (keys["total_amount"] < 0).to_numpy()
    unmatched = negative & ~has_partner & ~ambiguous
    key_hash = pd.util.hash_pandas_object(keys[REVERSAL_KEY], index=False).to_numpy()
    _, key_counts = np.unique(key_hash, return_counts=True)
    key_sizes = pd.Series(key_counts)
    positive_keys = key_hash[(keys["total_amount"] > 0).to_numpy()]
    unmatched_keys = pd.Series(key_hash[unmatched])
    return {
        "rows_sharing_a_key_with_another_row": int(key_sizes[key_sizes > 1].sum()),
        "key_group_sizes_above_1": {int(k): int(v) for k, v in
                                    key_sizes[key_sizes > 1].value_counts().items()},
        "negative_rows": int(negative.sum()),
        "matched_one_to_one": int(has_partner.sum()),
        "positive_rows_matched": int(reversed_original.sum()),
        "ambiguous_negative_rows": int(ambiguous.sum()),
        "unmatched_negative_rows": int(unmatched.sum()),
        "unmatched_sharing_key_with_positive_of_other_amount": int(
            unmatched_keys.isin(positive_keys).sum()
        ),
        "unmatched_by_payment_type": {
            str(k): int(v) for k, v in keys.loc[unmatched, "payment_type"].value_counts().items()
        },
    }


def fee_identity(raw_path: Path) -> dict:
    """Check total - components == -(congestion + CBD + airport fees), in integer cents.

    Recomputed from the raw columns for non-Flex rows of VendorIDs 1 and 2,
    one batch at a time to limit memory. Nulls are reported and left as nulls
    (a null makes the identity fail).
    """
    money = ["total_amount", *AMOUNT_COMPONENTS]
    fee_columns = ["congestion_surcharge", "cbd_congestion_fee", "Airport_fee"]
    whole_cents = True
    totals = {vendor: {"rows": 0, "rows_with_null_amounts": 0, "identity_holds": 0,
                       "residual_zero": 0, "residual_zero_with_fees_zero": 0}
              for vendor in (1, 2)}
    residual_counts = {vendor: pd.Series(dtype="int64") for vendor in (1, 2)}

    raw_file = pq.ParquetFile(raw_path)
    for batch in raw_file.iter_batches(batch_size=500_000,
                                       columns=["VendorID", "payment_type", *money]):
        frame = batch.to_pandas()
        whole_cents &= all(
            bool(((frame[c] * 100).round() - frame[c] * 100).abs().max() < 1e-6)
            for c in money if frame[c].notna().any()
        )
        for vendor in (1, 2):
            subset = frame[(frame["VendorID"] == vendor) & (frame["payment_type"] != 0)]
            residual = to_cents(subset["total_amount"]) - sum(
                to_cents(subset[c]) for c in AMOUNT_COMPONENTS
            )
            fees = sum(to_cents(subset[c]) for c in fee_columns)
            counts = totals[vendor]
            counts["rows"] += len(subset)
            counts["rows_with_null_amounts"] += int(subset[money].isna().any(axis=1).sum())
            counts["identity_holds"] += int((residual == -fees).fillna(False).sum())
            counts["residual_zero"] += int((residual == 0).fillna(False).sum())
            counts["residual_zero_with_fees_zero"] += int(
                ((residual == 0) & (fees == 0)).fillna(False).sum()
            )
            residual_counts[vendor] = residual_counts[vendor].add(
                residual.value_counts(), fill_value=0
            )

    result = {"all_amounts_whole_cents": whole_cents}
    for vendor, counts in totals.items():
        counts["identity_exceptions"] = counts["rows"] - counts["identity_holds"]
        top = residual_counts[vendor].sort_values(ascending=False).head(5)
        counts["top_residuals_cents"] = {str(int(k)): int(v) for k, v in top.items()}
        result[f"vendor{vendor}_non_flex"] = counts
    return result


def load_flags(path: Path) -> pd.DataFrame:
    columns = (EXCLUDING_FLAGS + INFO_FLAGS + ELIGIBILITY
               + ["VendorID", "payment_type", "tpep_pickup_datetime"])
    return pq.read_table(path, columns=columns).to_pandas()


def flag_summary(frame: pd.DataFrame) -> list[dict]:
    total = len(frame)
    rows = []
    for name in EXCLUDING_FLAGS + INFO_FLAGS:
        kind = "data quality" if name in DQ_FLAGS else "scope" if name in SCOPE_FLAGS else "info"
        hits = frame[name]
        only = hits & (frame[EXCLUDING_FLAGS].sum(axis=1) == 1) if name in EXCLUDING_FLAGS else None
        rows.append({
            "flag": name,
            "kind": kind,
            "rows": int(hits.sum()),
            "pct": pct(int(hits.sum()), total),
            "rows_with_no_other_excluding_flag": None if only is None else int(only.sum()),
        })
    return rows


def overlap_matrix(frame: pd.DataFrame) -> dict[str, dict[str, int]]:
    """Pairwise counts of rows carrying both flags (diagonal = flag total)."""
    values = frame[EXCLUDING_FLAGS].astype("int32")
    matrix = values.T @ values
    return {a: {b: int(matrix.loc[a, b]) for b in EXCLUDING_FLAGS} for a in EXCLUDING_FLAGS}


def waterfall(frame: pd.DataFrame, rules: list[str]) -> list[dict]:
    """Sequential waterfall: rows removed by each rule after the earlier ones."""
    remaining = pd.Series(True, index=frame.index)
    steps = [{"step": "raw rows", "removed": 0, "remaining": len(frame),
              "flagged_in_total": None}]
    for rule in rules:
        removed = remaining & frame[rule]
        remaining &= ~frame[rule]
        steps.append({
            "step": rule,
            "removed": int(removed.sum()),
            "remaining": int(remaining.sum()),
            "flagged_in_total": int(frame[rule].sum()),
        })
    return steps


def eligibility_summary(frame: pd.DataFrame) -> dict:
    """Eligible rows per purpose and how exclusions split between DQ and scope."""
    total = len(frame)
    result = {}
    for eligibility, rules in ELIGIBILITY_RULES.items():
        dq_rules = [r for r in rules if r in DQ_FLAGS]
        scope_rules = [r for r in rules if r in SCOPE_FLAGS]
        dq_hit = frame[dq_rules].any(axis=1)
        scope_hit = frame[scope_rules].any(axis=1)
        eligible = int(frame[eligibility].sum())
        result[eligibility] = {
            "eligible": eligible,
            "eligible_pct": pct(eligible, total),
            "excluded": total - eligible,
            "excluded_data_quality_only": int((dq_hit & ~scope_hit).sum()),
            "excluded_scope_only": int((scope_hit & ~dq_hit).sum()),
            "excluded_both": int((dq_hit & scope_hit).sum()),
        }
    return result


def group_rates(frame: pd.DataFrame, by: pd.Series, columns: list[str]) -> pd.DataFrame:
    """Share of rows (%) where each column is True, per group, plus group size."""
    rates = frame[columns].groupby(by).mean().mul(100).round(2)
    rates.insert(0, "rows", by.value_counts().reindex(rates.index))
    return rates


def disproportion(frame: pd.DataFrame) -> dict:
    """Exclusion rates by vendor, payment type and pickup date/hour."""
    in_period = ~frame["scope_outside_period"]
    excluded = ~frame[ELIGIBILITY]
    excluded.columns = [c.replace("eligible_", "excluded_") for c in ELIGIBILITY]
    combined = pd.concat([excluded, frame[DQ_FLAGS]], axis=1)

    by_vendor = group_rates(combined, frame["VendorID"], list(combined.columns))
    by_payment = group_rates(combined, frame["payment_type"].map(PAYMENT_LABELS), list(combined.columns))
    in_period_frame = combined[in_period]
    pickup = frame.loc[in_period, "tpep_pickup_datetime"]
    by_date = group_rates(in_period_frame, pickup.dt.date, list(excluded.columns))
    by_hour = group_rates(in_period_frame, pickup.dt.hour, list(excluded.columns))

    def spread(table: pd.DataFrame) -> dict:
        return {c: {"min": float(table[c].min()), "max": float(table[c].max()),
                    "min_at": str(table[c].idxmin()), "max_at": str(table[c].idxmax())}
                for c in excluded.columns}

    return {
        "by_vendor": by_vendor.reset_index(names="VendorID").to_dict(orient="records"),
        "by_payment_type": by_payment.reset_index(names="payment_type").to_dict(orient="records"),
        "by_pickup_date_spread": spread(by_date),
        "by_pickup_hour_spread": spread(by_hour),
    }


def reason_combinations(flagged_path: Path, top: int = 12) -> dict:
    """How many excluding flags rows carry, and the most common flag combinations."""
    reasons = pq.read_table(flagged_path, columns=["exclusion_reasons"]).column(0).to_pandas()
    n_flags = reasons.str.count(";").add(1).where(reasons != "", 0)
    return {
        "rows_by_number_of_excluding_flags": {
            int(k): int(v) for k, v in n_flags.value_counts().sort_index().items()
        },
        "top_combinations": {
            (k or "(none)"): int(v) for k, v in reasons.value_counts().head(top).items()
        },
    }


def to_markdown(report: dict) -> str:
    integ = report["integrity"]
    total = integ["input_rows"]
    lines = [
        "# Phase 2 quality report: flagged Yellow Taxi trips, January 2025",
        "",
        "Generated by `src/validate_cleaned.py` from "
        "`data/processed/yellow_tripdata_2025-01_flagged.parquet`. "
        "No rows were deleted; every count below is a flag on the full raw table.",
        "",
        "## Integrity checks",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for key, value in integ.items():
        if key != "output_columns":
            lines.append(f"| {key} | {value} |")

    rev = report["reversal_audit"]
    lines += [
        "", "## Negative-to-positive matching audit", "",
        "A match is an observed accounting pattern (exact negation, same vendor, "
        "timestamps and zones), not a verified trip reversal.", "",
        f"- Negative-total rows: {rev['negative_rows']:,}",
        f"- Matched one-to-one: {rev['matched_one_to_one']:,} "
        f"(positive rows matched: {rev['positive_rows_matched']:,})",
        f"- Ambiguous (more than one candidate on either side): {rev['ambiguous_negative_rows']:,}",
        f"- Unmatched: {rev['unmatched_negative_rows']:,}, of which "
        f"{rev['unmatched_sharing_key_with_positive_of_other_amount']:,} share the key with a "
        f"positive row of a different amount; by payment type {rev['unmatched_by_payment_type']}",
        f"- Rows sharing the 5-column key with another row: "
        f"{rev['rows_sharing_a_key_with_another_row']:,} "
        f"(group sizes {rev['key_group_sizes_above_1']})",
    ]
    fee = report["fee_identity"]
    lines += [
        "", "## Fee reconciliation identity (integer cents, raw columns)", "",
        "Identity tested on non-Flex rows: total_amount - sum(components) == "
        "-(congestion_surcharge + cbd_congestion_fee + Airport_fee). Nulls are not filled.", "",
        f"All amounts are whole cents: {fee['all_amounts_whole_cents']}", "",
        "| Subset | Rows | Rows with nulls | Identity holds | Exceptions | Residual 0 "
        "| Top residuals (cents) |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for label in ["vendor1_non_flex", "vendor2_non_flex"]:
        r = fee[label]
        lines.append(f"| {label} | {r['rows']:,} | {r['rows_with_null_amounts']:,} "
                     f"| {r['identity_holds']:,} | {r['identity_exceptions']:,} "
                     f"| {r['residual_zero']:,} | {r['top_residuals_cents']} |")
    m = report["meter_range_check"]
    lines += ["", f"Informational meter-range check: {m['rows_flagged']:,} rows flagged, "
              f"{m['of_which_fare_eligible']:,} of them fare-eligible (not excluded; see docs).",
              "", "## Flags", "",
              "| Flag | Kind | Rows | % | Rows with no other excluding flag |",
              "|---|---|---:|---:|---:|"]
    for row in report["flags"]:
        only = "" if row["rows_with_no_other_excluding_flag"] is None \
            else f"{row['rows_with_no_other_excluding_flag']:,}"
        lines.append(f"| {row['flag']} | {row['kind']} | {row['rows']:,} | {row['pct']} | {only} |")

    lines += ["", "Rows by number of excluding flags: "
              + ", ".join(f"{k} flags: {v:,}"
                          for k, v in report["combinations"]["rows_by_number_of_excluding_flags"].items()),
              "", "Most common flag combinations:", "",
              "| exclusion_reasons | Rows |", "|---|---:|"]
    for combo, n in report["combinations"]["top_combinations"].items():
        lines.append(f"| {combo.replace(';', '; ')} | {n:,} |")
    lines += ["", "Largest pairwise overlaps (rows carrying both flags):", ""]
    pairs = sorted(
        ((a, b, report["overlap"][a][b]) for a, b in combinations(EXCLUDING_FLAGS, 2)),
        key=lambda item: -item[2],
    )
    for a, b, n in pairs[:8]:
        lines.append(f"- {a} + {b}: {n:,}")

    lines += ["", "## Eligibility", "",
              "| Purpose | Eligible | % | Excluded | DQ only | Scope only | Both |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for name, e in report["eligibility"].items():
        lines.append(
            f"| {name} | {e['eligible']:,} | {e['eligible_pct']} | {e['excluded']:,} "
            f"| {e['excluded_data_quality_only']:,} | {e['excluded_scope_only']:,} "
            f"| {e['excluded_both']:,} |"
        )

    for name, steps in report["waterfalls"].items():
        lines += ["", f"### Waterfall: {name}", "",
                  "| Step | Removed here | Remaining | Flagged in total |",
                  "|---|---:|---:|---:|"]
        for s in steps:
            total_flagged = "" if s["flagged_in_total"] is None else f"{s['flagged_in_total']:,}"
            lines.append(f"| {s['step']} | {s['removed']:,} | {s['remaining']:,} | {total_flagged} |")

    disp = report["disproportion"]
    excluded_cols = [c.replace("eligible_", "excluded_") for c in ELIGIBILITY]
    lines += ["", "## Exclusion rates by group (%)", "", "### By VendorID", "",
              "| VendorID | Rows | " + " | ".join(excluded_cols) + " |",
              "|---|---:|" + "---:|" * len(excluded_cols)]
    for r in disp["by_vendor"]:
        lines.append(f"| {r['VendorID']} | {r['rows']:,} | "
                     + " | ".join(str(r[c]) for c in excluded_cols) + " |")
    lines += ["", "### By payment type", "",
              "| Payment type | Rows | " + " | ".join(excluded_cols) + " |",
              "|---|---:|" + "---:|" * len(excluded_cols)]
    for r in disp["by_payment_type"]:
        lines.append(f"| {r['payment_type']} | {r['rows']:,} | "
                     + " | ".join(str(r[c]) for c in excluded_cols) + " |")
    lines += ["", "### Spread across January pickup dates and hours", "",
              "| Measure | Daily min | Daily max | Hourly min | Hourly max |",
              "|---|---|---|---|---|"]
    for c in excluded_cols:
        d = disp["by_pickup_date_spread"][c]
        h = disp["by_pickup_hour_spread"][c]
        lines.append(f"| {c} | {d['min']} ({d['min_at']}) | {d['max']} ({d['max_at']}) "
                     f"| {h['min']} (h{h['min_at']}) | {h['max']} (h{h['max_at']}) |")

    lines += ["", "Rule definitions and justification: `docs/cleaning_rules.md`. "
              "Full numbers: `reports/phase2_quality_report.json`.", ""]
    return "\n".join(lines)


def build_report(raw_path: Path = RAW_PATH, flagged_path: Path = FLAGGED_PATH) -> dict:
    integrity = integrity_checks(raw_path, flagged_path)
    frame = load_flags(flagged_path)
    report = {
        "integrity": integrity,
        "reversal_audit": reversal_audit(raw_path),
        "fee_identity": fee_identity(raw_path),
        "flags": flag_summary(frame),
        "overlap": overlap_matrix(frame),
        "combinations": reason_combinations(flagged_path),
        "eligibility": eligibility_summary(frame),
        "waterfalls": {name: waterfall(frame, rules) for name, rules in ELIGIBILITY_RULES.items()},
        "disproportion": disproportion(frame),
    }
    meter = frame["info_fare_outside_standard_meter_range"]
    report["meter_range_check"] = {
        "rows_flagged": int(meter.sum()),
        "of_which_fare_eligible": int((meter & frame["eligible_fare"]).sum()),
    }
    report["peak_arrow_memory_mb"] = round(pa.default_memory_pool().max_memory() / 1024**2, 1)
    return report


def main() -> int:
    report = build_report()
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "phase2_quality_report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    (REPORTS_DIR / "phase2_quality_report.md").write_text(
        to_markdown(report), encoding="utf-8", newline="\n"
    )
    failed = [k for k, v in report["integrity"].items() if v is False]
    print(json.dumps(report["integrity"], indent=2))
    print(json.dumps(report["eligibility"], indent=2))
    if failed:
        print("FAILED integrity checks:", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
