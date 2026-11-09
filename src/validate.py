"""Validate the final dataset globally, per platform, and per category."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

import pandas as pd

from src.common import CATEGORIES, creator_tier, load_settings, now_utc, resolve_path, setup_logging

LOG = logging.getLogger("validate")

NUMERIC_COLUMNS = ["views", "likes", "comments", "shares", "saves", "follower_count", "duration_sec"]
CORE_COLUMNS = ["video_id", "post_timestamp", "caption", "likes", "follower_count"]
PLATFORMS = ["tiktok", "instagram"]


def safe_number(value: Any) -> float | int | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def describe(series: pd.Series) -> dict[str, Any]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return {"min": None, "median": None, "max": None, "count": 0}
    return {
        "min": safe_number(numeric.min()),
        "median": safe_number(numeric.median()),
        "max": safe_number(numeric.max()),
        "count": int(numeric.count()),
    }


def weekly_distribution(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or df["post_timestamp"].notna().sum() == 0:
        return {}
    series = df.set_index("post_timestamp").resample("W").size()
    return {index.date().isoformat(): int(value) for index, value in series.items()}


def top_creators(df: pd.DataFrame, limit: int = 5) -> list[dict[str, Any]]:
    creators = df["creator_id_hash"].dropna().astype(str)
    counts = creators.value_counts()
    return [
        {
            "creator_id_hash": str(key),
            "rows": int(value),
            "share": round(float(value) / max(1, len(df)), 4),
        }
        for key, value in counts.head(limit).items()
    ]


def creator_tier_composition(df: pd.DataFrame, settings: dict[str, Any]) -> dict[str, Any]:
    tiers_cfg = settings.get("creator_tiers", {})
    tiers = df["follower_count"].map(lambda value: creator_tier(value, tiers_cfg))
    counts = tiers.value_counts().to_dict()
    total = max(1, len(df))
    return {
        "counts": {str(key): int(value) for key, value in counts.items()},
        "share": {str(key): round(float(value) / total, 4) for key, value in counts.items()},
        "target": settings.get("composition_target", {}),
    }


def anomalies(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "duplicate_platform_video_id": int(df.duplicated(subset=["platform", "video_id"]).sum()),
        "negative_values": {
            column: int((pd.to_numeric(df[column], errors="coerce") < 0).sum()) for column in NUMERIC_COLUMNS
        },
        "duration_zero": int((pd.to_numeric(df["duration_sec"], errors="coerce") == 0).sum()),
        "likes_gt_views": int(
            (
                (pd.to_numeric(df["views"], errors="coerce") > 0)
                & (pd.to_numeric(df["likes"], errors="coerce") > pd.to_numeric(df["views"], errors="coerce"))
            ).sum()
        ),
        "future_post_timestamp": int((df["post_timestamp"] > now_utc()).sum()),
        "post_timestamp_missing": int(df["post_timestamp"].isna().sum()),
    }


def analyze_group(df: pd.DataFrame, settings: dict[str, Any]) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "by_platform": {},
            "null_pct": {column: 1.0 for column in df.columns},
            "core_null_pct": {column: 1.0 for column in CORE_COLUMNS},
            "post_timestamp_range": {"min": None, "max": None},
            "weekly_distribution": {},
            "descriptive": {column: {"min": None, "median": None, "max": None, "count": 0} for column in NUMERIC_COLUMNS},
            "unique_creators": 0,
            "top_creators": [],
            "creator_tiers": creator_tier_composition(df, settings),
            "anomalies": {},
        }

    null_pct = {column: round(float(df[column].isna().mean()), 4) for column in df.columns}
    return {
        "rows": int(len(df)),
        "by_platform": {str(key): int(value) for key, value in df["platform"].value_counts().items()},
        "null_pct": null_pct,
        "core_null_pct": {column: null_pct.get(column, 1.0) for column in CORE_COLUMNS},
        "post_timestamp_range": {
            "min": df["post_timestamp"].min().isoformat() if df["post_timestamp"].notna().any() else None,
            "max": df["post_timestamp"].max().isoformat() if df["post_timestamp"].notna().any() else None,
        },
        "weekly_distribution": weekly_distribution(df),
        "descriptive": {column: describe(df[column]) for column in NUMERIC_COLUMNS},
        "unique_creators": int(df["creator_id_hash"].dropna().nunique()),
        "top_creators": top_creators(df),
        "creator_tiers": creator_tier_composition(df, settings),
        "anomalies": anomalies(df),
    }


def category_quota_report(df: pd.DataFrame, settings: dict[str, Any]) -> dict[str, Any]:
    targets = settings.get("category_targets", {})
    report: dict[str, Any] = {}
    for category in CATEGORIES:
        subset = df[df["category"] == category]
        by_platform = {str(key): int(value) for key, value in subset["platform"].value_counts().items()}
        quota = targets.get(category, {})
        checks: dict[str, Any] = {}
        for platform in PLATFORMS:
            rows = by_platform.get(platform, 0)
            minimum = int(quota.get(f"{platform}_min", 0) or 0)
            maximum = int(quota.get(f"{platform}_max", 0) or 0)
            checks[platform] = {
                "rows": rows,
                "min": minimum,
                "target": int(quota.get(f"{platform}_target", 0) or 0),
                "max": maximum,
                "meets_minimum": rows >= minimum,
                "within_max": rows <= maximum if maximum else True,
            }
        report[category] = {
            "rows": int(len(subset)),
            "by_platform": by_platform,
            "quota_checks": checks,
            "analysis": analyze_group(subset, settings),
        }
    return report


def platform_report(df: pd.DataFrame, settings: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for platform in PLATFORMS:
        subset = df[df["platform"] == platform]
        result[platform] = analyze_group(subset, settings)
    return result


def category_shares(df: pd.DataFrame) -> dict[str, Any]:
    shares: dict[str, Any] = {}
    for platform in PLATFORMS:
        subset = df[df["platform"] == platform]
        total = len(subset)
        per_category = {}
        for category in CATEGORIES:
            rows = int((subset["category"] == category).sum())
            per_category[category] = {
                "rows": rows,
                "share": round(rows / total, 4) if total else 0.0,
            }
        shares[platform] = {"total": total, "categories": per_category}
    return shares


def validate(
    df: pd.DataFrame,
    merge_report: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    for column in ("post_timestamp", "scrape_timestamp"):
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], utc=True, errors="coerce")
    if "category" not in df.columns:
        df["category"] = None
    df["category"] = df["category"].astype("string").str.strip().str.lower()

    global_analysis = analyze_group(df, settings)
    by_category = category_quota_report(df, settings)
    by_platform = platform_report(df, settings)
    shares = category_shares(df)

    filters = settings.get("filters", {})
    max_creator_category = int(filters.get("max_per_creator_per_category", 5))
    creator_category_counts = (
        df.groupby(["platform", "category", "creator_id_hash"]).size()
        if not df.empty
        else pd.Series(dtype=int)
    )
    creator_category_cap_ok = bool(
        len(creator_category_counts) == 0 or creator_category_counts.max() <= max_creator_category
    )

    category_minimum = {
        category: {
            platform: int(by_category[category]["by_platform"].get(platform, 0)) >= 400
            if platform == "tiktok"
            else int(by_category[category]["by_platform"].get(platform, 0)) >= 150
            for platform in PLATFORMS
        }
        for category in CATEGORIES
    }
    category_shares_ok = all(
        item["share"] <= float(filters.get("max_category_share", 0.25))
        for platform_data in shares.values()
        for item in platform_data["categories"].values()
    )

    acceptance = {
        "tiktok_min_3000": global_analysis["by_platform"].get("tiktok", 0) >= 3000,
        "instagram_min_1000": global_analysis["by_platform"].get("instagram", 0) >= 1000,
        "each_category_tiktok_min_400": all(item["tiktok"] for item in category_minimum.values()),
        "each_category_instagram_min_150": all(item["instagram"] for item in category_minimum.values()),
        "core_columns_null_below_5pct": all(value < 0.05 for value in global_analysis["core_null_pct"].values()),
        "no_creator_above_5pct": all(item["share"] <= 0.05 for item in global_analysis["top_creators"]),
        "max_5_videos_per_creator_per_category": creator_category_cap_ok,
        "no_category_above_25pct": category_shares_ok,
    }

    report = {
        "generated_at": now_utc().isoformat(),
        "rows": int(len(df)),
        "by_category": {category: int((df["category"] == category).sum()) for category in CATEGORIES},
        "by_platform": global_analysis["by_platform"],
        "by_category_platform": (
            df.groupby(["category", "platform"]).size().reset_index(name="rows").to_dict("records")
            if not df.empty
            else []
        ),
        "by_seed": (
            df.groupby(["platform", "category", "seed_source"]).size().reset_index(name="rows").to_dict("records")
            if not df.empty
            else []
        ),
        "category_shares": shares,
        "global": global_analysis,
        "per_platform": by_platform,
        "per_category": by_category,
        "category_minimum": category_minimum,
        "creator_category_cap": {
            "max_allowed": max_creator_category,
            "passed": creator_category_cap_ok,
        },
        "merge_report": merge_report or {},
        "acceptance": acceptance,
        "acceptance_all_passed": all(acceptance.values()),
    }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the final dataset per category.")
    parser.add_argument("--input", help="CSV path. Default: data/processed/dataset_v1.csv")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if acceptance rules fail")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    setup_logging(resolve_path(settings["paths"].get("logs", "logs")) / "scrape.log")

    input_path = resolve_path(args.input) if args.input else resolve_path(settings["paths"]["processed"]) / "dataset_v1.csv"
    if not input_path.exists():
        LOG.error("Dataset not found: %s", input_path)
        return 1

    df = pd.read_csv(input_path)
    merge_path = input_path.parent / "merge_report.json"
    merge_report = json.loads(merge_path.read_text(encoding="utf-8")) if merge_path.exists() else None

    report = validate(df, merge_report, settings)
    out_path = input_path.parent / "validation_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("=== VALIDATION ===")
    print(f"Rows: {report['rows']} | Platforms: {report['by_platform']}")
    print(f"Per category: {report['by_category']}")
    print(f"Acceptance: {report['acceptance']}")
    print(f"Report: {out_path}")
    return 0 if (report["acceptance_all_passed"] or not args.strict) else 1


if __name__ == "__main__":
    sys.exit(main())
