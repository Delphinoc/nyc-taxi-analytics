"""Draw the Phase 4 segmentation charts from data/processed/zones/.

Usage:
    python src/cluster_figures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from eda_figures import (  # noqa: F401  (also applies the shared chart style)
    AXIS, BLUE, DIVERGING, GRID, INK, INK_SECONDARY, MUTED, ORANGE, SURFACE, AQUA,
    caption, headline, hour_axis, plt, save,
)
from matplotlib.patches import Patch
from zone_features import ZONES_DIR, load_base_tables

YELLOW, MAGENTA = "#eda100", "#e87ba4"
CLUSTER_COLORS = [BLUE, ORANGE, AQUA, YELLOW, MAGENTA]

# Descriptive names, assigned after reading the profiles (see the Phase 4 report).
CLUSTER_NAMES = {
    ("A", 5): {
        0: "High volume, morning outflow",
        1: "High volume, morning inflow",
        2: "Weekend and late night",
        3: "Mid volume, evening inflow",
        4: "Low volume, morning-heavy",
    },
    ("B", 3): {
        0: "Short trips, high volume",
        1: "Longer trips, mixed timing",
        2: "Longest trips, morning-heavy",
    },
}
PROFILE_FEATURES = [
    ("avg_daily_pickups", "Pickups per day", "{:,.0f}"),
    ("weekend_to_weekday_ratio", "Weekend ÷ weekday rate", "{:.2f}"),
    ("weekday_share_morning", "Weekday share 06–09", "{:.0%}"),
    ("weekday_share_pm_rush", "Weekday share 16–19", "{:.0%}"),
    ("weekday_share_evening", "Weekday share 20–23", "{:.0%}"),
    ("weekend_late_night_share", "Weekend share 00–03", "{:.0%}"),
    ("imbalance_morning", "Morning imbalance (06–09)", "{:+.2f}"),
    ("imbalance_pm_rush", "Evening imbalance (16–19)", "{:+.2f}"),
    ("median_distance_mi", "Median trip distance (mi)", "{:.1f}"),
    ("median_duration_min", "Median trip duration (min)", "{:.0f}"),
    ("median_fare_per_mile", "Metered fare per mile ($)", "{:.2f}"),
    ("flex_share", "Flex Fare share", "{:.0%}"),
]


def read(name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(ZONES_DIR / f"{name}.csv", keep_default_na=False, na_values=[""], **kwargs)


def model_selection() -> Path:
    table = read("model_selection")
    summary = json.loads((ZONES_DIR / "clustering_summary.json").read_text(encoding="utf-8"))
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.8), sharey=True)
    for ax, model in zip(axes, ["A", "B"]):
        data = table[table["model"] == model]
        ax.plot(data["k"], data["silhouette"], color=BLUE, marker="o", markersize=6, label="Silhouette")
        ax.plot(data["k"], data["bootstrap_ari_median"], color=ORANGE, marker="s", markersize=6,
                label="Day-resampling stability (median ARI)")
        ineligible = data[~data["eligible"].astype(str).eq("True")]
        ax.scatter(ineligible["k"], ineligible["silhouette"], s=120, facecolors="none",
                   edgecolors=INK_SECONDARY, linewidths=1.2, zorder=3)
        for kind, k in [("rule", summary[model]["rule_k"]), ("recommended", summary[model]["recommended_k"])]:
            ax.axvline(k, color=MUTED, linewidth=1, linestyle="--" if kind == "rule" else "-")
            ax.text(k, 1.02, f" {kind} k={k}", fontsize=9, color=INK_SECONDARY, va="bottom",
                    transform=ax.get_xaxis_transform())
        ax.set_title(f"Model {model}: " + ("operational behaviour" if model == "A" else "trip characteristics"),
                     fontsize=13, pad=22)
        ax.set_xlabel("Number of clusters (k)")
        ax.set_ylim(0, 1.0)
        ax.set_xticks(range(2, 9))
    axes[0].set_ylabel("Score (0–1)")
    axes[0].legend(loc="lower left", fontsize=9.5)
    headline(fig, "Choosing the number of clusters",
             "Silhouette alone favours k = 2; several richer k values are almost as stable")
    caption(fig, "101 pickup zones (≥ 1,000 pickups, airports held out). Stability = median adjusted Rand "
                 "index vs the full-month fit across 30 bootstrap resamples of days. Open circles: k fails "
                 "a gate (smallest cluster < 5 zones, stability < 0.60 or seed agreement < 0.90).")
    fig.subplots_adjust(top=0.76, bottom=0.18, wspace=0.08)
    return save(fig, "fig10_model_selection")


def profile_heatmap(model: str, k: int, name: str) -> Path:
    prof = read(f"model_{model}_k{k}_profiles").set_index("cluster")
    feats = [f for f in PROFILE_FEATURES if f[0] in prof.columns]
    values = prof[[f[0] for f in feats]]
    z = (values - values.mean()) / values.std(ddof=0).replace(0, 1)
    names = CLUSTER_NAMES[(model, k)]
    fig, ax = plt.subplots(figsize=(12.5, 1.6 + 0.55 * len(feats)))
    ax.imshow(z.T.to_numpy(), cmap=DIVERGING, vmin=-2, vmax=2, aspect="auto")
    for i, (column, _, fmt) in enumerate(feats):
        for j, cluster in enumerate(prof.index):
            ax.text(j, i, fmt.format(values.loc[cluster, column]), ha="center", va="center",
                    fontsize=9.5, color=INK)
    ax.set_yticks(range(len(feats)))
    ax.set_yticklabels([f[1] for f in feats], fontsize=10)
    ax.set_xticks(range(len(prof)))
    ax.set_xticklabels([f"{names[c]}\n{int(prof.loc[c, 'zones'])} zones · "
                        f"{prof.loc[c, 'pickup_share_of_all_pct']:.1f}% of all pickups" for c in prof.index],
                       fontsize=9.5)
    ax.xaxis.tick_top()
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    title = "Model A segments: operational behaviour" if model == "A" else "Model B segments: trip characteristics"
    headline(fig, title, "Cluster medians in original units; colour shows how far each value sits "
                         "above (red) or below (blue) the average across segments")
    caption(fig, "Medians across the zones in each segment. Shares are of all 3,402,692 eligible pickups "
                 "(airports 6.6% and zones below 1,000 pickups 1.4% are outside the fit). Imbalance = "
                 "(pickups − drop-offs) ÷ (pickups + drop-offs), weekdays. Fare per mile: metered trips ≥ 1 mi.")
    fig.subplots_adjust(top=0.78, bottom=0.07, left=0.2, right=0.98)
    return save(fig, name)


def representative_zones(per_segment: int = 4) -> Path:
    clusters = read("zone_clusters", index_col=0)
    names = CLUSTER_NAMES[("A", 5)]
    rows = []
    for cluster in range(5):
        members = clusters[clusters["cluster_A_k5"] == cluster].nlargest(per_segment, "pickups")
        rows.append(members.assign(segment=cluster))
        rows.append(pd.DataFrame({"zone": [""], "pickups": [np.nan], "segment": [cluster]}))
    table = pd.concat(rows[:-1], ignore_index=True).iloc[::-1].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(11.5, 8.2))
    daily = table["pickups"] / 31
    colors = [CLUSTER_COLORS[int(c)] for c in table["segment"]]
    ax.barh(range(len(table)), daily.fillna(0), color=colors, height=0.72)
    ax.set_yticks(range(len(table)))
    ax.set_yticklabels(table["zone"], fontsize=10)
    for y, value in enumerate(daily):
        if pd.notna(value):
            ax.text(value * 1.06, y, f"{value:,.0f}", va="center", fontsize=9, color=INK_SECONDARY)
    ax.set_xscale("log")
    ax.set_xlim(20, 15_000)
    ax.xaxis.set_major_formatter(plt.matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Average pickups per day (log scale)")
    ax.legend(handles=[Patch(color=CLUSTER_COLORS[c], label=names[c]) for c in range(5)],
              loc="lower right", fontsize=9.5)
    headline(fig, "Representative zones in each Model A segment",
             f"The {per_segment} largest zones per segment; volumes differ by about 40x between segments")
    caption(fig, "Log scale, so equal gaps mean equal ratios. Zones closest to each segment centre are "
                 "listed in the Phase 4 report.")
    fig.subplots_adjust(top=0.86, bottom=0.1, left=0.27, right=0.97)
    return save(fig, "fig13_representative_zones")


def operational_differences() -> Path:
    clusters = read("zone_clusters", index_col=0)
    counts = load_base_tables()["zone_date_hour_counts"]
    weekday = counts[counts["day_type"] == "weekday"].merge(
        clusters[["cluster_A_k5"]], left_on="zone_id", right_index=True)
    by_hour = weekday.groupby(["cluster_A_k5", "hour"])[["pickups", "dropoffs"]].sum()
    names = CLUSTER_NAMES[("A", 5)]
    fig, axes = plt.subplots(1, 2, figsize=(13, 6))
    for cluster in range(5):
        data = by_hour.loc[cluster]
        share = data["pickups"] / data["pickups"].sum() * 100
        index = (data["pickups"] - data["dropoffs"]) / (data["pickups"] + data["dropoffs"])
        axes[0].plot(share.index, share, color=CLUSTER_COLORS[cluster], label=names[cluster])
        axes[1].plot(index.index, index, color=CLUSTER_COLORS[cluster], label=names[cluster])
    axes[0].set_ylabel("Share of the segment's weekday pickups (%)")
    axes[0].set_title("When pickups happen", fontsize=13)
    axes[1].axhline(0, color=AXIS, linewidth=1)
    axes[1].set_ylabel("Imbalance (P − D) ÷ (P + D)")
    axes[1].set_ylim(-1, 1)
    axes[1].set_title("Direction of passenger flow", fontsize=13)
    for ax in axes:
        hour_axis(ax)
        ax.set_xticks(range(0, 24, 6))
        ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 24, 6)])
        ax.set_xlabel("Hour (weekdays)")
    axes[0].set_ylim(0, None)
    fig.legend(handles=[Patch(color=CLUSTER_COLORS[c], label=names[c]) for c in range(5)],
               loc="upper left", bbox_to_anchor=(0.01, 0.86), ncols=5, fontsize=9.5, frameon=False)
    headline(fig, "How the Model A segments differ through the weekday",
             "Timing and direction of completed trips, pooled over each segment's zones")
    caption(fig, "Weekdays only (21 days). Positive imbalance = more pickups than drop-offs (net outbound "
                 "passenger flow). These are observed trip flows, not taxi supply or unmet demand.")
    fig.subplots_adjust(top=0.72, bottom=0.15, wspace=0.22)
    return save(fig, "fig14_segment_operations")


def stability() -> Path:
    sens = read("model_sensitivity")
    labels = {
        "seed_ari_mean": "Different random seeds",
        "bootstrap_ari_median": "Resampled days (median)",
        "split_half_odd_days": "Odd days only",
        "split_half_even_days": "Even days only",
        "airports_included_in_fit": "Airports included in fit",
        "threshold_500_pickups": "Threshold 500 pickups",
        "threshold_3000_pickups": "Threshold 3,000 pickups",
        "equal_group_weighting": "Equal weight per feature group",
        "variant_A_plus_persistence": "Add persistence features",
        "variant_A_without_volume": "Drop volume feature",
        "variant_B_without_fare": "Drop fare feature",
    }
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.4), sharex=True)
    for ax, (model, k) in zip(axes, [("A", 5), ("B", 3)]):
        data = sens[(sens["model"] == model) & (sens["k"] == k)].copy()
        data["label"] = data["check"].map(labels)
        data = data.dropna(subset=["label"]).iloc[::-1]
        colors = [BLUE if v >= 0.65 else ORANGE for v in data["ari_vs_main"]]
        ax.barh(data["label"], data["ari_vs_main"], color=colors, height=0.65)
        for y, v in enumerate(data["ari_vs_main"]):
            ax.text(v + 0.01, y, f"{v:.2f}", va="center", fontsize=9, color=INK_SECONDARY)
        ax.axvline(0.65, color=MUTED, linewidth=1, linestyle="--")
        ax.set_xlim(0, 1.12)
        ax.set_title(f"Model {model}, k = {k}", fontsize=13)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Agreement with the main solution (adjusted Rand index)")
    fig.legend(handles=[Patch(color=BLUE, label="ARI ≥ 0.65"), Patch(color=ORANGE, label="ARI < 0.65")],
               loc="upper right", bbox_to_anchor=(0.99, 0.86), ncols=2, fontsize=9.5, frameon=False)
    headline(fig, "How robust are the segments?",
             "Model A is stable across days and thresholds but sensitive to feature weighting; "
             "Model B depends on its fare feature")
    caption(fig, "ARI = 1 means identical assignments, 0 means chance-level agreement. Threshold checks compare "
                 "only zones present in both populations. 0.65 is a reading guide, not a statistical test.")
    fig.subplots_adjust(top=0.78, bottom=0.14, left=0.2, wspace=0.75, right=0.98)
    return save(fig, "fig15_segment_stability")


CHARTS = [
    model_selection,
    lambda: profile_heatmap("A", 5, "fig11_model_a_profiles"),
    lambda: profile_heatmap("B", 3, "fig12_model_b_profiles"),
    representative_zones,
    operational_differences,
    stability,
]


def main() -> int:
    for chart in CHARTS:
        print(chart())
    return 0


if __name__ == "__main__":
    sys.exit(main())
