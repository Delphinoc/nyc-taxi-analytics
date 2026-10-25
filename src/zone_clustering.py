"""K-means segmentation of NYC pickup zones (Phase 4).

Reads the zone base tables from src/zone_features.py and writes model inputs,
selection metrics, stability and sensitivity tables, and cluster assignments
to data/processed/zones/. Design, selection rule and limitations are in
docs/clustering_design.md.

Usage:
    python src/zone_clustering.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import (
    adjusted_rand_score,
    calinski_harabasz_score,
    davies_bouldin_score,
    silhouette_score,
)
from sklearn.preprocessing import StandardScaler
from scipy.optimize import linear_sum_assignment

import pyarrow.parquet as pq

from eda_aggregates import FLAGGED_PATH, read_zones
from zone_features import ZONES_DIR, compute_features, load_base_tables

SEED = 20250101
N_INIT = 20
K_RANGE = range(2, 9)
N_SEEDS = 20
N_BOOTSTRAP = 30
MIN_PICKUPS = 1_000
AIRPORT_IDS = (132, 138)  # JFK and LaGuardia; Newark has < 1,000 pickups

# Pre-registered selection rule (applied before looking at cluster profiles):
# a k is eligible if its smallest cluster has >= 5 zones, the median bootstrap
# ARI is >= 0.60 and the mean seed ARI is >= 0.90; among eligible k, pick the
# highest silhouette, preferring the smaller k when silhouettes are within 0.02.
MIN_CLUSTER_ZONES = 5
# The pre-specified rule above is the statistical selection (k = 2 for both
# models: a high-volume core vs a low-volume remainder). RECOMMENDED_K is an
# analyst-selected operational segmentation, not a statistical optimum: chosen
# from the selection metrics before reading profiles, required to pass every
# gate, and reported alongside the rule result. See docs/clustering_design.md.
RECOMMENDED_K = {"A": 5, "B": 3}
MIN_BOOTSTRAP_ARI = 0.60
MIN_SEED_ARI = 0.90
SILHOUETTE_TIE = 0.02

# Feature groups per model. Transformations are applied in model_inputs().
MODELS = {
    "A": {
        "volume": ["log10_avg_daily_pickups"],
        "week": ["log2_weekend_to_weekday_ratio"],
        "timing": ["weekday_share_overnight", "weekday_share_morning",
                   "weekday_share_pm_rush", "weekday_share_evening"],
        "direction": ["imbalance_morning", "imbalance_pm_rush"],
    },
    "B": {
        "timing": ["weekday_share_overnight", "weekday_share_morning",
                   "weekday_share_pm_rush", "weekday_share_evening"],
        "trip": ["log2_median_distance_mi", "median_duration_min"],
        "fare": ["median_fare_per_mile"],
    },
}
VARIANT_FEATURES = {
    # Model A plus the weekday persistence of the morning and pm-rush net-flow sign.
    "A_plus_persistence": {**MODELS["A"], "persistence": ["persistence_morning", "persistence_pm_rush"]},
    "A_without_volume": {k: v for k, v in MODELS["A"].items() if k != "volume"},
    "B_without_fare": {k: v for k, v in MODELS["B"].items() if k != "fare"},
}


# --------------------------------------------------------------------------- inputs

def add_transforms(features: pd.DataFrame) -> pd.DataFrame:
    """Derived columns used by the models (log scales for skewed ratios)."""
    out = features.copy()
    for source, target in [("weekend_to_weekday_ratio", "log2_weekend_to_weekday_ratio"),
                           ("median_distance_mi", "log2_median_distance_mi")]:
        if source in out:
            out[target] = np.log2(out[source])
    return out


def population(features: pd.DataFrame, min_pickups: int = MIN_PICKUPS,
               include_airports: bool = False) -> pd.DataFrame:
    keep = features["pickups"] >= min_pickups
    if not include_airports:
        keep &= ~features.index.isin(AIRPORT_IDS)
    return features[keep]


def model_inputs(features: pd.DataFrame, groups: dict, weighting: str = "equal_feature"
                 ) -> tuple[np.ndarray, list[str], pd.Series]:
    """Standardised matrix for clustering.

    Missing values are filled with the population median (counts reported).
    ``equal_feature`` gives every feature unit variance; ``equal_group`` scales
    each feature by 1/sqrt(group size) so every group carries the same total
    weight in the squared distance.
    """
    columns = [c for group in groups.values() for c in group]
    frame = add_transforms(features)[columns]
    missing = frame.isna().sum()
    frame = frame.fillna(frame.median())
    matrix = StandardScaler().fit_transform(frame)
    if weighting == "equal_group":
        scale = np.concatenate([[1 / np.sqrt(len(g))] * len(g) for g in groups.values()])
        matrix = matrix * scale
    elif weighting != "equal_feature":
        raise ValueError(weighting)
    return matrix, columns, missing


def fit(matrix: np.ndarray, k: int, seed: int = SEED) -> np.ndarray:
    return KMeans(n_clusters=k, n_init=N_INIT, random_state=seed).fit_predict(matrix)


# --------------------------------------------------------------------------- evaluation

def cluster_sizes(labels: np.ndarray) -> np.ndarray:
    return np.bincount(labels)


def seed_stability(matrix: np.ndarray, k: int, n_seeds: int = N_SEEDS) -> float:
    """Mean ARI between the reference fit and fits with other seeds."""
    reference = fit(matrix, k)
    return float(np.mean([adjusted_rand_score(reference, fit(matrix, k, SEED + i + 1))
                          for i in range(n_seeds)]))


def bootstrap_dates(dates: pd.Series, day_types: pd.Series, rng: np.random.Generator) -> pd.Series:
    """Resample dates with replacement within each day type; returns date weights."""
    drawn = []
    for kind in day_types.unique():
        pool = dates[day_types == kind].to_numpy()
        drawn.append(rng.choice(pool, size=len(pool), replace=True))
    counts = pd.Series(np.concatenate(drawn)).value_counts()
    return counts.reindex(dates.to_numpy(), fill_value=0).astype(float)


def selection_table(matrix: np.ndarray, bootstrap_matrices: list[np.ndarray]) -> pd.DataFrame:
    rows = []
    for k in K_RANGE:
        labels = fit(matrix, k)
        sizes = cluster_sizes(labels)
        boot = [adjusted_rand_score(labels, fit(m, k)) for m in bootstrap_matrices]
        rows.append({
            "k": k,
            "silhouette": silhouette_score(matrix, labels),
            "calinski_harabasz": calinski_harabasz_score(matrix, labels),
            "davies_bouldin": davies_bouldin_score(matrix, labels),
            "inertia": KMeans(n_clusters=k, n_init=N_INIT, random_state=SEED).fit(matrix).inertia_,
            "min_cluster_zones": int(sizes.min()),
            "max_cluster_zones": int(sizes.max()),
            "seed_ari_mean": seed_stability(matrix, k),
            "bootstrap_ari_median": float(np.median(boot)),
            "bootstrap_ari_p10": float(np.quantile(boot, 0.1)),
        })
    return pd.DataFrame(rows)


def choose_k(selection: pd.DataFrame) -> tuple[int | None, pd.DataFrame]:
    """Apply the pre-registered rule; returns (k or None, table with eligibility)."""
    table = selection.assign(
        eligible=(selection["min_cluster_zones"] >= MIN_CLUSTER_ZONES)
        & (selection["bootstrap_ari_median"] >= MIN_BOOTSTRAP_ARI)
        & (selection["seed_ari_mean"] >= MIN_SEED_ARI))
    eligible = table[table["eligible"]]
    if eligible.empty:
        return None, table
    best = eligible["silhouette"].max()
    near_best = eligible[eligible["silhouette"] >= best - SILHOUETTE_TIE]
    return int(near_best["k"].min()), table


def variance_explained(matrix: np.ndarray, labels: np.ndarray) -> float:
    """Share of total squared distance explained by group means (R-squared)."""
    total = ((matrix - matrix.mean(axis=0)) ** 2).sum()
    within = sum(((matrix[labels == g] - matrix[labels == g].mean(axis=0)) ** 2).sum()
                 for g in np.unique(labels))
    return float(1 - within / total)


# --------------------------------------------------------------------------- rule-based baselines

def rule_dominant_period(features: pd.DataFrame) -> pd.Series:
    """Each zone's weekday period with the highest share relative to the citywide share."""
    periods = ["overnight", "morning", "pm_rush", "evening"]
    shares = features[[f"weekday_share_{p}" for p in periods]]
    relative = shares / shares.mean()
    return relative.idxmax(axis=1).str.replace("weekday_share_", "", regex=False)


def rule_flow_pattern(features: pd.DataFrame, threshold: float = 0.1) -> pd.Series:
    """Morning-out/evening-in, morning-in/evening-out, or other (by imbalance sign)."""
    morning, evening = features["imbalance_morning"], features["imbalance_pm_rush"]
    return pd.Series(np.select(
        [(morning > threshold) & (evening < -threshold), (morning < -threshold) & (evening > threshold)],
        ["out AM / in PM", "in AM / out PM"], "mixed or balanced"), index=features.index)


def rule_weekend_then_period(features: pd.DataFrame, weekend_ratio: float = 1.5) -> pd.Series:
    """Five-group rule: weekend-heavy zones first, the rest by dominant weekday period.

    Built after the k = 5 solution was seen (its weekend segment suggested the
    first split), so it is a convenience baseline, not an independent one.
    """
    return rule_dominant_period(features).where(
        features["weekend_to_weekday_ratio"] <= weekend_ratio, "weekend_heavy")


def rule_volume_tercile(features: pd.DataFrame) -> pd.Series:
    return pd.qcut(features["avg_daily_pickups"], 3, labels=["low", "middle", "high"]).astype(str)


def rule_volume_halves(features: pd.DataFrame) -> pd.Series:
    return pd.qcut(features["avg_daily_pickups"], 2, labels=["lower", "upper"]).astype(str)


RULES = {
    "volume_halves": rule_volume_halves,
    "volume_tercile": rule_volume_tercile,
    "flow_pattern": rule_flow_pattern,
    "dominant_period": rule_dominant_period,
    "weekend_then_dominant_period": lambda f: rule_weekend_then_period(f),
}


def rule_comparison(features: pd.DataFrame, matrix: np.ndarray, labels: np.ndarray,
                    odd: tuple[pd.DataFrame, np.ndarray] | None = None,
                    even_matrix: np.ndarray | None = None) -> pd.DataFrame:
    """Rule-based groupings vs k-means with the same number of groups.

    Variance explained always rises with the number of groups, so each rule is
    compared with a k-means fit using exactly as many groups as the rule has.
    k-means maximises in-sample variance explained by construction, so a
    held-out version is also reported: groups are formed from odd-day features
    and variance explained is measured on even-day features.
    """
    rows = []
    for name, rule in RULES.items():
        codes = pd.factorize(rule(features))[0]
        g = int(codes.max() + 1)
        same_k = fit(matrix, g)
        heldout = {}
        if odd is not None and even_matrix is not None:
            odd_features, odd_matrix = odd
            heldout = {
                "heldout_rule_variance_explained": variance_explained(
                    even_matrix, pd.factorize(rule(odd_features))[0]),
                "heldout_kmeans_same_k_variance_explained": variance_explained(
                    even_matrix, fit(odd_matrix, g)),
            }
        rows.append({**heldout,
            "rule": name, "groups": g,
            "rule_variance_explained": variance_explained(matrix, codes),
            "kmeans_same_k_variance_explained": variance_explained(matrix, same_k),
            "rule_silhouette": silhouette_score(matrix, codes),
            "kmeans_same_k_silhouette": silhouette_score(matrix, same_k),
            "ari_rule_vs_solution": adjusted_rand_score(labels, codes),
            "smallest_rule_group": int(np.bincount(codes).min()),
        })
    rows.append({"rule": "k-means solution", "groups": int(labels.max() + 1),
                 "kmeans_same_k_variance_explained": variance_explained(matrix, labels),
                 "kmeans_same_k_silhouette": silhouette_score(matrix, labels)})
    return pd.DataFrame(rows)


def airport_placement(population_features: pd.DataFrame, airports: pd.DataFrame, groups: dict,
                      labels: np.ndarray) -> dict:
    """Assign held-out airports to the nearest centroid, scaled with the population.

    Also reports how far each airport sits from that centroid relative to the
    95th percentile of member distances (> 1 means further than 95% of members).
    """
    columns = [c for group in groups.values() for c in group]
    frame = add_transforms(population_features)[columns]
    fill = frame.median()
    scaler = StandardScaler().fit(frame.fillna(fill))
    members = scaler.transform(frame.fillna(fill))
    extra = scaler.transform(add_transforms(airports)[columns].fillna(fill))
    centroids = np.vstack([members[labels == c].mean(axis=0) for c in np.unique(labels)])
    out = {}
    for zone_id, row in zip(airports.index, extra):
        distances = np.linalg.norm(centroids - row, axis=1)
        nearest = int(distances.argmin())
        member_distance = np.linalg.norm(members[labels == nearest] - centroids[nearest], axis=1)
        out[int(zone_id)] = {"nearest_cluster": nearest,
                             "distance_vs_member_p95": float(distances[nearest]
                                                             / np.quantile(member_distance, 0.95))}
    return out


# --------------------------------------------------------------------------- profiles

PROFILE_COLUMNS = [
    "avg_daily_pickups", "weekend_to_weekday_ratio", "weekday_share_overnight",
    "weekday_share_morning", "weekday_share_midday", "weekday_share_pm_rush",
    "weekday_share_evening", "weekend_late_night_share", "imbalance_morning", "imbalance_pm_rush",
    "persistence_morning", "persistence_pm_rush", "median_distance_mi", "median_duration_min",
    "median_fare_per_mile", "flex_share", "fare_sample_coverage", "metered_vs_all_distance_pct",
]


def profiles(features: pd.DataFrame, labels: pd.Series, all_eligible_pickups: float
             ) -> pd.DataFrame:
    """Cluster medians in original units, plus size and two explicit pickup shares.

    ``pickup_share_of_fitted_pct`` divides by the fitted zones' pickups;
    ``pickup_share_of_all_pct`` divides by all eligible pickups in the month
    (including airports and zones below the threshold).
    """
    frame = features.assign(cluster=labels)
    table = frame.groupby("cluster")[PROFILE_COLUMNS].median()
    cluster_pickups = frame.groupby("cluster")["pickups"].sum()
    table.insert(0, "zones", frame.groupby("cluster").size())
    table.insert(1, "pickups", cluster_pickups)
    table.insert(2, "pickup_share_of_fitted_pct", 100 * cluster_pickups / features["pickups"].sum())
    table.insert(3, "pickup_share_of_all_pct", 100 * cluster_pickups / all_eligible_pickups)
    return table


def match_labels(reference: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Relabel ``other`` to maximise overlap with ``reference`` (Hungarian matching)."""
    table = pd.crosstab(reference, other)
    rows, cols = linear_sum_assignment(-table.to_numpy())
    mapping = {table.columns[c]: table.index[r] for r, c in zip(rows, cols)}
    return np.array([mapping[label] for label in other])


def weighting_comparison(features: pd.DataFrame, groups: dict, k: int,
                         labels: np.ndarray) -> tuple[pd.DataFrame, dict]:
    """Compare equal-feature labels with equal-group labels after label matching."""
    alt_matrix, _, _ = model_inputs(features, groups, "equal_group")
    alternative = match_labels(labels, fit(alt_matrix, k))
    moved = labels != alternative
    zones = features[["zone", "borough", "pickups"]].assign(
        equal_feature=labels, equal_group=alternative, moved=moved)
    summary = {
        "zones_moved": int(moved.sum()),
        "zones_moved_pct": float(100 * moved.mean()),
        "pickup_weighted_agreement_pct": float(
            100 * features.loc[~moved, "pickups"].sum() / features["pickups"].sum()),
        "ari": float(adjusted_rand_score(labels, alternative)),
        "zones_kept_by_cluster": {int(c): int(((labels == c) & ~moved).sum())
                                  for c in np.unique(labels)},
    }
    return zones, summary


def representatives(features: pd.DataFrame, matrix: np.ndarray, labels: np.ndarray,
                    n: int = 5) -> pd.DataFrame:
    """Zones closest to each centroid, and each cluster's largest zones by volume."""
    rows = []
    for cluster in np.unique(labels):
        members = np.where(labels == cluster)[0]
        centroid = matrix[members].mean(axis=0)
        distance = np.linalg.norm(matrix[members] - centroid, axis=1)
        closest = features.iloc[members[np.argsort(distance)[:n]]]
        largest = features.iloc[members].nlargest(n, "pickups")
        rows.append({"cluster": int(cluster),
                     "closest_to_centroid": "; ".join(closest["zone"]),
                     "largest_by_pickups": "; ".join(largest["zone"])})
    return pd.DataFrame(rows)


def order_clusters(labels: np.ndarray, features: pd.DataFrame) -> np.ndarray:
    """Relabel clusters 0..k-1 by descending total pickups, so labels are stable to read."""
    totals = pd.Series(features["pickups"].to_numpy()).groupby(labels).sum()
    mapping = {old: new for new, old in enumerate(totals.sort_values(ascending=False).index)}
    return np.array([mapping[label] for label in labels])


# --------------------------------------------------------------------------- main

def reconcile(clusters: pd.DataFrame, full: pd.DataFrame,
              flagged_path: Path = FLAGGED_PATH) -> dict:
    """Check assignments and pickup totals against the flagged trip file itself.

    Pickups are recounted from ``eligible_demand`` rows, independently of the
    zone base tables, so a bug in feature building would show up here.
    """
    trips = pq.read_table(flagged_path, columns=["PULocationID", "eligible_demand"]).to_pandas()
    source = trips.loc[trips["eligible_demand"], "PULocationID"].value_counts()
    total = int(source.sum())
    checks = {}
    for column in [c for c in clusters.columns if c.startswith("cluster_")]:
        sizes = clusters[column].value_counts().sort_index()
        checks[column] = {
            "zones_assigned": int(clusters[column].notna().sum()),
            "cluster_sizes": {int(c): int(n) for c, n in sizes.items()},
            "sizes_sum": int(sizes.sum()),
            "pickup_share_of_all_pct": {
                int(c): float(100 * source.reindex(clusters.index[clusters[column] == c]).sum() / total)
                for c in sizes.index},
        }
    airports = source.reindex(list(AIRPORT_IDS)).fillna(0)
    fitted = int(source.reindex(clusters.index).sum())
    return {
        "eligible_pickups_source": total,
        "eligible_pickups_feature_tables": int(full["pickups"].sum()),
        "zones_fitted": len(clusters),
        "zone_ids_unique": bool(clusters.index.is_unique),
        "airports_in_fit": bool(clusters.index.isin(AIRPORT_IDS).any()),
        "fitted_pickups": fitted,
        "fitted_share_pct": 100 * fitted / total,
        "zone_pickups_match_source": bool(
            (clusters["pickups"] == source.reindex(clusters.index)).all()),
        "airport_pickups": {int(z): int(n) for z, n in airports.items()},
        "airport_share_pct": float(100 * airports.sum() / total),
        "below_threshold_share_pct": float(
            100 * (total - fitted - airports.sum()) / total),
        "solutions": checks,
    }


def run(output_dir: Path = ZONES_DIR) -> dict:
    started = time.perf_counter()
    base = load_base_tables()
    zones = read_zones()
    full = compute_features(base, zone_names=zones)
    all_pickups = float(full["pickups"].sum())  # every eligible pickup in NYC zones 1-263
    main_pop = population(full)

    counts = base["zone_date_hour_counts"]
    date_types = counts.drop_duplicates("date").set_index("date")["day_type"].sort_index()
    dates = pd.Series(date_types.index)
    rng = np.random.default_rng(SEED)
    boot_features = [compute_features(base, bootstrap_dates(dates, date_types.reset_index(drop=True),
                                                            rng)).reindex(main_pop.index)
                     for _ in range(N_BOOTSTRAP)]
    # Split-half check (held out from selection): odd vs even calendar days.
    day_numbers = pd.Series(dates.dt.day.to_numpy(), index=dates.to_numpy())
    half_features = {
        "odd_days": compute_features(base, (day_numbers % 2 == 1).astype(float)).reindex(main_pop.index),
        "even_days": compute_features(base, (day_numbers % 2 == 0).astype(float)).reindex(main_pop.index),
    }

    results, assignments, selections, stability_rows, rule_tables = {}, [], [], [], []
    with_air = population(full, include_airports=True)
    airports = full.loc[list(AIRPORT_IDS)]
    for model, groups in MODELS.items():
        matrix, columns, missing = model_inputs(main_pop, groups)
        boot_matrices = [model_inputs(b.assign(pickups=main_pop["pickups"]), groups)[0]
                         for b in boot_features]
        selection = selection_table(matrix, boot_matrices)
        rule_k, selection = choose_k(selection)
        selections.append(selection.assign(model=model))
        candidates = sorted({rule_k, RECOMMENDED_K[model]})
        recommended_row = selection[selection["k"] == RECOMMENDED_K[model]].iloc[0]
        if not recommended_row["eligible"]:
            raise ValueError(f"Recommended k for model {model} fails the stability/size gates")
        results[model] = {"features": columns, "rule_k": rule_k,
                          "recommended_k": RECOMMENDED_K[model],
                          "missing_filled": {c: int(v) for c, v in missing.items() if v},
                          "solutions": {}}
        add_transforms(main_pop)[["zone", *columns]].to_csv(
            output_dir / f"model_{model}_inputs.csv", float_format="%.6g", lineterminator="\n")

        for k in candidates:
            labels = order_clusters(fit(matrix, k), main_pop)
            tag = f"{model}_k{k}"

            def ari_on(features: pd.DataFrame, model_groups: dict,
                       weighting: str = "equal_feature") -> float:
                m, _, _ = model_inputs(features, model_groups, weighting)
                other = pd.Series(fit(m, k), index=features.index)
                common = main_pop.index.intersection(features.index)
                return adjusted_rand_score(pd.Series(labels, index=main_pop.index)[common],
                                           other[common])

            halves = [fit(model_inputs(h.assign(pickups=main_pop["pickups"]), groups)[0], k)
                      for h in half_features.values()]
            row = selection[selection["k"] == k].iloc[0]
            sens = {
                "seed_ari_mean": float(row["seed_ari_mean"]),
                "bootstrap_ari_median": float(row["bootstrap_ari_median"]),
                "split_half_odd_days": adjusted_rand_score(labels, halves[0]),
                "split_half_even_days": adjusted_rand_score(labels, halves[1]),
                "equal_group_weighting": ari_on(main_pop, groups, "equal_group"),
                "airports_included_in_fit": ari_on(with_air, groups),
                "threshold_500_pickups": ari_on(population(full, 500), groups),
                "threshold_3000_pickups": ari_on(population(full, 3000), groups),
            }
            for name, variant in VARIANT_FEATURES.items():
                if name.startswith(model):
                    sens[f"variant_{name}"] = ari_on(main_pop, variant)
            stability_rows += [{"model": model, "k": k, "check": c, "ari_vs_main": v}
                               for c, v in sens.items()]

            air = airport_placement(main_pop, airports, groups, labels)
            odd_features = half_features["odd_days"].assign(pickups=main_pop["pickups"],
                                                            zone=main_pop["zone"])
            odd_matrix = model_inputs(odd_features, groups)[0]
            even_matrix = model_inputs(half_features["even_days"].assign(pickups=main_pop["pickups"]),
                                       groups)[0]
            rule_tables.append(rule_comparison(main_pop, matrix, labels, (odd_features, odd_matrix),
                                               even_matrix).assign(model=model, k=k))
            if (model, k) == ("A", RECOMMENDED_K["A"]):
                moves, weight_summary = weighting_comparison(main_pop, groups, k, labels)
                moves.to_csv(output_dir / "model_A_weighting_comparison.csv", lineterminator="\n")
                profiles(main_pop, pd.Series(moves["equal_group"].to_numpy(), index=main_pop.index),
                         all_pickups).to_csv(output_dir / f"model_{tag}_equal_group_profiles.csv",
                                             float_format="%.6g", lineterminator="\n")
                results[model]["weighting_comparison"] = weight_summary
            profiles(main_pop, pd.Series(labels, index=main_pop.index), all_pickups).to_csv(
                output_dir / f"model_{tag}_profiles.csv", float_format="%.6g", lineterminator="\n")
            representatives(main_pop, matrix, labels).to_csv(
                output_dir / f"model_{tag}_representatives.csv", index=False, lineterminator="\n")
            pd.DataFrame(matrix, columns=columns).groupby(labels).mean().to_csv(
                output_dir / f"model_{tag}_centroids_z.csv", float_format="%.4g", lineterminator="\n")
            assignments.append(pd.DataFrame({f"cluster_{tag}": labels}, index=main_pop.index))
            results[model]["solutions"][k] = {
                "cluster_zones": cluster_sizes(labels).tolist(),
                "sensitivity_ari": sens,
                "airport_placement": air,
            }

    pd.concat(selections).to_csv(output_dir / "model_selection.csv", index=False,
                                 float_format="%.4g", lineterminator="\n")
    pd.DataFrame(stability_rows).to_csv(output_dir / "model_sensitivity.csv", index=False,
                                        float_format="%.4g", lineterminator="\n")
    pd.concat(rule_tables).to_csv(output_dir / "rule_comparison.csv", index=False,
                                  float_format="%.4g", lineterminator="\n")
    clusters = main_pop[["zone", "borough", "pickups"]].join(pd.concat(assignments, axis=1))
    clusters.to_csv(output_dir / "zone_clusters.csv", lineterminator="\n")
    full.to_csv(output_dir / "zone_features_all.csv", float_format="%.6g", lineterminator="\n")

    results["reconciliation"] = reconcile(clusters, full)
    excluded = full[~full.index.isin(main_pop.index)]
    results["population"] = {
        "zones_total": len(full),
        "zones_clustered": len(main_pop),
        "pickup_coverage_pct": float(100 * main_pop["pickups"].sum() / full["pickups"].sum()),
        "airports_held_out": list(AIRPORT_IDS),
        "excluded_zones_below_threshold": int((full["pickups"] < MIN_PICKUPS).sum()),
        "excluded_pickups_below_threshold": int(full.loc[full["pickups"] < MIN_PICKUPS, "pickups"].sum()),
        "excluded_borough_counts": excluded[excluded["pickups"] < MIN_PICKUPS]["borough"]
        .value_counts().to_dict(),
    }
    (output_dir / "clustering_summary.json").write_text(
        json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    # Runtime is returned but kept out of the file so reruns are byte-identical.
    results["seconds"] = round(time.perf_counter() - started, 1)
    return results


def main() -> int:
    results = run()
    print(json.dumps({m: results[m] for m in MODELS}, indent=2, default=str))
    print(json.dumps(results["population"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
