"""TikTok scraper using a browser and XHR capture from /api/challenge/item_list/."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.common import (
    CATEGORIES,
    BlockedError,
    RateLimiter,
    atomic_write_json,
    category_for_seed,
    ensure_parent,
    iter_seed_entries,
    load_progress,
    load_seeds,
    load_settings,
    looks_like_block,
    now_utc,
    resolve_path,
    save_progress,
    seed_slug,
    setup_logging,
)

LOG = logging.getLogger("tiktok")


def build_session(cfg: dict[str, Any]):
    from scrapling.fetchers import StealthySession

    kwargs: dict[str, Any] = {
        "headless": bool(cfg.get("headless", False)),
        "real_chrome": bool(cfg.get("real_chrome", True)),
        "record_requests": True,
        "max_pages": 1,
        "max_recorded_requests": int(cfg.get("max_recorded_requests", 400)),
        "locale": cfg.get("locale", "id-ID"),
        "timezone_id": cfg.get("timezone_id", "Asia/Jakarta"),
        "block_webrtc": True,
        "dns_over_https": True,
        "retries": int(cfg.get("retries", 3)),
        "timeout": int(cfg.get("fetch_timeout_ms", 45000)),
    }
    if cfg.get("proxy"):
        kwargs["proxy"] = cfg["proxy"]
    blocked = cfg.get("blocked_domains") or []
    if blocked:
        kwargs["blocked_domains"] = set(blocked)
    return StealthySession(**kwargs)


def save_response(
    response,
    seed: str,
    category: str,
    seed_dir: Path,
    url: str,
) -> dict[str, Any] | None:
    body = response.body or b""
    if not body:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        LOG.warning("Response %s is not JSON (status %s); skipped", response.url, response.status)
        return None
    if not payload.get("itemList"):
        return None

    cursor = payload.get("cursor", 0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = ensure_parent(seed_dir / f"{stamp}_c{cursor}.json")
    raw_path.write_bytes(body)

    item_count = len(payload.get("itemList") or [])
    meta = {
        "platform": "tiktok",
        "seed": seed,
        "category": category,
        "url": url,
        "response_url": response.url,
        "status": response.status,
        "captured_at": now_utc().isoformat(),
        "cursor": cursor,
        "has_more": payload.get("hasMore"),
        "item_count": item_count,
        "raw_file": raw_path.name,
    }
    atomic_write_json(seed_dir / f"{raw_path.stem}.meta.json", meta)
    item_ids = [str(item.get("id")) for item in payload.get("itemList") or [] if item.get("id")]
    return {"cursor": cursor, "item_ids": item_ids, "has_more": payload.get("hasMore")}


def collect_once(seed: str, category: str, cfg: dict[str, Any], raw_root: Path) -> dict[str, Any]:
    seed_dir = raw_root / "tiktok" / seed_slug(seed)
    seed_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(cfg, LOG)
    url = f"https://www.tiktok.com/tag/{seed}?lang=id-ID"
    scrolls = int(cfg.get("scrolls", 10))
    max_items = int(cfg.get("max_per_seed", 250) or 0)

    with build_session(cfg) as session:
        seen: set[str] = set()
        cursors: list[int] = []
        saved = 0
        handled: set[Any] = set()
        exhausted = False

        def harvest() -> None:
            """Save item_list responses immediately, before browser history fills up."""
            nonlocal saved, exhausted
            for response in session.network.search(
                url_pattern=r"/api/(?:post|challenge)/item_list/", limit=None
            ):
                key = response.meta.get("network_id") if isinstance(response.meta, dict) else None
                if key is None:
                    key = (str(response.url), len(response.body or b""))
                if key in handled:
                    continue
                handled.add(key)
                payload = save_response(response, seed, category, seed_dir, url)
                if not payload:
                    continue
                saved += 1
                cursors.append(payload["cursor"])
                seen.update(payload["item_ids"])
                if payload.get("has_more") is False:
                    exhausted = True

        def action(browser_page) -> None:
            nonlocal exhausted
            for _ in range(scrolls):
                if exhausted:
                    break
                # window.scrollTo is more reliable than mouse.wheel for the TikTok feed.
                browser_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                limiter.sleep()
                harvest()
                if max_items and len(seen) >= max_items:
                    exhausted = True

        page = session.fetch(
            url,
            network_idle=bool(cfg.get("network_idle", False)),
            wait=3000,
            page_action=action,
        )
        if page.status in {401, 403, 429}:
            raise BlockedError(f"HTTP {page.status} while opening {url}")
        if looks_like_block(page.body, page.status):
            blocked_dir = raw_root / "tiktok" / "_blocked"
            blocked_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (blocked_dir / f"{seed_slug(seed)}_{stamp}.html").write_bytes(page.body)
            raise BlockedError(f"TikTok returned a WAF/CAPTCHA page for {url}")

        harvest()
        LOG.info("Seed %s [%s]: saved %d item_list responses", seed, category, saved)
        session.network.clear()

    return {
        "items": len(seen),
        "responses": saved,
        "cursor": max(cursors) if cursors else 0,
        "status": "done" if seen else "failed",
        "reason": None if seen else "no item_list responses captured",
    }


def collect_seed(
    category: str,
    seed: str,
    cfg: dict[str, Any],
    raw_root: Path,
    progress: dict[str, Any],
    progress_path: Path,
    force: bool = False,
) -> dict[str, Any]:
    key = f"tiktok:{category}:{seed}"
    if not force and progress.get(key, {}).get("status") == "done":
        LOG.info("Seed %s [%s] is already done; skipping (use --force to rerun)", seed, category)
        return progress[key]

    attempts = max(1, int(cfg.get("retries", 3)))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            LOG.info("Seed %s [%s] attempt %d/%d", seed, category, attempt, attempts)
            result = collect_once(seed, category, cfg, raw_root)
            progress[key] = {
                **result,
                "category": category,
                "seed": seed,
                "at": now_utc().isoformat(),
                "attempt": attempt,
            }
            save_progress(progress_path, progress)
            LOG.info("Seed %s [%s]: %d unique videos, %d responses", seed, category, result["items"], result["responses"])
            return progress[key]
        except BlockedError:
            raise
        except Exception as exc:  # noqa: BLE001 - ingin lanjut retry lalu lapor
            last_error = exc
            LOG.exception("Seed %s [%s] failed attempt %d: %s", seed, category, attempt, exc)
            if attempt < attempts:
                import time

                time.sleep(min(60, 5 * (2 ** (attempt - 1))))

    progress[key] = {
        "status": "failed",
        "items": 0,
        "category": category,
        "seed": seed,
        "reason": str(last_error),
        "at": now_utc().isoformat(),
    }
    save_progress(progress_path, progress)
    return progress[key]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape TikTok hashtags per category with a browser and XHR capture.")
    parser.add_argument("--categories", help="Comma-separated categories, e.g. kuliner,gaming")
    parser.add_argument("--seeds", help="Comma-separated seeds; category is inferred from config/seeds.yaml")
    parser.add_argument("--seeds-file", help="YAML file mapping categories to seeds")
    parser.add_argument("--seed-limit", type=int, help="Limit the total number of seeds")
    parser.add_argument("--max-per-seed", type=int, help="Override the maximum videos per seed")
    parser.add_argument("--scrolls", type=int, help="Override the number of scrolls per seed")
    parser.add_argument("--pilot", action="store_true", help="Pilot mode: one seed per category, one scroll, max 20")
    parser.add_argument("--headful", action="store_true", help="Show the Chrome window")
    parser.add_argument("--headless", action="store_true", help="Hide the Chrome window")
    parser.add_argument("--force", action="store_true", help="Rerun seeds marked as done")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without launching a browser")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    setup_logging(resolve_path(settings["paths"].get("logs", "logs")) / "scrape.log")

    if args.seeds_file:
        seeds_cfg = load_seeds(resolve_path(args.seeds_file))
    else:
        seeds_cfg = load_seeds()

    cfg = dict(settings["tiktok"])
    if args.headful:
        cfg["headless"] = False
    if args.headless:
        cfg["headless"] = True
    if args.scrolls is not None:
        cfg["scrolls"] = args.scrolls
    if args.max_per_seed is not None:
        cfg["max_per_seed"] = args.max_per_seed

    if args.categories:
        categories = [item.strip() for item in args.categories.split(",") if item.strip()]
        unknown = [item for item in categories if item not in CATEGORIES]
        if unknown:
            LOG.error("Unknown categories: %s", unknown)
            return 1
    else:
        categories = [category for category in CATEGORIES if category in seeds_cfg]

    if args.pilot:
        entries = [(category, seeds_cfg[category][0]) for category in categories if seeds_cfg.get(category)]
        cfg["scrolls"] = 1
        cfg["max_per_seed"] = min(int(cfg.get("max_per_seed", 250)), 20)
    elif args.seeds:
        entries = []
        for item in args.seeds.split(","):
            seed = item.strip().lstrip("#")
            if not seed:
                continue
            category = category_for_seed(seeds_cfg, seed)
            if category is None:
                LOG.error("Seed %r is not in config/seeds.yaml; add it first so its category is clear", seed)
                return 1
            entries.append((category, seed))
    else:
        entries = iter_seed_entries(seeds_cfg, categories)

    if args.seed_limit:
        entries = entries[: args.seed_limit]
    if not entries:
        LOG.error("No seeds to run.")
        return 1

    raw_root = resolve_path(settings["paths"]["raw"])
    progress_path = resolve_path(settings["paths"].get("progress", "logs/progress.json"))
    progress = load_progress(progress_path)

    LOG.info(
        "Plan: %d seeds across %d categories, max %d/seed, scrolls=%d, headless=%s",
        len(entries),
        len({category for category, _ in entries}),
        cfg.get("max_per_seed"),
        cfg.get("scrolls"),
        cfg.get("headless"),
    )
    if args.dry_run:
        for category, seed in entries:
            print(f"[{category}] https://www.tiktok.com/tag/{seed}")
        return 0

    try:
        for index, (category, seed) in enumerate(entries, start=1):
            LOG.info("[%d/%d] %s | %s", index, len(entries), category, seed)
            collect_seed(category, seed, cfg, raw_root, progress, progress_path, force=args.force)
    except BlockedError as exc:
        LOG.critical("STOPPED: %s", exc)
        return 2
    except KeyboardInterrupt:
        LOG.warning("Interrupted by user. Progress was saved.")
        return 130

    current = {
        key: value
        for key, value in progress.items()
        if key.startswith("tiktok:") and len(key.split(":")) == 3
    }
    done = sum(1 for value in current.values() if value.get("status") == "done")
    total = sum(int(value.get("items", 0)) for value in current.values())
    LOG.info("Done: %d seeds completed, %d unique videos before cross-seed dedupe", done, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
