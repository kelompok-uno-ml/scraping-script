"""Convert raw TikTok/Instagram JSON into the standard schema (JSONL)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.common import (
    CATEGORIES,
    category_for_seed,
    clean_caption,
    get_salt,
    hashtags_from_text,
    hash_creator,
    iso_utc,
    load_seeds,
    load_settings,
    merge_hashtags,
    now_utc,
    parse_iso,
    resolve_path,
    setup_logging,
    to_float,
    to_int,
)

LOG = logging.getLogger("parse")

SCHEMA = [
    "platform",
    "category",
    "video_id",
    "creator_id_hash",
    "follower_count",
    "post_timestamp",
    "scrape_timestamp",
    "caption",
    "hashtags",
    "hashtag_count",
    "duration_sec",
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "sound_id",
    "sound_title",
    "is_original_sound",
    "sound_usage_count",
    "seed_source",
]


def meta_for(raw_file: Path) -> dict[str, Any]:
    meta_file = raw_file.with_suffix(".meta.json")
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"captured_at": now_utc().isoformat()}


def iter_tiktok_raw(raw_root: Path) -> Iterator[tuple[Path, dict[str, Any], str]]:
    base = raw_root / "tiktok"
    if not base.exists():
        return
    for seed_dir in sorted(path for path in base.iterdir() if path.is_dir() and not path.name.startswith("_")):
        for raw_file in sorted(seed_dir.glob("*.json")):
            if raw_file.name.endswith(".meta.json"):
                continue
            meta = meta_for(raw_file)
            yield raw_file, meta, str(meta.get("seed") or seed_dir.name)


def map_tiktok_item(
    item: dict[str, Any],
    seed: str,
    category: str | None,
    scrape_timestamp: str,
    salt: str,
) -> dict[str, Any] | None:
    video_id = item.get("id") or item.get("aweme_id")
    if not video_id:
        return None

    stats = item.get("statsV2") or item.get("stats") or {}
    author = item.get("author") or {}
    author_stats = item.get("authorStats") or item.get("authorStatsV2") or {}
    music = item.get("music") or {}
    video = item.get("video") or {}

    username = author.get("uniqueId") or author.get("unique_id")
    follower_count = to_int(
        author_stats.get("followerCount")
        or author_stats.get("follower_count")
        or author.get("followerCount")
        or item.get("followerCount")
    )

    caption = clean_caption(item.get("desc"))
    tags_from_text = hashtags_from_text(caption)
    tags_from_extra = [
        extra.get("hashtagName", "")
        for extra in item.get("textExtra") or []
        if isinstance(extra, dict)
    ]
    tags = merge_hashtags(tags_from_extra, tags_from_text)

    original = music.get("original")
    sound_usage = music.get("videoCount")
    if sound_usage is None:
        sound_usage = (music.get("stats") or {}).get("videoCount")

    return {
        "platform": "tiktok",
        "category": category,
        "video_id": str(video_id),
        "creator_id_hash": hash_creator("tiktok", username, salt),
        "follower_count": follower_count,
        "post_timestamp": iso_utc(item.get("createTime")),
        "scrape_timestamp": scrape_timestamp,
        "caption": caption,
        "hashtags": "|".join(tags),
        "hashtag_count": len(tags),
        "duration_sec": to_float(video.get("duration")),
        "views": to_int(stats.get("playCount") or stats.get("play_count")),
        "likes": to_int(stats.get("diggCount") or stats.get("digg_count") or stats.get("likeCount")),
        "comments": to_int(stats.get("commentCount") or stats.get("comment_count")),
        "shares": to_int(stats.get("shareCount") or stats.get("share_count")),
        "saves": to_int(stats.get("collectCount") or stats.get("collect_count") or stats.get("saveCount")),
        "sound_id": str(music.get("id")) if music.get("id") is not None else None,
        "sound_title": music.get("title"),
        "is_original_sound": bool(original) if original is not None else None,
        "sound_usage_count": to_int(sound_usage),
        "seed_source": seed,
        "_source_language": str(item.get("textLanguage") or "").lower() or None,
    }


def _fill_missing(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    """Keep the first record's identity and fill empty fields from another record."""
    result = dict(primary)
    for key, value in secondary.items():
        if result.get(key) is None and value is not None:
            result[key] = value
    return result


def _prefer_earliest(
    best: dict[str, dict[str, Any]],
    record: dict[str, Any],
) -> None:
    video_id = record["video_id"]
    previous = best.get(video_id)
    if previous is None:
        best[video_id] = record
        return
    previous_ts = parse_iso(previous.get("scrape_timestamp"))
    record_ts = parse_iso(record.get("scrape_timestamp"))
    if record_ts is not None and (previous_ts is None or record_ts < previous_ts):
        best[video_id] = _fill_missing(record, previous)
    else:
        best[video_id] = _fill_missing(previous, record)


def parse_tiktok(raw_root: Path, out_file: Path, salt: str) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    seeds_cfg = load_seeds()
    for raw_file, meta, seed in iter_tiktok_raw(raw_root):
        scrape_timestamp = meta.get("captured_at") or now_utc().isoformat()
        category = meta.get("category")
        if category not in CATEGORIES:
            category = category_for_seed(seeds_cfg, seed)
        try:
            payload = json.loads(raw_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("Failed to read %s: %s", raw_file, exc)
            continue
        items = payload.get("itemList") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = map_tiktok_item(item, seed, category, scrape_timestamp, salt)
            if not record:
                continue
            _prefer_earliest(best, record)

    records = sorted(best.values(), key=lambda row: row.get("post_timestamp") or "")
    write_jsonl(out_file, records)
    LOG.info("TikTok: %d unique videos from raw", len(records))
    return records


def iter_json_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        yield path


def _walk_ig_objects(obj: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 14:
        return
    if isinstance(obj, dict):
        if is_ig_media(obj):
            yield obj
        for value in obj.values():
            yield from _walk_ig_objects(value, depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_ig_objects(value, depth + 1)


def is_ig_media(obj: dict[str, Any]) -> bool:
    code = obj.get("code")
    if not isinstance(code, str) or len(code) < 5:
        return False
    has_counter = any(key in obj for key in ("like_count", "play_count", "view_count", "taken_at", "taken_at_timestamp"))
    has_payload = any(key in obj for key in ("user", "owner", "image_versions2", "video_versions", "media_type", "product_type"))
    return has_counter and has_payload


def map_ig_media(
    media: dict[str, Any],
    seed: str,
    category: str | None,
    scrape_timestamp: str,
    salt: str,
) -> dict[str, Any] | None:
    code = media.get("code")
    if not code:
        return None

    media_type = media.get("media_type")
    is_video = (
        str(media_type) == "2"
        or media.get("video_versions") is not None
        or media.get("product_type") == "clips"
    )
    if not is_video:
        return None

    user = media.get("user") or media.get("owner") or {}
    username = user.get("username")
    follower_count = to_int(
        user.get("follower_count")
        or (user.get("edge_followed_by") or {}).get("count")
    )

    caption_obj = media.get("caption")
    if isinstance(caption_obj, dict):
        caption_raw = caption_obj.get("text")
    elif isinstance(caption_obj, str):
        caption_raw = caption_obj
    else:
        edges = ((media.get("edge_media_to_caption") or {}).get("edges") or [])
        caption_raw = edges[0].get("node", {}).get("text") if edges else None
    caption = clean_caption(caption_raw)
    tags = merge_hashtags(hashtags_from_text(caption))

    music = media.get("music_metadata") or {}
    music_info = music.get("music_info") or music.get("original_sound_info") or {}
    sound_id = music_info.get("id") or music_info.get("audio_asset_id") or music.get("audio_asset_id")
    sound_title = music_info.get("title") or music_info.get("song_name") or music.get("title")
    if music.get("original_sound_info") is not None:
        is_original = True
    elif music_info.get("is_original") is not None:
        is_original = bool(music_info.get("is_original"))
    else:
        is_original = None

    taken_at = media.get("taken_at") or media.get("taken_at_timestamp")
    return {
        "platform": "instagram",
        "category": category,
        "video_id": str(code),
        "creator_id_hash": hash_creator("instagram", username, salt),
        "follower_count": follower_count,
        "post_timestamp": iso_utc(taken_at),
        "scrape_timestamp": scrape_timestamp,
        "caption": caption,
        "hashtags": "|".join(tags),
        "hashtag_count": len(tags),
        "duration_sec": to_float(media.get("video_duration")),
        "views": to_int(media.get("play_count") or media.get("view_count") or media.get("video_view_count")),
        "likes": to_int(media.get("like_count") or (media.get("edge_liked_by") or {}).get("count")),
        "comments": to_int(media.get("comment_count") or (media.get("edge_media_to_comment") or {}).get("count")),
        "shares": to_int(media.get("share_count")),
        "saves": to_int(media.get("save_count")),
        "sound_id": str(sound_id) if sound_id is not None else None,
        "sound_title": sound_title,
        "is_original_sound": is_original,
        "sound_usage_count": to_int(music_info.get("video_count") or music.get("video_count")),
        "seed_source": seed,
    }


def parse_instagram(raw_root: Path, out_file: Path, salt: str) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    seeds_cfg = load_seeds()
    base = raw_root / "instagram"
    for raw_file in iter_json_files(base):
        meta = meta_for(raw_file)
        scrape_timestamp = meta.get("captured_at") or now_utc().isoformat()
        parts = raw_file.relative_to(base).parts if raw_file.is_relative_to(base) else ()
        seed = str(meta.get("seed") or (parts[0] if parts else "unknown"))
        category = meta.get("category")
        if category not in CATEGORIES:
            category = category_for_seed(seeds_cfg, seed)
        try:
            payload = json.loads(raw_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for media in _walk_ig_objects(payload):
            record = map_ig_media(media, seed, category, scrape_timestamp, salt)
            if not record:
                continue
            _prefer_earliest(best, record)

    records = sorted(best.values(), key=lambda row: row.get("post_timestamp") or "")
    write_jsonl(out_file, records)
    LOG.info("Instagram: %d unique videos from raw", len(records))
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse raw JSON into the standard schema.")
    parser.add_argument("--platform", choices=["tiktok", "instagram", "all"], default="all")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    setup_logging(resolve_path(settings["paths"].get("logs", "logs")) / "scrape.log")
    salt = get_salt()
    raw_root = resolve_path(settings["paths"]["raw"])
    interim = resolve_path(settings["paths"]["interim"])
    interim.mkdir(parents=True, exist_ok=True)

    if args.platform in {"tiktok", "all"}:
        parse_tiktok(raw_root, interim / "tiktok_videos.jsonl", salt)
    if args.platform in {"instagram", "all"}:
        parse_instagram(raw_root, interim / "instagram_videos.jsonl", salt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
