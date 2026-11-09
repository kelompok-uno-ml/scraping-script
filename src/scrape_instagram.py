"""Instagram Reels scraper using a logged-in browser session (burner account).

Run `--login` once in headful mode, then scrape. Instagram's internal endpoints
change often, so this collector saves all relevant XHR JSON responses under
data/raw/instagram/<seed>/ and the parser extracts media generically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
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
from src.parse import _walk_ig_objects

LOG = logging.getLogger("instagram")


def build_session(cfg: dict[str, Any], record_requests: bool = True):
    from scrapling.fetchers import StealthySession

    profile = resolve_path(cfg.get("user_data_dir", ".chrome-profile-instagram"))
    profile.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "headless": bool(cfg.get("headless", False)),
        "real_chrome": bool(cfg.get("real_chrome", True)),
        "record_requests": record_requests,
        "max_pages": 1,
        "max_recorded_requests": int(cfg.get("max_recorded_requests", 400)),
        "locale": cfg.get("locale", "id-ID"),
        "timezone_id": cfg.get("timezone_id", "Asia/Jakarta"),
        "block_webrtc": True,
        "dns_over_https": True,
        "retries": int(cfg.get("retries", 3)),
        "timeout": int(cfg.get("fetch_timeout_ms", 45000)),
        "user_data_dir": str(profile),
    }
    if cfg.get("proxy"):
        kwargs["proxy"] = cfg["proxy"]
    blocked = cfg.get("blocked_domains") or []
    if blocked:
        kwargs["blocked_domains"] = set(blocked)
    return StealthySession(**kwargs)


def cookie_names(cookies) -> set[str]:
    if isinstance(cookies, dict):
        return {str(key) for key in cookies}
    names: set[str] = set()
    try:
        for item in cookies or ():
            if isinstance(item, (tuple, list)) and item:
                names.add(str(item[0]))
    except TypeError:
        pass
    return names


def page_is_login_wall(page) -> bool:
    url = str(getattr(page, "url", "") or "")
    body = page.body or b""
    if "/accounts/login" in url or "/challenge" in url:
        return True
    if b"checkpoint_required" in body or b"challenge_required" in body:
        return True
    if b"loginForm" in body and b"Log in" in body:
        return True
    return looks_like_block(body, getattr(page, "status", None))


def is_logged_in(page) -> bool:
    url = str(getattr(page, "url", "") or "")
    if "/accounts/login" in url or "/challenge" in url:
        return False
    if "sessionid" in cookie_names(getattr(page, "cookies", None)):
        return True
    body = page.body or b""
    return b"loginForm" not in body and b"Log in" not in body


def save_xhr(response, seed: str, category: str, seed_dir: Path) -> int:
    url = str(response.url)
    if "instagram.com" not in url:
        return 0
    if not any(part in url for part in ("/api/", "/graphql/")):
        return 0
    body = response.body or b""
    if not body:
        return 0
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 0
    text = body.decode("utf-8", errors="ignore")
    if "checkpoint_required" in text or "challenge_required" in text:
        raise BlockedError("Instagram requested a checkpoint/challenge")

    digest = hashlib.sha1(body).hexdigest()[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = ensure_parent(seed_dir / f"{stamp}_{digest}.json")
    raw_path.write_bytes(body)
    atomic_write_json(
        seed_dir / f"{raw_path.stem}.meta.json",
        {
            "platform": "instagram",
            "seed": seed,
            "category": category,
            "response_url": url,
            "status": response.status,
            "captured_at": now_utc().isoformat(),
            "raw_file": raw_path.name,
        },
    )
    return 1


def collect_once(seed: str, category: str, cfg: dict[str, Any], raw_root: Path) -> dict[str, Any]:
    seed_dir = raw_root / "instagram" / seed_slug(seed)
    seed_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(cfg, LOG)
    url = f"https://www.instagram.com/explore/tags/{seed}/"
    scrolls = int(cfg.get("scrolls", 10))

    with build_session(cfg) as session:
        codes: set[str] = set()
        saved = 0
        handled: set[Any] = set()

        def harvest() -> None:
            nonlocal saved
            for response in session.network.search(
                url_pattern=r"instagram\.com/(api|graphql)/", limit=None
            ):
                key = response.meta.get("network_id") if isinstance(response.meta, dict) else None
                if key is None:
                    key = (str(response.url), len(response.body or b""))
                if key in handled:
                    continue
                handled.add(key)
                saved += save_xhr(response, seed, category, seed_dir)
                try:
                    payload = response.json()
                except Exception:
                    continue
                for media in _walk_ig_objects(payload):
                    code = media.get("code")
                    if code:
                        codes.add(str(code))

        def action(browser_page) -> None:
            for _ in range(scrolls):
                browser_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                limiter.sleep()
                harvest()

        page = session.fetch(
            url,
            network_idle=bool(cfg.get("network_idle", False)),
            wait=4000,
            page_action=action,
        )
        if page_is_login_wall(page):
            raise BlockedError("Instagram returned a login/checkpoint page. Run --login with a burner account.")
        if not is_logged_in(page):
            raise BlockedError("Instagram session is not logged in. Run `python -m src.scrape_instagram --login`.")

        harvest()
        LOG.info("Seed %s [%s]: saved %d Instagram XHR responses", seed, category, saved)
        session.network.clear()

    return {
        "items": len(codes),
        "responses": saved,
        "status": "done" if codes else "failed",
        "reason": None if codes else "no media detected in XHR responses",
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
    key = f"instagram:{category}:{seed}"
    if not force and progress.get(key, {}).get("status") == "done":
        LOG.info("Seed %s [%s] is already done; skipping", seed, category)
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
            LOG.info("Seed %s [%s]: %d media items, %d responses", seed, category, result["items"], result["responses"])
            return progress[key]
        except BlockedError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOG.exception("Seed %s [%s] failed attempt %d: %s", seed, category, attempt, exc)
            if attempt < attempts:
                time.sleep(min(90, 8 * (2 ** (attempt - 1))))

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


def login(cfg: dict[str, Any]) -> int:
    cfg = dict(cfg)
    cfg["headless"] = False
    with build_session(cfg) as session:
        session.fetch("https://www.instagram.com/accounts/login/", network_idle=False, wait=3000)
        print("\nThe Chrome window is open.")
        print("1. Log in with a burner account (not your personal account).")
        print("2. Complete 2FA/checkpoint if requested.")
        print("3. Once you reach the home feed, return to the terminal and press Enter.")
        try:
            input()
        except EOFError:
            print("Non-interactive terminal; login cannot be verified.")
            return 1
        page = session.fetch("https://www.instagram.com/", network_idle=False, wait=3000)
        if is_logged_in(page):
            print("Login detected. Browser profile saved at:", cfg.get("user_data_dir"))
            return 0
        print("Login not detected. Run --login again.")
        return 1


def check(cfg: dict[str, Any]) -> int:
    with build_session(cfg) as session:
        page = session.fetch(
            "https://www.instagram.com/explore/tags/kulinerindonesia/",
            network_idle=False,
            wait=3000,
        )
        if is_logged_in(page) and not page_is_login_wall(page):
            print("Instagram session: LOG IN OK")
            return 0
        print("Instagram session: NOT LOGGED IN / checkpoint")
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Instagram Reels per category with a burner session.")
    parser.add_argument("--login", action="store_true", help="Open headful Chrome for manual login")
    parser.add_argument("--check", action="store_true", help="Check whether the session is logged in")
    parser.add_argument("--categories", help="Comma-separated categories")
    parser.add_argument("--seeds", help="Comma-separated seeds; category is inferred from config/seeds.yaml")
    parser.add_argument("--seeds-file", help="YAML file mapping categories to seeds")
    parser.add_argument("--seed-limit", type=int)
    parser.add_argument("--pilot", action="store_true", help="One seed per category, one scroll")
    parser.add_argument("--scrolls", type=int)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    setup_logging(resolve_path(settings["paths"].get("logs", "logs")) / "scrape.log")

    if args.seeds_file:
        seeds_cfg = load_seeds(resolve_path(args.seeds_file))
    else:
        seeds_cfg = load_seeds()

    cfg = dict(settings["instagram"])
    profile = settings["paths"].get("instagram_profile")
    if profile:
        cfg["user_data_dir"] = profile
    if args.headful:
        cfg["headless"] = False
    if args.headless:
        cfg["headless"] = True
    if args.scrolls is not None:
        cfg["scrolls"] = args.scrolls

    if args.login:
        return login(cfg)
    if args.check:
        return check(cfg)

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
        cfg["max_per_seed"] = 20
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
        "Plan: %d seeds across %d categories, scrolls=%d, headless=%s",
        len(entries),
        len({category for category, _ in entries}),
        cfg.get("scrolls"),
        cfg.get("headless"),
    )
    if args.dry_run:
        for category, seed in entries:
            print(f"[{category}] https://www.instagram.com/explore/tags/{seed}/")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
