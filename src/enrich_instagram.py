"""Enrich Instagram media with full metadata and creator follower counts.

The hashtag search response only contains a small subset of fields. This script
uses the logged-in browser session to fetch:
- /api/v1/media/<pk>/info/ for duration, views, likes, comments, and sound data
- the public profile page for follower_count

Results are cached in data/interim/instagram_enrichment.json so the run can be
resumed and the parser can fill missing fields.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from src.common import (
    BlockedError,
    RateLimiter,
    atomic_write_json,
    load_settings,
    resolve_path,
    setup_logging,
    to_float,
    to_int,
)
from src.parse import _walk_ig_objects
from src.scrape_instagram import build_session, is_logged_in, page_is_login_wall

LOG = logging.getLogger("enrich_instagram")

API_HEADERS = {
    "x-ig-app-id": "936619743392459",
    "x-requested-with": "XMLHttpRequest",
    "referer": "https://www.instagram.com/",
}
FOLLOWER_RE = re.compile(
    r"([\d.,]+\s*(?:jt|rb|[kmb])?)\s*(?:pengikut|followers?)",
    re.IGNORECASE,
)


def collect_targets(raw_root: Path) -> dict[str, dict[str, Any]]:
    """Return code -> {pk, username} from all raw Instagram responses."""
    targets: dict[str, dict[str, Any]] = {}
    base = raw_root / "instagram"
    if not base.exists():
        return targets
    for path in base.rglob("*.json"):
        if path.name.endswith(".meta.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for media in _walk_ig_objects(payload):
            code = media.get("code")
            pk = media.get("pk")
            if not code or not pk:
                continue
            user = media.get("user") or media.get("owner") or {}
            targets[str(code)] = {
                "pk": str(pk),
                "username": user.get("username"),
            }
    return targets


def parse_metric(value: Any) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower().replace(" ", "")
    multiplier = 1
    for suffix, factor in (("jt", 1_000_000), ("rb", 1_000), ("k", 1_000), ("m", 1_000_000), ("b", 1_000_000_000)):
        if text.endswith(suffix):
            multiplier = factor
            text = text[: -len(suffix)]
            break
    if multiplier > 1:
        try:
            return int(float(text.replace(",", ".")) * multiplier)
        except ValueError:
            return None
    text = text.replace(".", "").replace(",", "")
    try:
        return int(text)
    except ValueError:
        return None


def parse_profile_followers(body: str) -> int | None:
    for tag in re.findall(r"<meta\b[^>]*>", body, re.IGNORECASE):
        if "og:description" not in tag:
            continue
        match = re.search(r'content="([^"]*)"', tag, re.IGNORECASE)
        if not match:
            continue
        text = html.unescape(match.group(1))
        follower_match = FOLLOWER_RE.search(text)
        if follower_match:
            return parse_metric(follower_match.group(1))
    return None


def media_extra(pk: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    items = payload.get("items") or []
    if not items:
        return None
    item = items[0]
    clips = item.get("clips_metadata") or {}
    original = clips.get("original_sound_info") or {}
    music = clips.get("music_info") or {}
    user = item.get("user") or item.get("owner") or {}

    sound_id = (
        original.get("audio_asset_id")
        or music.get("audio_asset_id")
        or music.get("id")
        or clips.get("music_canonical_id")
    )
    sound_title = (
        original.get("original_audio_title")
        or music.get("title")
        or music.get("song_name")
    )
    if clips.get("audio_type") == "original_sounds" or original:
        is_original = True
    elif clips.get("audio_type"):
        is_original = False
    else:
        is_original = None

    return {
        "pk": str(pk),
        "video_id": item.get("code"),
        "username": user.get("username"),
        "duration_sec": to_float(item.get("video_duration")),
        "views": to_int(item.get("play_count") or item.get("view_count") or item.get("ig_play_count")),
        "likes": to_int(item.get("like_count")),
        "comments": to_int(item.get("comment_count")),
        "shares": to_int(item.get("share_count")),
        "saves": to_int(item.get("save_count")),
        "sound_id": str(sound_id) if sound_id is not None else None,
        "sound_title": sound_title,
        "is_original_sound": is_original,
        "sound_usage_count": parse_metric(original.get("formatted_clips_media_count")),
        "post_timestamp": item.get("taken_at"),
    }


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"media": {}, "profiles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"media": {}, "profiles": {}}
    data.setdefault("media", {})
    data.setdefault("profiles", {})
    return data


def fetch_media(session, pk: str) -> dict[str, Any] | None:
    url = f"https://www.instagram.com/api/v1/media/{pk}/info/"
    page = session.fetch(
        url,
        network_idle=False,
        wait=1500,
        extra_headers=API_HEADERS,
    )
    if page.status != 200:
        LOG.warning("Media %s returned HTTP %s", pk, page.status)
        return None
    try:
        payload = json.loads(page.body or b"{}")
    except json.JSONDecodeError:
        LOG.warning("Media %s returned non-JSON", pk)
        return None
    return media_extra(pk, payload)


def fetch_profile(session, username: str) -> dict[str, Any] | None:
    url = f"https://www.instagram.com/{username}/"
    page = session.fetch(url, network_idle=False, wait=2000)
    if page.status != 200:
        LOG.warning("Profile %s returned HTTP %s", username, page.status)
        return None
    body = (page.body or b"").decode("utf-8", "ignore")
    if "og:description" not in body:
        return None
    follower_count = parse_profile_followers(body)
    if follower_count is None:
        return None
    return {"follower_count": follower_count}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich Instagram media and creator metadata.")
    parser.add_argument("--limit", type=int, help="Limit media and profile targets for a test run")
    parser.add_argument("--media-only", action="store_true", help="Only enrich media metadata")
    parser.add_argument("--profiles-only", action="store_true", help="Only enrich creator profiles")
    parser.add_argument("--refresh", action="store_true", help="Ignore the existing cache")
    parser.add_argument("--headful", action="store_true", help="Show the Chrome window")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    setup_logging(resolve_path(settings["paths"].get("logs", "logs")) / "scrape.log")

    cfg = dict(settings["instagram"])
    profile = settings["paths"].get("instagram_profile")
    if profile:
        cfg["user_data_dir"] = profile
    cfg["headless"] = not args.headful

    raw_root = resolve_path(settings["paths"]["raw"])
    interim = resolve_path(settings["paths"]["interim"])
    interim.mkdir(parents=True, exist_ok=True)
    cache_path = interim / "instagram_enrichment.json"
    cache = load_cache(cache_path)

    targets = collect_targets(raw_root)
    if args.limit:
        targets = dict(list(targets.items())[: args.limit])
    if not targets:
        LOG.error("No Instagram media found under data/raw/instagram.")
        return 1

    LOG.info("Enrichment targets: %d media items", len(targets))
    enrich_cfg = dict(cfg)
    enrich_cfg.update({"sleep_min": 1.5, "sleep_max": 3.0, "long_pause_every": 40, "long_pause_sec": 30})
    limiter = RateLimiter(enrich_cfg, LOG)

    try:
        with build_session(cfg, record_requests=False) as session:
            home = session.fetch("https://www.instagram.com/", network_idle=False, wait=2000)
            if not is_logged_in(home) or page_is_login_wall(home):
                LOG.error("Instagram session is not logged in. Run `python -m src.scrape_instagram --login` first.")
                return 1

            if not args.profiles_only:
                for index, (code, target) in enumerate(targets.items(), start=1):
                    if not args.refresh and code in cache["media"]:
                        continue
                    LOG.info("[media %d/%d] %s", index, len(targets), code)
                    try:
                        extra = fetch_media(session, target["pk"])
                    except BlockedError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("Media %s failed: %s", code, exc)
                        extra = None
                    if extra:
                        cache["media"][code] = extra
                        if extra.get("username"):
                            cache["profiles"].setdefault(extra["username"], {})
                        atomic_write_json(cache_path, cache)
                    limiter.sleep()

            if not args.media_only:
                usernames = sorted(
                    {
                        target.get("username")
                        or cache["media"].get(code, {}).get("username")
                        for code, target in targets.items()
                        if target.get("username") or cache["media"].get(code, {}).get("username")
                    }
                )
                if args.limit:
                    usernames = usernames[: args.limit]
                LOG.info("Enrichment targets: %d profiles", len(usernames))
                for index, username in enumerate(usernames, start=1):
                    cached = cache["profiles"].get(username, {})
                    if not args.refresh and cached.get("follower_count") is not None:
                        continue
                    LOG.info("[profile %d/%d] %s", index, len(usernames), username)
                    try:
                        profile_data = fetch_profile(session, username)
                    except BlockedError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("Profile %s failed: %s", username, exc)
                        profile_data = None
                    if profile_data:
                        cache["profiles"][username] = profile_data
                        atomic_write_json(cache_path, cache)
                    limiter.sleep()
    except BlockedError as exc:
        LOG.critical("STOPPED: %s", exc)
        return 2
    except KeyboardInterrupt:
        LOG.warning("Interrupted by user. Enrichment cache was saved.")
        return 130

    atomic_write_json(cache_path, cache)
    LOG.info(
        "Enrichment done: %d media, %d profiles -> %s",
        len(cache["media"]),
        len(cache["profiles"]),
        cache_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
