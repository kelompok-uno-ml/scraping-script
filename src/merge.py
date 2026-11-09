"""Merge interim TikTok + Instagram data, deduplicate, filter, cap, and write the final dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import (
    CATEGORIES,
    is_indonesian_caption,
    is_within_age_window,
    load_settings,
    resolve_path,
    setup_logging,
)
from src.parse import SCHEMA

LOG = logging.getLogger("merge")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        LOG.warning("Interim file not found: %s", path)
        return []
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                LOG.warning("Invalid JSON line in %s", path)
    return records


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    for column in SCHEMA:
        if column not in df.columns:
            df[column] = None
    aux_columns = [column for column in ("_source_language",) if column in df.columns]
    df = df[SCHEMA + aux_columns].copy()
    df["category"] = df["category"].astype("string").str.strip().str.lower()
    df["video_id"] = df["video_id"].astype(str)
    df["hashtags"] = df["hashtags"].fillna("").astype(str)
    df["hashtag_count"] = pd.to_numeric(df["hashtag_count"], errors="coerce").fillna(0).astype(int)
    for column in ("follower_count", "views", "likes", "comments", "shares", "saves", "sound_usage_count"):
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("Int64")
    df["duration_sec"] = pd.to_numeric(df["duration_sec"], errors="coerce")
    df["is_original_sound"] = df["is_original_sound"].map(
        lambda value: None if pd.isna(value) else bool(value)
    )
    df["post_timestamp"] = pd.to_datetime(df["post_timestamp"], utc=True, errors="coerce")
    df["scrape_timestamp"] = pd.to_datetime(df["scrape_timestamp"], utc=True, errors="coerce")
    return df


def deterministic_key(value: Any) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()


def dedupe(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    before = len(df)
    df = df.drop_duplicates(subset=["platform", "video_id"], keep="first")
    return df, before - len(df)


def filter_rows(df: pd.DataFrame, settings: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, int]]:
    filters = settings.get("filters", {})
    min_days = int(filters.get("min_age_days", 7))
    max_days = int(filters.get("max_age_days", 90))

    stats: dict[str, int] = {}

    before = len(df)
    df = df.dropna(subset=["video_id", "post_timestamp", "scrape_timestamp"])
    stats["dropped_missing_timestamp"] = before - len(df)

    before = len(df)
    df = df[df["category"].isin(CATEGORIES)]
    stats["dropped_unknown_category"] = before - len(df)

    before = len(df)
    df = df[df["creator_id_hash"].notna() & (df["creator_id_hash"].astype(str) != "")]
    stats["dropped_missing_creator_hash"] = before - len(df)

    before = len(df)
    mask = [
        is_within_age_window(post.isoformat() if pd.notna(post) else None,
                             scrape.isoformat() if pd.notna(scrape) else None,
                             min_days, max_days)
        for post, scrape in zip(df["post_timestamp"], df["scrape_timestamp"])
    ]
    df = df[mask]
    stats["dropped_age_window"] = before - len(df)

    before = len(df)
    mask = []
    for _, row in df.iterrows():
        source_language = str(row.get("_source_language") or "").lower()
        if source_language == "id":
            mask.append(True)
        elif source_language in {"en", "ms", "de", "es", "vi", "fr", "pt", "it", "ja", "ko", "zh", "ar", "th"}:
            mask.append(False)
        else:
            mask.append(is_indonesian_caption(row.get("caption")))
    df = df[mask]
    stats["dropped_language"] = before - len(df)

    return df.copy(), stats


def cap_per_seed(df: pd.DataFrame, max_per_seed: int) -> tuple[pd.DataFrame, int]:
    if max_per_seed <= 0 or df.empty:
        return df, 0
    before = len(df)
    df = df.copy()
    df["_sort_key"] = df["video_id"].map(deterministic_key)
    parts = []
    for _, group in df.groupby(["platform", "seed_source"], dropna=False):
        parts.append(group.sort_values("_sort_key").head(max_per_seed))
    df = pd.concat(parts).drop(columns=["_sort_key"])
    return df, before - len(df)


def cap_per_category(df: pd.DataFrame, settings: dict[str, Any]) -> tuple[pd.DataFrame, int]:
    targets = settings.get("category_targets", {})
    if df.empty or not targets:
        return df, 0
    before = len(df)
    df = df.copy()
    df["_sort_key"] = df["video_id"].map(deterministic_key)
    parts = []
    for platform in ("tiktok", "instagram"):
        subset = df[df["platform"] == platform]
        if subset.empty:
            continue
        for category, group in subset.groupby("category", dropna=False):
            max_rows = int(targets.get(category, {}).get(f"{platform}_max", 0) or 0)
            ordered = group.sort_values("_sort_key")
            parts.append(ordered.head(max_rows) if max_rows > 0 else ordered)
    result = pd.concat(parts).drop(columns=["_sort_key"]) if parts else df.iloc[0:0].drop(columns=["_sort_key"])
    return result, before - len(result)


def cap_per_creator_per_category(df: pd.DataFrame, max_videos: int) -> tuple[pd.DataFrame, int]:
    if df.empty or max_videos <= 0:
        return df, 0
    before = len(df)
    df = df.copy()
    df["_sort_key"] = df["video_id"].map(deterministic_key)
    kept = (
        df.sort_values("_sort_key")
        .groupby(["platform", "category", "creator_id_hash"], dropna=False)
        .head(max_videos)
    )
    result = kept.drop(columns=["_sort_key"])
    return result, before - len(result)


def cap_creator_share(df: pd.DataFrame, max_share: float) -> tuple[pd.DataFrame, int]:
    if df.empty or max_share <= 0:
        return df, 0
    before = len(df)
    df = df.copy()
    df["_sort_key"] = df["video_id"].map(deterministic_key)
    parts = []
    for _, group in df.groupby("platform", dropna=False):
        current = group
        while True:
            total = len(current)
            if total == 0:
                break
            cap = max(1, math.floor(total * max_share))
            counts = current["creator_id_hash"].value_counts()
            over = counts[counts > cap]
            if over.empty:
                break
            keep_indices: list[Any] = []
            for creator in counts.index:
                subset = current[current["creator_id_hash"] == creator]
                if len(subset) > cap:
                    subset = subset.sort_values("_sort_key").head(cap)
                keep_indices.extend(subset.index.tolist())
            current = current.loc[keep_indices].copy()
        parts.append(current)
    result = pd.concat(parts).drop(columns=["_sort_key"])
    return result, before - len(result)


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    by_platform = df["platform"].value_counts().to_dict() if not df.empty else {}
    by_category = df["category"].value_counts().to_dict() if not df.empty else {}
    by_category_platform = (
        df.groupby(["category", "platform"]).size().reset_index(name="rows").to_dict("records")
        if not df.empty
        else []
    )
    by_seed = (
        df.groupby(["platform", "category", "seed_source"]).size().reset_index(name="rows").to_dict("records")
        if not df.empty
        else []
    )
    return {
        "rows": int(len(df)),
        "by_platform": {str(key): int(value) for key, value in by_platform.items()},
        "by_category": {str(key): int(value) for key, value in by_category.items()},
        "by_category_platform": by_category_platform,
        "by_seed": by_seed,
        "unique_creators": int(df["creator_id_hash"].nunique()) if not df.empty else 0,
        "post_range": {
            "min": df["post_timestamp"].min().isoformat() if not df.empty and df["post_timestamp"].notna().any() else None,
            "max": df["post_timestamp"].max().isoformat() if not df.empty and df["post_timestamp"].notna().any() else None,
        },
    }


def merge(settings: dict[str, Any]) -> dict[str, Any]:
    interim = resolve_path(settings["paths"]["interim"])
    processed = resolve_path(settings["paths"]["processed"])
    processed.mkdir(parents=True, exist_ok=True)

    tiktok = read_jsonl(interim / "tiktok_videos.jsonl")
    instagram = read_jsonl(interim / "instagram_videos.jsonl")
    raw_rows = len(tiktok) + len(instagram)
    if raw_rows == 0:
        raise RuntimeError("No interim data found. Run parse.py after scraping.")

    df = normalize(pd.DataFrame(tiktok + instagram))
    df, duplicates = dedupe(df)
    df, filter_stats = filter_rows(df, settings)

    filters = settings.get("filters", {})
    df, seed_cap_dropped = cap_per_seed(df, int(filters.get("max_per_seed", 250)))
    df, category_cap_dropped = cap_per_category(df, settings)
    df, creator_category_cap_dropped = cap_per_creator_per_category(
        df, int(filters.get("max_per_creator_per_category", 5))
    )
    df, creator_cap_dropped = cap_creator_share(df, float(filters.get("max_creator_share", 0.05)))

    df = df.sort_values(["platform", "post_timestamp", "video_id"]).reset_index(drop=True)
    df = df[SCHEMA]

    csv_path = processed / "dataset_v1.csv"
    parquet_path = processed / "dataset_v1.parquet"
    csv_df = df.copy()
    for column in ("post_timestamp", "scrape_timestamp"):
        csv_df[column] = csv_df[column].map(
            lambda value: value.isoformat() if pd.notna(value) else None
        )
    csv_df.to_csv(csv_path, index=False)
    try:
        df.to_parquet(parquet_path, index=False)
    except Exception as exc:  # pragma: no cover - pyarrow may not be installed
        LOG.warning("Failed to write Parquet: %s", exc)
        parquet_path = None

    report = {
        "input_rows": raw_rows,
        "input_by_platform": {
            "tiktok": len(tiktok),
            "instagram": len(instagram),
        },
        "duplicates_removed": duplicates,
        **filter_stats,
        "seed_cap_dropped": seed_cap_dropped,
        "category_cap_dropped": category_cap_dropped,
        "creator_category_cap_dropped": creator_category_cap_dropped,
        "creator_cap_dropped": creator_cap_dropped,
        "output": summarize(df),
        "files": {
            "csv": str(csv_path),
            "parquet": str(parquet_path) if parquet_path else None,
        },
    }
    (processed / "merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("Final dataset: %d rows -> %s", len(df), csv_path)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return argparse.ArgumentParser(description="Merge and clean the dataset.").parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ = parse_args(argv)
    settings = load_settings()
    setup_logging(resolve_path(settings["paths"].get("logs", "logs")) / "scrape.log")
    try:
        report = merge(settings)
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 1
    print(json.dumps(report["output"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
