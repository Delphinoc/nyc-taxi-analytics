"""Build the five-slide executive deck (Phase 5) as an editable PowerPoint file.

Every number on the slides is read from the Phase 2-4 outputs in
data/processed/ and checked against the values reported in
reports/phase3_eda_report.md and reports/phase4_clustering_report.md before
the deck is written (see ``verify``). Charts are native PowerPoint charts, so
they stay editable.

Usage:
    python src/build_deck.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_TICK_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDA_DIR = PROJECT_ROOT / "data" / "processed" / "eda"
ZONES_DIR = PROJECT_ROOT / "data" / "processed" / "zones"
OUTPUT_PATH = PROJECT_ROOT / "reports" / "nyc_taxi_executive_deck.pptx"

# Palette: deep navy dominates, taxi yellow is the single accent (fills only,
# never yellow text on white), slate greys support.
NAVY = RGBColor(0x14, 0x21, 0x3D)
NAVY_2 = RGBColor(0x22, 0x33, 0x55)
INK = RGBColor(0x1F, 0x29, 0x37)
SLATE = RGBColor(0x4B, 0x55, 0x63)
MUTED = RGBColor(0x6B, 0x72, 0x80)
GRID = RGBColor(0xE2, 0xE5, 0xEA)
PANEL = RGBColor(0xF3, 0xF4, 0xF6)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
YELLOW = RGBColor(0xF2, 0xB7, 0x05)
YELLOW_SOFT = RGBColor(0xFD, 0xF3, 0xCC)
ICE = RGBColor(0xC9, 0xD3, 0xE0)
STEEL = RGBColor(0x3B, 0x5B, 0x8C)
SKY = RGBColor(0x8F, 0xA3, 0xBF)
LINE_GREY = RGBColor(0xB8, 0xC0, 0xCC)

FONT = "Calibri"
SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.6)
SOURCE = ("Source: NYC TLC Yellow Taxi trip records, January 2025 (nyc.gov/tlc). "
          "Completed trips only; eligibility rules per project Phase 2.")
AUDIENCE = "a hypothetical fleet operations and dispatch planning team"


# --------------------------------------------------------------------------- data

def load_numbers() -> dict:
    """Read every displayed figure from the project outputs."""
    hourly = pd.read_csv(EDA_DIR / "hourly_pickups.csv")
    zones = pd.read_csv(EDA_DIR / "zone_pickups.csv", keep_default_na=False, na_values=[""])
    boroughs = pd.read_csv(EDA_DIR / "borough_pickups.csv", keep_default_na=False, na_values=[""])
    fare = pd.read_csv(EDA_DIR / "fare_peak_adjusted.csv").set_index("pu_period")
    persistence = pd.read_csv(EDA_DIR / "persistence_sensitivity.csv")
    summary = json.loads((ZONES_DIR / "clustering_summary.json").read_text(encoding="utf-8"))
    profiles = pd.read_csv(ZONES_DIR / "model_A_k5_profiles.csv").set_index("cluster")
    counts = pd.read_parquet(ZONES_DIR / "zone_date_hour_counts.parquet")
    weighting = pd.read_csv(ZONES_DIR / "model_A_weighting_comparison.csv")

    midtown = counts[(counts["zone_id"] == 161) & (counts["day_type"] == "weekday")]
    daily = (midtown.assign(net=midtown["pickups"] - midtown["dropoffs"])
             .pivot_table(index="date", columns="hour", values="net", aggfunc="sum")
             .reindex(columns=range(24), fill_value=0))
    weekdays = daily.shape[0]

    recon = summary["reconciliation"]
    total = recon["eligible_pickups_source"]
    shares = {int(c): v for c, v in recon["solutions"]["cluster_A_k5"]["pickup_share_of_all_pct"].items()}
    base_persist = persistence[(persistence["min_daily_trip_ends"] == 10)
                               & (persistence["min_qualifying_days"] == 5)].iloc[0]
    weekday_hourly = hourly[hourly["day_type"] == "weekday"].set_index("hour")["avg_pickups_per_day"]
    weekend_hourly = hourly[hourly["day_type"] == "weekend"].set_index("hour")["avg_pickups_per_day"]

    return {
        "total_eligible": total,
        "weekday_hourly": weekday_hourly.reindex(range(24)).to_numpy(),
        "weekend_hourly": weekend_hourly.reindex(range(24)).to_numpy(),
        "peak_18": float(weekday_hourly[18]),
        "peak_08": float(weekday_hourly[8]),
        "manhattan_pct": float(boroughs.set_index("borough").loc["Manhattan", "share_pct"]),
        "top15_pct": float(zones.iloc[14]["cumulative_share_pct"]),
        "zones_with_pickups": len(zones),
        "fare_base_pm_pct": float(fare.loc["pm rush (16-19)", "base_fare_adjusted_mean_vs_midday_pct"]),
        "fare_charge_pm_pct": float(fare.loc["pm rush (16-19)", "charge_adjusted_mean_vs_midday_pct"]),
        "midtown_daily": daily,
        "midtown_avg": daily.mean().to_numpy(),
        "midtown_weekdays": weekdays,
        "midtown_am_inbound_days": int((daily[[7, 8, 9]] < 0).all(axis=1).sum()),
        "midtown_pm_outbound_days": int((daily[[17, 18, 19, 20, 21]] > 0).all(axis=1).sum()),
        "strong_cells": int(base_persist["strong_cells"]),
        "strong_persistent": int(base_persist["share_ge_90pct_majority_sign"]),
        "segment_shares": shares,
        "segment_zones": {int(c): int(n) for c, n in profiles["zones"].items()},
        "airport_pct": recon["airport_share_pct"],
        "below_threshold_pct": recon["below_threshold_share_pct"],
        "fitted_pct": recon["fitted_share_pct"],
        "fitted_zones": recon["zones_fitted"],
        "rule_k": summary["A"]["rule_k"],
        "recommended_k": summary["A"]["recommended_k"],
        "weight_moved": summary["A"]["weighting_comparison"]["zones_moved"],
        "weight_agree_pct": summary["A"]["weighting_comparison"]["pickup_weighted_agreement_pct"],
        "profiles": profiles,
        "weighting": weighting,
    }


EXPECTED = {
    # value as reported in the Phase 3 / Phase 4 reports, and the rounding used on slides
    "total_eligible": (3_402_692, 0),
    "peak_18": (9_401, 0),
    "peak_08": (5_738, 0),
    "manhattan_pct": (89.4, 1),
    "top15_pct": (50.6, 1),
    "zones_with_pickups": (259, 0),
    "fare_base_pm_pct": (-1.1, 1),
    "fare_charge_pm_pct": (11.2, 1),
    "midtown_weekdays": (21, 0),
    "midtown_am_inbound_days": (21, 0),
    "midtown_pm_outbound_days": (21, 0),
    "strong_cells": (228, 0),
    "strong_persistent": (225, 0),
    "airport_pct": (6.6, 1),
    "below_threshold_pct": (1.4, 1),
    "fitted_pct": (92.0, 1),
    "fitted_zones": (101, 0),
    "rule_k": (2, 0),
    "recommended_k": (5, 0),
    "weight_moved": (20, 0),
    "weight_agree_pct": (72.4, 1),
}
EXPECTED_SEGMENTS = {0: (43.0, 21), 1: (35.7, 23), 2: (8.9, 13), 3: (3.3, 24), 4: (1.1, 20)}


def verify(n: dict) -> list[str]:
    """Check every slide figure against the reported value; returns a log."""
    log = []
    for key, (expected, digits) in EXPECTED.items():
        actual = round(float(n[key]), digits)
        assert actual == expected, f"{key}: data gives {actual}, report says {expected}"
        log.append(f"OK {key} = {actual}")
    for cluster, (share, zones) in EXPECTED_SEGMENTS.items():
        assert round(n["segment_shares"][cluster], 1) == share, cluster
        assert n["segment_zones"][cluster] == zones, cluster
        log.append(f"OK segment {cluster + 1}: {share}% of all pickups, {zones} zones")
    high_volume = round(n["segment_shares"][0] + n["segment_shares"][1], 1)
    assert high_volume == 78.7, high_volume
    log.append(f"OK two high-volume segments = {high_volume}%")
    total = sum(n["segment_shares"].values()) + n["airport_pct"] + n["below_threshold_pct"]
    assert abs(total - 100) < 1e-6, total
    log.append(f"OK shares sum to {total:.4f}%")
    am, pm = n["midtown_avg"][8], n["midtown_avg"][20]
    assert round(am) == -301 and round(pm) == 396, (am, pm)
    log.append(f"OK Midtown Center average net flow 08:00 = {am:.0f}, 20:00 = {pm:+.0f}")
    zones = n["weighting"].set_index("zone")
    for cluster, names in EXAMPLE_ZONES.items():
        for name in names:
            row = zones.loc[name]
            assert row["equal_feature"] == cluster and not row["moved"], (
                f"example {name} is not a stable member of segment {cluster + 1}")
        log.append(f"OK segment {cluster + 1} examples are stable members: {', '.join(names)}")
    return log


# --------------------------------------------------------------------------- helpers

def text(slide, x, y, w, h, runs, size=14, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, name=None, line_spacing=None, space_after=0):
    """Add a text box. ``runs`` is a string, or a list of paragraphs; each paragraph is
    a string or a list of (text, {overrides}) tuples."""
    box = slide.shapes.add_textbox(x, y, w, h)
    if name:
        box.name = name
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = anchor
    paragraphs = [runs] if isinstance(runs, str) else runs
    for i, para in enumerate(paragraphs):
        p = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        p.alignment = align
        if line_spacing:
            p.line_spacing = line_spacing
        p.space_after = Pt(space_after)
        pieces = [(para, {})] if isinstance(para, str) else para
        for piece, opts in pieces:
            run = p.add_run()
            run.text = piece
            font = run.font
            font.name = FONT
            font.size = Pt(opts.get("size", size))
            font.bold = opts.get("bold", bold)
            font.italic = opts.get("italic", False)
            font.color.rgb = opts.get("color", color)
    return box


def panel(slide, x, y, w, h, fill=PANEL, name=None, rounded=True):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
                                   x, y, w, h)
    if rounded:
        shape.adjustments[0] = 0.06
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    if name:
        shape.name = name
    return shape


def badge(slide, x, y, label, fill=YELLOW, color=NAVY, size=Inches(0.42)):
    circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, x, y, size, size)
    circle.fill.solid()
    circle.fill.fore_color.rgb = fill
    circle.line.fill.background()
    circle.shadow.inherit = False
    frame = circle.text_frame
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = label
    run.font.name, run.font.size, run.font.bold, run.font.color.rgb = FONT, Pt(15), True, color
    return circle


def header(slide, kicker, title, dark=False):
    text(slide, MARGIN, Inches(0.42), Inches(12), Inches(0.3), kicker.upper(), size=12, bold=True,
         color=YELLOW if dark else SLATE, name="Kicker")
    text(slide, MARGIN, Inches(0.72), Inches(12.1), Inches(1.0), title, size=28, bold=True,
         color=WHITE if dark else NAVY, name="Title", anchor=MSO_ANCHOR.TOP)


def footer(slide, number, note=SOURCE, dark=False):
    color = ICE if dark else MUTED
    text(slide, MARGIN, Inches(6.98), Inches(11.4), Inches(0.35), note, size=10, color=color,
         name="Source")
    text(slide, Inches(12.233), Inches(6.98), Inches(0.5), Inches(0.35), str(number), size=10,
         color=color, align=PP_ALIGN.RIGHT, name="Slide number")


def background(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def point_label(point, label, position):
    """Bold navy Calibri data label on one chart point."""
    point.data_label.has_text_frame = True
    point.data_label.text_frame.text = label
    point.data_label.text_frame.word_wrap = False
    run = point.data_label.text_frame.paragraphs[0].runs[0]
    run.font.size, run.font.bold, run.font.name = Pt(12), True, FONT
    run.font.color.rgb = NAVY
    point.data_label.position = position
    # White label background so the text stays readable over the grey day lines.
    dlbl = point.data_label._dLbl
    sp_pr = dlbl.makeelement(qn("c:spPr"), {})
    fill = sp_pr.makeelement(qn("a:solidFill"), {})
    fill.append(fill.makeelement(qn("a:srgbClr"), {"val": "FFFFFF"}))
    sp_pr.append(fill)
    anchor = dlbl.find(qn("c:txPr"))
    if anchor is None:
        anchor = dlbl.find(qn("c:dLblPos"))
    anchor.addprevious(sp_pr)


def signed(value: float, digits: int = 1) -> str:
    """Signed percentage with a true minus sign."""
    return f"{value:+.{digits}f}%".replace("-", "\u2212")


def style_axis(axis, size=11, color=SLATE, gridlines=False):
    axis.tick_labels.font.size = Pt(size)
    axis.tick_labels.font.color.rgb = color
    axis.tick_labels.font.name = FONT
    axis.format.line.color.rgb = LINE_GREY
    axis.has_major_gridlines = gridlines
    if gridlines:
        axis.major_gridlines.format.line.color.rgb = GRID
        axis.major_gridlines.format.line.width = Pt(0.75)


# --------------------------------------------------------------------------- slides

def slide_summary(prs, n):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, NAVY)
    header(slide, f"Executive summary · NYC Yellow Taxi, Jan 2025 · For {AUDIENCE}",
           "Taxi activity is concentrated, directional and recurring, which supports "
           "zone- and time-specific planning", dark=True)
    text(slide, MARGIN, Inches(1.95), Inches(12.1), Inches(0.5),
         [[("Business question  ", {"bold": True, "color": YELLOW}),
           ("Where and when do completed Yellow Taxi trips concentrate, and which recurring "
            "patterns could inform operating plans?", {"color": WHITE})]], size=16)

    cards = [
        ("1", f"{n['top15_pct']:.1f}%",
         f"of {n['total_eligible']:,} eligible pickups start in just 15 of "
         f"{n['zones_with_pickups']} zones",
         f"Pickups are concentrated in place and time: {n['manhattan_pct']:.1f}% start in "
         f"Manhattan, and the weekday 18:00 hour averages {n['peak_18']:,.0f} pickups."),
        ("2", f"{n['midtown_am_inbound_days']} of {n['midtown_weekdays']}",
         "weekdays Midtown Center flipped from net inbound (am) to net outbound (pm)",
         f"Directional passenger flows recur: {n['strong_persistent']} of {n['strong_cells']} "
         "strongly imbalanced weekday zone-hours keep the same direction on ≥ 90% of days."),
        ("3", f"{n['segment_shares'][0] + n['segment_shares'][1]:.1f}%",
         "of pickups fall in two high-volume zone segments with opposite morning flows",
         f"Clustering finds {n['recommended_k']} operating profiles plus a separate airport "
         "category; boundaries are indicative and weighting-sensitive."),
    ]
    card_w, gap, top = Inches(3.85), Inches(0.275), Inches(2.75)
    for i, (num, stat, stat_label, detail) in enumerate(cards):
        x = MARGIN + i * (card_w + gap)
        panel(slide, x, top, card_w, Inches(3.15), fill=NAVY_2, name=f"Finding {num} card")
        badge(slide, x + Inches(0.3), top + Inches(0.3), num)
        text(slide, x + Inches(0.3), top + Inches(0.85), card_w - Inches(0.6), Inches(0.75),
             stat, size=40, bold=True, color=WHITE, name=f"Finding {num} stat")
        text(slide, x + Inches(0.3), top + Inches(1.6), card_w - Inches(0.6), Inches(0.7),
             stat_label, size=14, bold=True, color=ICE, name=f"Finding {num} label")
        text(slide, x + Inches(0.3), top + Inches(2.33), card_w - Inches(0.6), Inches(0.8),
             detail, size=12, color=ICE, name=f"Finding {num} detail")

    text(slide, MARGIN, Inches(6.2), Inches(12.1), Inches(0.6),
         [[("What this data can and cannot show  ", {"bold": True, "color": YELLOW}),
           ("These are completed trips. Pickups are not total ride requests, and pickup/drop-off "
            "imbalance is an observed passenger flow, not a measure of available taxis or "
            "driver shortages.", {"color": WHITE})]], size=13)
    footer(slide, 1, dark=True)
    slide.notes_slide.notes_text_frame.text = (
        f"Audience: {AUDIENCE} for NYC Yellow Taxis. "
        "Data: NYC TLC Yellow Taxi trip records for January 2025 (3,475,226 raw records). "
        "Phase 2 flags rather than deletes records; 3,402,692 are eligible pickups "
        "(eligible_demand). Finding 1: Phase 3 zone and borough tables. Finding 2: Phase 3 "
        "imbalance index (P - D)/(P + D) and persistence, recomputed independently in the "
        "Phase 3 checks. 'Recurring' means repeated within January 2025; no forecast was "
        "built or tested. Finding 3: Phase 4 Model A k-means with k = 5 (analyst-selected; the "
        "pre-specified rule selected k = 2). Nothing here measures ride requests, taxi supply "
        "or wait times.")


def slide_demand(prs, n):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, WHITE)
    header(slide, "Demand concentration and fares",
           f"Weekday pickups peak at 18:00, and {n['manhattan_pct']:.1f}% start in Manhattan")

    data = CategoryChartData()
    data.categories = [f"{h:02d}" for h in range(24)]
    data.add_series("Weekday", [round(v) for v in n["weekday_hourly"]])
    data.add_series("Weekend", [round(v) for v in n["weekend_hourly"]])
    frame = slide.shapes.add_chart(XL_CHART_TYPE.LINE, MARGIN, Inches(1.85), Inches(7.9),
                                   Inches(4.75), data)
    frame.name = "Hourly pickups chart"
    chart = frame.chart
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.TOP
    chart.legend.include_in_layout = False
    chart.legend.font.size, chart.legend.font.name = Pt(12), FONT
    chart.legend.font.color.rgb = SLATE
    for series, color, width in [(chart.series[0], NAVY, 3), (chart.series[1], YELLOW, 2.5)]:
        series.format.line.color.rgb = color
        series.format.line.width = Pt(width)
        series.smooth = False
        series.marker.style = None
        from pptx.enum.chart import XL_MARKER_STYLE
        series.marker.style = XL_MARKER_STYLE.NONE
    point = chart.series[0].points[18]
    point.marker.style = XL_MARKER_STYLE.CIRCLE
    point.marker.size = 9
    point.marker.format.fill.solid()
    point.marker.format.fill.fore_color.rgb = NAVY
    point_label(point, f"{n['peak_18']:,.0f} at 18:00", XL_LABEL_POSITION.ABOVE)
    style_axis(chart.category_axis)
    chart.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    chart.category_axis.tick_labels.offset = 50
    style_axis(chart.value_axis, gridlines=True)
    chart.value_axis.tick_labels.number_format = "#,##0"
    chart.value_axis.tick_labels.number_format_is_linked = False
    chart.value_axis.maximum_scale = 10000
    chart.value_axis.minimum_scale = 0
    chart.value_axis.has_title = True
    chart.value_axis.axis_title.text_frame.text = "Average completed pickups per day"
    title_font = chart.value_axis.axis_title.text_frame.paragraphs[0].runs[0].font
    title_font.size, title_font.color.rgb, title_font.bold, title_font.name = Pt(11), SLATE, False, FONT
    chart.category_axis.has_title = True
    chart.category_axis.axis_title.text_frame.text = "Pickup hour"
    title_font = chart.category_axis.axis_title.text_frame.paragraphs[0].runs[0].font
    title_font.size, title_font.color.rgb, title_font.bold, title_font.name = Pt(11), SLATE, False, FONT

    right = Inches(8.85)
    col_w = Inches(3.88)
    text(slide, right, Inches(1.95), col_w, Inches(0.7), f"{n['manhattan_pct']:.1f}%", size=40,
         bold=True, color=NAVY, name="Manhattan stat")
    text(slide, right, Inches(2.7), col_w, Inches(0.5),
         "of eligible pickups start in Manhattan", size=14, color=SLATE)
    text(slide, right, Inches(3.3), col_w, Inches(0.7), f"{n['top15_pct']:.1f}%", size=40,
         bold=True, color=NAVY, name="Top zones stat")
    text(slide, right, Inches(4.05), col_w, Inches(0.5),
         f"start in the top 15 of {n['zones_with_pickups']} pickup zones", size=14, color=SLATE)

    panel(slide, right, Inches(4.6), col_w, Inches(2.05), fill=YELLOW_SOFT, name="Fare insight")
    text(slide, right + Inches(0.22), Inches(4.75), col_w - Inches(0.44), Inches(0.5),
         "Fares in the pm rush vs midday, at the same trip distance and duration", size=12,
         bold=True, color=NAVY)
    text(slide, right + Inches(0.22), Inches(5.25), col_w - Inches(0.44), Inches(0.85),
         [[(signed(n["fare_base_pm_pct"]), {"size": 24, "bold": True, "color": NAVY}),
           ("  metered base fare", {"size": 13, "color": SLATE})],
          [(signed(n["fare_charge_pm_pct"]), {"size": 24, "bold": True, "color": NAVY}),
           ("  total charge excl. tips", {"size": 13, "color": SLATE})]], size=13)
    text(slide, right + Inches(0.22), Inches(6.15), col_w - Inches(0.44), Inches(0.42),
         "Consistent with the scheduled $2.50 rush-hour surcharge; not evidence of surge pricing.",
         size=11, color=SLATE)

    footer(slide, 2, SOURCE + " Weekday = 21 non-holiday weekdays; weekend = 8 days. "
           "Fares: weekday standard-rate metered trips; Flex Fare excluded.")
    slide.notes_slide.notes_text_frame.text = (
        "Hourly chart: eligible pickups divided by the number of calendar days of each type "
        "(21 weekdays excluding 1 and 20 January holidays; 8 weekend days). Weekday 18:00 averages "
        f"{n['peak_18']:,.0f} pickups per day; the morning peak is {n['peak_08']:,.0f} at 08:00. "
        f"Concentration: {n['manhattan_pct']:.1f}% of {n['total_eligible']:,} eligible pickups start "
        f"in Manhattan; the top 15 of {n['zones_with_pickups']} zones account for "
        f"{n['top15_pct']:.1f}%. Fare comparison: weekday standard-rate (RatecodeID 1) metered "
        "trips, means direct-standardised to a common distance x duration mix (24 strata with "
        ">= 30 trips in every period, 97.2% of trips). Base fare = fare_amount; total charge = "
        "total_amount - tip_amount, which includes the documented $2.50 weekday 16:00-20:00 "
        "surcharge. Robustness range across 12 specifications: base -1.5% to -1.0%, total "
        "+10.6% to +12.0%. Completed pickups are not ride requests.")


def slide_flows(prs, n):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, WHITE)
    header(slide, "Recurring directional flows · Midtown Center",
           "Midtown Center receives more trips than it sends every weekday morning, "
           "and the reverse every evening")

    daily = n["midtown_daily"]
    data = CategoryChartData()
    data.categories = [f"{h:02d}" for h in range(24)]
    for date, row in daily.iterrows():
        data.add_series(f"{pd.Timestamp(date):%d %b}", [int(v) for v in row.to_numpy()])
    data.add_series("21-weekday average", [round(v) for v in n["midtown_avg"]])
    frame = slide.shapes.add_chart(XL_CHART_TYPE.LINE, MARGIN, Inches(1.85), Inches(8.2),
                                   Inches(4.75), data)
    frame.name = "Midtown net flow chart"
    chart = frame.chart
    chart.has_legend = False
    from pptx.enum.chart import XL_MARKER_STYLE
    for i, series in enumerate(chart.series):
        last = i == len(chart.series) - 1
        series.smooth = False
        series.marker.style = XL_MARKER_STYLE.NONE
        series.format.line.color.rgb = NAVY if last else LINE_GREY
        series.format.line.width = Pt(3.25 if last else 1)
    avg = chart.series[-1]
    for hour, label, position in [
            (8, f"{n['midtown_avg'][8]:+.0f} at 08:00".replace("-", "\u2212"),
             XL_LABEL_POSITION.BELOW),
            (20, f"{n['midtown_avg'][20]:+.0f} at 20:00", XL_LABEL_POSITION.ABOVE)]:
        point = avg.points[hour]
        point.marker.style = XL_MARKER_STYLE.CIRCLE
        point.marker.size = 9
        point.marker.format.fill.solid()
        point.marker.format.fill.fore_color.rgb = NAVY
        point_label(point, label, position)
    style_axis(chart.category_axis)
    chart.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    style_axis(chart.value_axis, gridlines=True)
    chart.value_axis.minimum_scale, chart.value_axis.maximum_scale = -600, 800
    chart.value_axis.major_unit = 200
    chart.value_axis.tick_labels.number_format = "+#,##0;\u2212#,##0;0"
    chart.value_axis.tick_labels.number_format_is_linked = False
    chart.value_axis.has_title = True
    chart.value_axis.axis_title.text_frame.text = "Net flow per hour (pickups − drop-offs)"
    title_font = chart.value_axis.axis_title.text_frame.paragraphs[0].runs[0].font
    title_font.size, title_font.color.rgb, title_font.bold, title_font.name = Pt(11), SLATE, False, FONT
    chart.category_axis.has_title = True
    chart.category_axis.axis_title.text_frame.text = (
        "Hour of day · grey lines = each of the 21 non-holiday weekdays · navy = average")
    title_font = chart.category_axis.axis_title.text_frame.paragraphs[0].runs[0].font
    title_font.size, title_font.color.rgb, title_font.bold, title_font.name = Pt(11), SLATE, False, FONT

    right, col_w = Inches(9.15), Inches(3.58)
    rows = [
        (f"{n['midtown_am_inbound_days']} / {n['midtown_weekdays']}",
         "weekdays net inbound in each hour from 07:00 to 09:59"),
        (f"{n['midtown_pm_outbound_days']} / {n['midtown_weekdays']}",
         "weekdays net outbound in each hour from 17:00 to 21:59"),
        (f"{n['strong_persistent']} / {n['strong_cells']}",
         "strongly imbalanced weekday zone-hours citywide keep their direction on ≥ 90% of days"),
    ]
    y = Inches(1.95)
    for stat, label in rows:
        text(slide, right, y, col_w, Inches(0.6), stat, size=32, bold=True, color=NAVY)
        text(slide, right, y + Inches(0.62), col_w, Inches(0.65), label, size=13, color=SLATE)
        y += Inches(1.2)
    text(slide, right, y - Inches(0.1), col_w, Inches(0.55),
         [[("Caveat  ", {"bold": True, "color": INK}),
           ("Cells were selected for strong imbalance before persistence was measured, "
            "so high persistence is partly expected.", {"color": SLATE})]], size=11,
         name="Selection caveat")
    panel(slide, right, Inches(6.08), col_w, Inches(0.74), fill=PANEL, name="Definition")
    text(slide, right + Inches(0.18), Inches(6.13), col_w - Inches(0.36), Inches(0.64),
         "Net flow = pickups minus drop-offs of completed trips: a passenger-flow pattern, "
         "not a count of available taxis or unmet demand.", size=11, color=INK,
         anchor=MSO_ANCHOR.MIDDLE)
    footer(slide, 3, SOURCE + " Midtown Center = TLC zone 161; pickups use eligible_demand, "
           "drop-offs are validated separately (eligible_dropoff).")
    slide.notes_slide.notes_text_frame.text = (
        "Each grey line is one non-holiday weekday in January 2025 (21 days); the navy line is "
        f"the average. Average net flow is {n['midtown_avg'][8]:+.0f} trips at 08:00 and "
        f"{n['midtown_avg'][20]:+.0f} at 20:00. Net flow was negative at every hour 07:00-09:59 on "
        "all 21 weekdays and positive at every hour 17:00-21:59 on all 21 weekdays. Citywide, "
        f"{n['strong_persistent']} of {n['strong_cells']} weekday zone-hours with |imbalance index| "
        ">= 0.3 and >= 50 trip ends per day kept the majority sign on >= 90% of qualifying days "
        "(robust to stricter thresholds in the Phase 3 sensitivity checks). These cells were "
        "selected for strong imbalance, so high persistence is partly expected. Net flow "
        "describes completed passenger trips only; it does not measure taxi availability, "
        "driver shortages or unmet requests.")


SEGMENT_STYLE = [
    ("High volume, morning outflow", NAVY, "Penn Station, Upper West Side"),
    ("High volume, morning inflow", STEEL, "Midtown, Times Square"),
    ("Weekend and late night", YELLOW, "Lower East Side, Williamsburg"),
    ("Mid volume, evening inflow", SKY, "Central Harlem, Long Island City"),
    ("Low volume, morning-heavy", ICE, "East New York, Astoria"),
]
# TLC zone names behind each example; verify() checks that every one belongs to its
# segment and keeps it under the alternative feature weighting.
EXAMPLE_ZONES = {
    0: ["Penn Station/Madison Sq West", "Upper West Side South"],
    1: ["Midtown Center", "Times Sq/Theatre District"],
    2: ["Lower East Side", "Williamsburg (South Side)"],
    3: ["Central Harlem", "Long Island City/Hunters Point"],
    4: ["East New York", "Astoria"],
}


def slide_segments(prs, n):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, WHITE)
    header(slide, "Operational zone segmentation",
           f"Two high-volume zone segments with opposite morning flows carry "
           f"{n['segment_shares'][0] + n['segment_shares'][1]:.1f}% of pickups")

    labels = [name for name, _, _ in SEGMENT_STYLE] + ["Airports (JFK, LaGuardia)",
                                                       "Zones below 1,000 pickups"]
    values = [round(n["segment_shares"][c], 1) for c in range(5)] + [
        round(n["airport_pct"], 1), round(n["below_threshold_pct"], 1)]
    data = CategoryChartData()
    data.categories = list(reversed(labels))
    data.add_series("Share of all eligible pickups (%)", list(reversed(values)))
    text(slide, MARGIN, Inches(1.9), Inches(6.9), Inches(0.35),
         f"Share of all {n['total_eligible']:,} eligible pickups", size=13, bold=True, color=NAVY)
    frame = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, MARGIN, Inches(2.25), Inches(6.9),
                                   Inches(3.95), data)
    frame.name = "Segment share chart"
    chart = frame.chart
    chart.has_legend = False
    chart.has_title = False
    plot = chart.plots[0]
    plot.gap_width = 45
    plot.has_data_labels = True
    labels_fmt = plot.data_labels
    labels_fmt.number_format = '0.0"%"'
    labels_fmt.number_format_is_linked = False
    labels_fmt.position = XL_LABEL_POSITION.OUTSIDE_END
    labels_fmt.font.size, labels_fmt.font.bold, labels_fmt.font.name = Pt(12), True, FONT
    labels_fmt.font.color.rgb = INK
    colors = [color for _, color, _ in SEGMENT_STYLE] + [MUTED, GRID]
    for i, color in enumerate(reversed(colors)):
        point = chart.series[0].points[i]
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = color
        point.format.line.fill.background()
    style_axis(chart.category_axis, size=12, color=INK)
    style_axis(chart.value_axis, gridlines=False)
    chart.value_axis.visible = False
    chart.value_axis.minimum_scale, chart.value_axis.maximum_scale = 0, 52

    right, col_w = Inches(7.85), Inches(4.88)
    text(slide, right, Inches(1.9), col_w, Inches(0.35), "What defines each segment (medians)",
         size=13, bold=True, color=NAVY)
    traits = [
        "Busiest zones; more pickups than drop-offs 06–09",
        "More drop-offs than pickups 06–09; 35% of pickups 16–19",
        "Weekend rate 2.7× weekday; 46% of weekend pickups 00–03",
        "More drop-offs than pickups in the evening; ~4 mi trips",
        "41% of weekday pickups 06–09; ~25 min trips",
    ]
    y = Inches(2.3)
    for c, ((name, color, examples), trait) in enumerate(zip(SEGMENT_STYLE, traits)):
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, right, y + Inches(0.06), Inches(0.18), Inches(0.18))
        dot.fill.solid()
        dot.fill.fore_color.rgb = color
        dot.line.color.rgb = LINE_GREY if color in (ICE, YELLOW) else color
        dot.shadow.inherit = False
        text(slide, right + Inches(0.3), y, col_w - Inches(0.3), Inches(0.74),
             [[(f"{name}", {"bold": True, "color": INK}),
               (f"  ·  {n['segment_zones'][c]} zones", {"color": MUTED, "size": 12})],
              [(trait, {"color": SLATE, "size": 12})],
              [(f"e.g. {examples}", {"color": MUTED, "size": 11})]], size=13)
        y += Inches(0.78)

    panel(slide, MARGIN, Inches(6.3), Inches(12.13), Inches(0.58), fill=PANEL, name="Disclosure")
    text(slide, MARGIN + Inches(0.2), Inches(6.36), Inches(11.75), Inches(0.48),
         [[("How to read this  ", {"bold": True, "color": NAVY}),
           (f"{n['recommended_k']} k-means segments (Model A, analyst-selected; the pre-specified "
            f"rule chose k = {n['rule_k']}) plus a separately defined airport category, not six "
            f"clusters. Membership is sensitive to feature weighting ({n['weight_moved']} of "
            f"{n['fitted_zones']} zones move; {n['weight_agree_pct']:.1f}% of pickups stay); "
            "the weekend/late-night segment is the most sensitive.", {"color": INK})]], size=12,
         anchor=MSO_ANCHOR.MIDDLE)
    footer(slide, 4, SOURCE + f" Shares are of all {n['total_eligible']:,} eligible pickups. "
           f"Fitted: {n['fitted_zones']} zones with ≥ 1,000 pickups ({n['fitted_pct']:.1f}%).")
    slide.notes_slide.notes_text_frame.text = (
        f"Model A (operational behaviour) k-means on {n['fitted_zones']} pickup zones with >= 1,000 "
        "eligible pickups, airports held out. Features: log daily pickups, log weekend/weekday "
        "rate, weekday pickup shares at 00-05, 06-09, 16-19 and 20-23, and weekday imbalance at "
        "06-09 and 16-19, standardised with equal weight per feature. KMeans n_init = 20, "
        "random_state = 20250101. Selection: the pre-specified rule (gates, then silhouette) "
        f"selected k = {n['rule_k']} (a high-volume core vs a low-volume remainder); k = 5 is an "
        "analyst-selected operational segmentation with a lower silhouette (0.279 vs 0.334). It "
        "passes all stability gates (day-resampling ARI 0.88, seed ARI 0.95) but is not "
        "statistically optimal and has not been validated against business outcomes. Under equal "
        f"weight per feature group, {n['weight_moved']} of {n['fitted_zones']} zones change segment "
        f"({n['weight_agree_pct']:.1f}% pickup-weighted agreement); the two high-volume segments keep "
        "their opposite morning flows, and the weekend segment shrinks from 13 zones to 7. "
        f"Shares use all {n['total_eligible']:,} eligible pickups: segments "
        + ", ".join(f"{n['segment_shares'][c]:.1f}%" for c in range(5))
        + f"; airports {n['airport_pct']:.1f}%; zones below threshold {n['below_threshold_pct']:.1f}%.")


def slide_recommendations(prs, n):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, WHITE)
    header(slide, "Recommendations for fleet operations and dispatch planning",
           "Three low-risk next steps, each to be validated before claiming operational impact")
    recs = [
        ("1", "Investigate repositioning in recurring imbalance corridors",
         f"Midtown Center flows reverse on {n['midtown_am_inbound_days']} of "
         f"{n['midtown_weekdays']} weekdays; {n['strong_persistent']} of {n['strong_cells']} strong "
         "zone-hours repeat their direction.",
         "Pilot study of where vacant taxis are and could be repositioned in the strongest "
         "morning and evening corridors.",
         "Vacant-taxi locations, idle time and request or wait-time data, compared between "
         "pilot and control zones and hours."),
        ("2", "Plan with time- and zone-specific operating profiles",
         f"Weekday pickups peak at 18:00 ({n['peak_18']:,.0f} per day); two segments with opposite "
         f"morning flows carry {n['segment_shares'][0] + n['segment_shares'][1]:.1f}% of pickups.",
         "Use the five segments plus airports as a starting frame for staffing, staging and "
         "communication calendars.",
         "Re-run on further months; track segment stability and forecast error by segment."),
        ("3", "Separate metered and Flex Fare reporting",
         "Flex Fare grew from about 4% to about 20% of daily pickups in January, shifting "
         "the metered fare sample.",
         "Report metered and Flex Fare activity separately, and show sample composition next to "
         "every fare trend.",
         "Monthly Flex share by zone and hour, coverage of the fare sample by zone, and "
         "documented Flex fare fields."),
    ]
    col_w, gap, top = Inches(3.88), Inches(0.245), Inches(1.95)
    for i, (num, title, evidence, action, validation) in enumerate(recs):
        x = MARGIN + i * (col_w + gap)
        panel(slide, x, top, col_w, Inches(4.75), fill=PANEL, name=f"Recommendation {num}")
        badge(slide, x + Inches(0.25), top + Inches(0.25), num, fill=NAVY, color=WHITE)
        text(slide, x + Inches(0.8), top + Inches(0.2), col_w - Inches(1.0), Inches(0.75), title,
             size=15, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
        y = top + Inches(1.15)
        for label, body in [("Evidence (observed)", evidence), ("Proposed action", action),
                            ("Validate with", validation)]:
            text(slide, x + Inches(0.25), y, col_w - Inches(0.5), Inches(0.3), label.upper(),
                 size=10, bold=True, color=MUTED)
            text(slide, x + Inches(0.25), y + Inches(0.28), col_w - Inches(0.5), Inches(0.85), body,
                 size=12, color=INK)
            y += Inches(1.17)
    footer(slide, 5, "No reduction in waiting times, revenue gain or driver-utilisation "
           "improvement is claimed; each action requires the validation data listed. " + SOURCE)
    slide.notes_slide.notes_text_frame.text = (
        "Evidence comes from Phases 3-4: Midtown Center daily net flow (21 non-holiday weekdays), "
        f"persistence of {n['strong_persistent']} of {n['strong_cells']} strongly imbalanced zone-hours, "
        f"weekday 18:00 average {n['peak_18']:,.0f} pickups, Model A segments "
        f"({n['segment_shares'][0] + n['segment_shares'][1]:.1f}% in the two high-volume segments), "
        "and the Flex Fare share rising from about 4% (2-6 January) to about 20% later in the month. "
        "Repositioning is a hypothesis: drop-off counts do not show where vacant taxis are, and "
        "imbalance does not measure driver shortages. Segments are analyst-selected (k = 5) and "
        "weighting-sensitive. No outcome data (wait times, revenue, utilisation) was available, so "
        "no impact is estimated.")


def build(output_path: Path = OUTPUT_PATH) -> tuple[Path, list[str]]:
    numbers = load_numbers()
    log = verify(numbers)
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    prs.core_properties.title = "NYC Yellow Taxi trips, January 2025: executive summary"
    prs.core_properties.subject = "NYC TLC Yellow Taxi trip records, January 2025"
    # Replace python-pptx template defaults (author "Steve Canny", dates in 2013).
    props = prs.core_properties
    props.author = props.last_modified_by = "Delphino"
    props.created = props.modified = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    props.revision = 1
    props.comments = ("Five-slide executive summary for " + AUDIENCE + ". Built by "
                         "src/build_deck.py from the project outputs.")
    props.keywords = "NYC TLC; Yellow Taxi; trip records; k-means; executive summary"
    props.category = "Portfolio project"
    for build_slide in [slide_summary, slide_demand, slide_flows, slide_segments,
                        slide_recommendations]:
        build_slide(prs, numbers)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output_path)
    return output_path, log


def main() -> int:
    path, log = build()
    print("\n".join(log))
    print(f"Wrote {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
