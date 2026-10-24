"""Draw the Phase 3 charts from the aggregate tables in data/processed/eda/.

Charts are static PNGs sized for slides (16:9) and written to reports/figures/.
They read only the aggregate CSVs, so they can be redrawn without touching
the trip-level data.

Usage:
    python src/eda_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDA_DIR = PROJECT_ROOT / "data" / "processed" / "eda"
FIGURES_DIR = PROJECT_ROOT / "reports" / "figures"

# Palette: validated reference instance from the dataviz guidance (light mode).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
NEUTRAL = "#f0efec"
DIVERGING = LinearSegmentedColormap.from_list(
    "inbound_outbound", ["#184f95", "#6da7ec", NEUTRAL, "#ee8a89", "#b42f2e"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 11,
    "text.color": INK, "axes.labelcolor": INK_SECONDARY, "axes.edgecolor": AXIS,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 15,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 14,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "legend.frameon": False, "lines.linewidth": 2,
})


def read(name: str) -> pd.DataFrame:
    return pd.read_csv(EDA_DIR / f"{name}.csv", keep_default_na=False, na_values=[""])


def caption(fig, text: str) -> None:
    fig.text(0.01, 0.01, text, ha="left", va="bottom", fontsize=9, color=INK_SECONDARY, wrap=True)


def headline(fig, title: str, subtitle: str) -> None:
    """Short bold title plus a one-line takeaway, left-aligned at the top of the figure."""
    fig.text(0.01, 0.975, title, ha="left", va="top", fontsize=16, fontweight="bold", color=INK)
    fig.text(0.01, 0.915, subtitle, ha="left", va="top", fontsize=11.5, color=INK_SECONDARY)


def save(fig, name: str) -> Path:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURES_DIR / f"{name}.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def thousands(ax, axis="y") -> None:
    fmt = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def hour_axis(ax) -> None:
    ax.set_xticks(range(0, 24, 3))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 24, 3)])
    ax.set_xlim(-0.5, 23.5)


# --------------------------------------------------------------------------- charts

def hourly_pickups() -> Path:
    hourly = read("hourly_pickups")
    fig, ax = plt.subplots(figsize=(11, 6.2))
    for kind, color in [("weekday", BLUE), ("weekend", ORANGE)]:
        data = hourly[hourly["day_type"] == kind].sort_values("hour")
        ax.plot(data["hour"], data["avg_pickups_per_day"], color=color, label=kind)
        last = data.iloc[-1]
        ax.annotate(kind.capitalize(), (last["hour"], last["avg_pickups_per_day"]),
                    xytext=(6, 0), textcoords="offset points", color=INK_SECONDARY,
                    va="center", fontsize=10)
    weekday = hourly[hourly["day_type"] == "weekday"].set_index("hour")["avg_pickups_per_day"]
    for hour in [8, 18]:
        ax.annotate(f"{weekday[hour]:,.0f} at {hour:02d}:00", (hour, weekday[hour]),
                    xytext=(0, 10), textcoords="offset points", ha="center", fontsize=9,
                    color=INK_SECONDARY)
    ax.set_ylim(0, None)
    hour_axis(ax)
    thousands(ax)
    ax.set_xlabel("Pickup hour")
    ax.set_ylabel("Average completed pickups per day")
    headline(fig, "Hourly pickup pattern",
             "Weekdays peak at 08:00 and 18:00; weekends build through the afternoon "
             "and stay busy after midnight")
    ax.legend(loc="upper left")
    caption(fig, "Completed Yellow Taxi pickups (eligible_demand), January 2025. Averages divide by "
                 "21 weekdays and 8 weekend days; holidays (1 and 20 Jan) are excluded. "
                 "Completed pickups are not total ride requests.")
    fig.subplots_adjust(top=0.84, bottom=0.16, right=0.93)
    return save(fig, "fig01_hourly_pickups")


def daily_pickups() -> Path:
    daily = read("daily_pickups")
    daily["date"] = pd.to_datetime(daily["date"])
    colors = {"weekday": BLUE, "weekend": ORANGE, "holiday": AQUA}
    fig, ax = plt.subplots(figsize=(11, 6.2))
    ax.bar(daily["date"], daily["pickups"], color=daily["day_type"].map(colors), width=0.8,
           edgecolor=SURFACE, linewidth=1)
    ax.legend(handles=[Patch(color=c, label=k) for k, c in colors.items()], loc="upper left",
              ncols=3)
    thousands(ax)
    ax.set_ylim(0, daily["pickups"].max() * 1.15)
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
    ax.set_ylabel("Completed pickups")
    headline(fig, "Daily pickups",
             "Lowest in the first week of January and on Sundays and Mondays")
    caption(fig, "Daily completed pickups (eligible_demand), January 2025. 1 and 20 Jan are "
                 "federal holidays. The first week's low counts may reflect the holiday season; "
                 "weather and events were not analysed.")
    fig.subplots_adjust(top=0.84, bottom=0.16)
    return save(fig, "fig02_daily_pickups")


def top_zones(n: int = 15) -> Path:
    zones = read("zone_pickups").head(n).iloc[::-1]
    fig, ax = plt.subplots(figsize=(11, 6.6))
    colors = np.where(zones["is_airport"].astype(str) == "True", ORANGE, BLUE)
    ax.barh(zones["zone"], zones["pickups"], color=colors, height=0.7)
    for y, (count, share) in enumerate(zip(zones["pickups"], zones["share_pct"])):
        ax.text(count, y, f"  {share:.1f}%", va="center", fontsize=9, color=INK_SECONDARY)
    ax.legend(handles=[Patch(color=BLUE, label="Manhattan zone"), Patch(color=ORANGE, label="Airport")],
              loc="lower right")
    ax.grid(axis="y", visible=False)
    thousands(ax, "x")
    ax.set_xlim(0, zones["pickups"].max() * 1.12)
    ax.set_xlabel("Completed pickups, January 2025")
    cumulative = read("zone_pickups").head(n)["cumulative_share_pct"].iloc[-1]
    headline(fig, f"Top {n} pickup zones",
             f"{n} of 259 zones account for {cumulative:.0f}% of completed pickups")
    caption(fig, "Labels show each zone's share of all 3,402,692 eligible pickups. "
                 "Manhattan zones make up 89% of pickups.")
    fig.subplots_adjust(top=0.85, left=0.27, bottom=0.14)
    return save(fig, "fig03_top_zones")


def imbalance_heatmap(n: int = 25) -> Path:
    flow = read("zone_hour_flow")
    weekday = flow[flow["day_type"] == "weekday"]
    busiest = (weekday.groupby("zone")["trip_ends"].sum().nlargest(n).index)
    grid = (weekday[weekday["zone"].isin(busiest)]
            .pivot(index="zone", columns="hour", values="imbalance_index")
            .reindex(index=busiest, columns=range(24)))
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_facecolor("#e1e0d9")
    image = ax.imshow(grid.to_numpy(dtype=float), cmap=DIVERGING, vmin=-1, vmax=1, aspect="auto")
    ax.set_yticks(range(len(grid)))
    ax.set_yticklabels(grid.index, fontsize=9)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 2)])
    ax.grid(False)
    ax.set_xlabel("Hour of day (weekdays)")
    bar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.02)
    bar.set_label("Imbalance index (P − D) / (P + D)")
    bar.set_ticks([-1, -0.5, 0, 0.5, 1])
    bar.set_ticklabels(["−1", "−0.5", "0", "+0.5", "+1"])
    bar.ax.text(0.5, 1.02, "net\noutbound", transform=bar.ax.transAxes, ha="center", va="bottom",
                fontsize=9, color=INK_SECONDARY)
    bar.ax.text(0.5, -0.02, "net\ninbound", transform=bar.ax.transAxes, ha="center", va="top",
                fontsize=9, color=INK_SECONDARY)
    headline(fig, "Weekday pickup/drop-off imbalance by zone and hour",
             "Midtown is net inbound in the morning and net outbound in the evening; "
             "airports are net outbound most hours")
    caption(fig, f"{n} zones with the most weekday trip ends. P = completed pickups, D = completed "
                 "drop-offs over 21 weekdays. Grey cells have < 100 trip ends (unstable). "
                 "Observed passenger flows, not driver supply or unmet demand.")
    fig.subplots_adjust(top=0.86, left=0.24, bottom=0.13, right=0.95)
    return save(fig, "fig04_imbalance_heatmap")


def airport_flows() -> Path:
    flow = read("zone_hour_flow")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8), sharey=True)
    for ax, zone in zip(axes, ["JFK Airport", "LaGuardia Airport"]):
        data = flow[(flow["zone"] == zone) & (flow["day_type"] == "weekday")].set_index("hour")
        data = data.reindex(range(24), fill_value=0)
        ax.plot(data.index, data["pickups"] / 21, color=BLUE, label="Pickups")
        ax.plot(data.index, data["dropoffs"] / 21, color=ORANGE, label="Drop-offs")
        ax.set_title(zone, fontsize=13)
        hour_axis(ax)
        ax.set_xticks(range(0, 24, 6))
        ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 24, 6)])
        ax.set_xlabel("Hour (weekdays)")
        ax.set_ylim(0, None)
    axes[0].set_ylabel("Average per weekday")
    axes[0].legend(loc="upper left")
    headline(fig, "Airport pickups and drop-offs",
             "Both airports have far more taxi pickups than drop-offs, except LaGuardia before 07:00")
    caption(fig, "Completed pickups and validated drop-offs per weekday (21 days). Many airport "
                 "arrivals by other modes are not in this data; the gap does not measure taxi availability.")
    fig.subplots_adjust(top=0.78, bottom=0.18, wspace=0.08)
    return save(fig, "fig05_airport_flows")


def peak_geography() -> Path:
    peaks = read("zone_peak_profile")
    peaks = peaks[peaks["weekday_pickups"] >= 20_000]
    fig, ax = plt.subplots(figsize=(11, 6.6))
    sizes = 20 + 180 * peaks["weekday_pickups"] / peaks["weekday_pickups"].max()
    ax.scatter(peaks["share_morning_pct"], peaks["share_evening_pct"], s=sizes, color=BLUE,
               alpha=0.75, edgecolor=SURFACE, linewidth=1.5)
    label = pd.concat([peaks.nlargest(4, "share_morning_pct"), peaks.nlargest(4, "share_evening_pct"),
                       peaks.nlargest(2, "weekday_pickups")]).drop_duplicates("zone")
    for _, row in label.iterrows():
        ax.annotate(row["zone"], (row["share_morning_pct"], row["share_evening_pct"]),
                    xytext=(6, 4), textcoords="offset points", fontsize=9, color=INK_SECONDARY)
    ax.set_xlabel("Share of weekday pickups at 06:00–09:59 (%)")
    ax.set_ylabel("Share of weekday pickups at 20:00–23:59 (%)")
    headline(fig, "Where the peaks happen",
             "Residential zones peak in the morning; nightlife zones peak late in the evening")
    caption(fig, f"{len(peaks)} zones with at least 20,000 weekday pickups in January 2025; "
                 "point size shows volume. Shares are of each zone's own weekday pickups.")
    fig.subplots_adjust(top=0.85, bottom=0.15)
    return save(fig, "fig06_peak_geography")


def trip_distributions() -> Path:
    hist = read("trip_histograms")
    stats = read("trip_characteristics")
    overall = stats[(stats["period"] == "all") & (stats["day_type"] == "all")].set_index("metric")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.6))
    for ax, metric, label, unit in [(axes[0], "trip_distance", "Trip distance", "miles"),
                                    (axes[1], "trip_duration_min", "Trip duration", "minutes")]:
        data = hist[hist["metric"] == metric]
        ax.bar(data["bin_start"], data["trips"], width=data["bin_end"] - data["bin_start"],
               align="edge", color=BLUE, edgecolor=SURFACE, linewidth=0.5)
        median = overall.loc[metric, "median"]
        ax.axvline(median, color=INK_SECONDARY, linewidth=1, linestyle="--")
        ax.text(median, ax.get_ylim()[1] * 0.95, f" median {median:.1f}", fontsize=9,
                color=INK_SECONDARY, va="top")
        ax.set_xlabel(f"{label} ({unit}; last bar includes longer trips)")
        thousands(ax)
        ax.set_title(label, fontsize=13)
    axes[0].set_ylabel("Trips")
    headline(fig, "Trip distance and duration",
             "Most trips are short: median 1.7 miles and 12 minutes")
    caption(fig, "Trips eligible for distance/duration analysis (3,322,460). Distance capped at 30 "
                 "miles and duration at 90 minutes for display only.")
    fig.subplots_adjust(top=0.78, bottom=0.18, wspace=0.15)
    return save(fig, "fig07_trip_distributions")


def fare_peak_adjustment() -> Path:
    table = read("fare_peak_adjusted").set_index("pu_period")
    spread = read("fare_peak_adjustment_sensitivity")
    spread = spread.groupby(["measure", "pu_period"])[["min", "max"]].first()
    periods = [p for p in table.index if p != "midday (10-15)"]
    series = [
        ("base_fare_raw_mean_vs_midday_pct", None, "Base fare, raw", MUTED),
        ("base_fare_adjusted_mean_vs_midday_pct", "fare_amount",
         "Base fare, same distance and duration mix", BLUE),
        ("charge_adjusted_mean_vs_midday_pct", "charge_excl_tip",
         "Total charge excl. tips, same mix", ORANGE),
    ]
    x = np.arange(len(periods))
    width = 0.26
    fig, ax = plt.subplots(figsize=(11, 6.2))
    for i, (column, measure, label, color) in enumerate(series):
        values = table.loc[periods, column].to_numpy()
        ax.bar(x + (i - 1) * width, values, width=width - 0.03, color=color, label=label)
        if measure:
            low = spread.loc[[(measure, p) for p in periods], "min"].to_numpy()
            high = spread.loc[[(measure, p) for p in periods], "max"].to_numpy()
            ax.errorbar(x + (i - 1) * width, values, yerr=[values - low, high - values],
                        fmt="none", ecolor=INK_SECONDARY, elinewidth=1, capsize=3)
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.set_xticks(x)
    ax.set_xticklabels(periods)
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("Difference in mean vs midday (%)")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncols=3, fontsize=10)
    headline(fig, "Base fare vs total charge by time of day",
             "At the same distance and duration, base fares barely move; total charges are "
             "about 11% higher in the pm rush")
    caption(fig, "Weekday standard-rate metered trips (eligible_fare, RatecodeID 1). Adjusted means use "
                 "a common set of distance x duration strata with >= 30 trips in every period. Whiskers: "
                 "range across 4 binning schemes x 3 sparse-stratum rules. Total charge includes the "
                 "documented rush-hour and overnight surcharges. Descriptive, not evidence of surge pricing.")
    fig.subplots_adjust(top=0.8, bottom=0.2)
    return save(fig, "fig08_fare_peak_adjustment")


def flex_share() -> Path:
    daily = read("daily_pickups")
    daily["date"] = pd.to_datetime(daily["date"])
    hourly = read("flex_share_hourly")
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.8), gridspec_kw={"width_ratios": [1.3, 1]})
    ax = axes[0]
    ax.plot(daily["date"], daily["flex_share_pct"], color=BLUE, marker="o", markersize=4)
    ax.set_ylim(0, 40)
    ax.xaxis.set_major_locator(matplotlib.dates.DayLocator(bymonthday=[1, 8, 15, 22, 29]))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
    ax.set_ylabel("Flex Fare share of pickups (%)")
    ax.set_title("By day", fontsize=13)
    ax = axes[1]
    for kind, color in [("weekday", BLUE), ("weekend", ORANGE)]:
        data = hourly[hourly["day_type"] == kind].sort_values("hour")
        ax.plot(data["hour"], data["flex_share_pct"], color=color, label=kind)
    hour_axis(ax)
    ax.set_xticks(range(0, 24, 6))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 24, 6)])
    ax.set_ylim(0, 40)
    ax.set_title("By hour", fontsize=13)
    ax.legend(loc="upper right")
    headline(fig, "Flex Fare share of pickups",
             "Grew from about 4% to about 20% during January; highest at night and early morning")
    caption(fig, "Share of eligible pickups with payment_type 0 (Flex Fare: upfront app-booked fares). "
                 "Flex trips are excluded from metered fare analysis, so the fare sample's time mix "
                 "shifts as Flex grows.")
    fig.subplots_adjust(top=0.78, bottom=0.17, wspace=0.18)
    return save(fig, "fig09_flex_share")


CHARTS = [hourly_pickups, daily_pickups, top_zones, imbalance_heatmap, airport_flows,
          peak_geography, trip_distributions, fare_peak_adjustment, flex_share]


def main() -> int:
    for chart in CHARTS:
        print(save_path := chart())
    return 0


if __name__ == "__main__":
    sys.exit(main())
