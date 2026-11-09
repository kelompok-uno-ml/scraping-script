"""Shared utilities: config, logging, progress, rate limiting, hashing, filters."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency opsional saat tes
    load_dotenv = None

try:
    from langdetect import DetectorFactory, detect
except ImportError:  # pragma: no cover - dependency opsional saat tes
    DetectorFactory = None
    detect = None

if DetectorFactory is not None:
    DetectorFactory.seed = 0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_SEEDS = PROJECT_ROOT / "config" / "seeds.yaml"

MENTION_RE = re.compile(r"@[\w.]+", re.UNICODE)
HASHTAG_RE = re.compile(r"#([\w]+)", re.UNICODE)

# Category order follows UPDATE_BRIEF.md.
CATEGORIES = [
    "kuliner",
    "fashion_beauty",
    "edukasi_tips",
    "hiburan_komedi",
    "lifestyle_travel",
    "gaming",
]

# Common Indonesian words for short captions that language detection often misses.
ID_WORDS = {
    "ada", "adalah", "aku", "anak", "atau", "bahan", "banget", "bikin", "buat",
    "cara", "cuma", "dan", "dari", "dengan", "di", "dia", "enak", "gak", "gue",
    "hasil", "ini", "itu", "jadi", "jajan", "jajanan", "kalian", "kamu", "karena",
    "kue", "kuliner", "lagi", "lalu", "makan", "makanan", "masak", "masakan",
    "mau", "murah", "nabi", "nggak", "nih", "pedas", "pake", "pakai", "resep",
    "rumahan", "saya", "sekali", "sederhana", "tanpa", "tapi", "tempat", "terus",
    "tidak", "untuk", "wajib", "yang",
}


class BlockedError(RuntimeError):
    """The platform returned a CAPTCHA, login wall, or verification wall."""


def load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    return load_yaml(path or DEFAULT_SETTINGS)


def load_seeds(path: str | Path | None = None) -> dict[str, list[str]]:
    data = load_yaml(path or DEFAULT_SEEDS)
    seeds: dict[str, list[str]] = {}
    for key, value in data.items():
        category = str(key)
        if category not in CATEGORIES:
            raise ValueError(
                f"Unknown seed category: {category!r}. "
                f"Use one of {CATEGORIES}."
            )
        seeds[category] = [
            str(item).lstrip("#").strip()
            for item in value or []
            if str(item).lstrip("#").strip()
        ]
    return seeds


def iter_seed_entries(
    seeds_cfg: dict[str, list[str]],
    categories: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Return (category, seed) pairs in a stable category order."""
    selected = categories or [category for category in CATEGORIES if category in seeds_cfg]
    entries: list[tuple[str, str]] = []
    for category in selected:
        for seed in seeds_cfg.get(category, []):
            entries.append((category, seed))
    return entries


def category_for_seed(seeds_cfg: dict[str, list[str]], seed: str) -> str | None:
    normalized = str(seed).lstrip("#").strip()
    for category in CATEGORIES:
        if normalized in seeds_cfg.get(category, []):
            return category
    return None


def creator_tier(follower_count: Any, tiers: dict[str, Any] | None = None) -> str:
    tiers = tiers or {}
    small_max = float(tiers.get("small_max", 10_000))
    medium_max = float(tiers.get("medium_max", 100_000))
    if follower_count is None or follower_count == "":
        return "unknown"
    try:
        value = float(follower_count)
    except (TypeError, ValueError):
        return "unknown"
    if value < small_max:
        return "small"
    if value < medium_max:
        return "medium"
    return "large"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def ensure_parent(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_json(path: str | Path, data: Any) -> None:
    path = ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def setup_logging(log_file: str | Path = "logs/scrape.log", level: int = logging.INFO) -> None:
    path = resolve_path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(console)


def load_progress(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_progress(path: str | Path, progress: dict[str, Any]) -> None:
    atomic_write_json(path, progress)


def get_salt() -> str:
    load_env()
    salt = os.environ.get("SCRAPE_SALT", "").strip()
    if not salt:
        raise RuntimeError(
            "SCRAPE_SALT is not set. Copy .env.example to .env and add a random salt."
        )
    return salt


def hash_creator(platform: str, username: str | None, salt: str) -> str | None:
    if not username:
        return None
    raw = f"{platform}:{username.strip().lower()}:{salt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def clean_caption(text: str | None) -> str:
    text = MENTION_RE.sub("@user", text or "")
    return re.sub(r"\s+", " ", text).strip()


def hashtags_from_text(text: str | None) -> list[str]:
    return [match.lower() for match in HASHTAG_RE.findall(text or "")]


def merge_hashtags(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for tag in group:
            tag = str(tag).lstrip("#").strip().lower()
            if tag and tag not in seen:
                seen.add(tag)
                result.append(tag)
    return result


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace(",", "").strip()
    multiplier = 1
    if text[-1:].lower() == "k":
        multiplier, text = 1_000, text[:-1]
    elif text[-1:].lower() == "m":
        multiplier, text = 1_000_000, text[:-1]
    elif text[-1:].lower() == "b":
        multiplier, text = 1_000_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iso_utc(timestamp: Any) -> str | None:
    if timestamp is None or timestamp == "":
        return None
    if isinstance(timestamp, str):
        try:
            timestamp = float(timestamp)
        except ValueError:
            return None
    try:
        timestamp = float(timestamp)
    except (TypeError, ValueError):
        return None
    if timestamp > 1e12:  # milliseconds
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_indonesian_caption(text: str | None) -> bool:
    """Conservative filter: drop only captions that are clearly not Indonesian."""
    text = (text or "").strip()
    if not text:
        return False
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    if not tokens:
        return False
    id_hits = sum(1 for token in tokens if token in ID_WORDS)
    if len(tokens) <= 4:
        # Very short captions do not provide enough evidence to drop them.
        return True
    if id_hits >= 1 and len(tokens) <= 8:
        return True
    if detect is not None and len(text) >= 12:
        try:
            language = detect(text)
        except Exception:
            language = None
        if language == "id":
            return True
        if language in {"en", "ms", "es", "fr", "de", "pt", "it", "ja", "ko", "zh-cn", "zh-tw", "ar", "th", "vi"}:
            return id_hits > 0
    return id_hits > 0 or len(tokens) < 3


def seed_slug(seed: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", seed.lower()).strip("_")
    return slug or "seed"


def is_within_age_window(post_iso: str | None, scrape_iso: str | None, min_days: int, max_days: int) -> bool:
    post = parse_iso(post_iso)
    scrape = parse_iso(scrape_iso)
    if post is None or scrape is None:
        return False
    age = scrape - post
    return timedelta(days=min_days) <= age <= timedelta(days=max_days)


class RateLimiter:
    """Random delay plus a periodic long pause."""

    def __init__(self, cfg: dict[str, Any], logger: logging.Logger | None = None):
        self.cfg = cfg
        self.logger = logger or logging.getLogger(__name__)
        self.count = 0

    def sleep(self) -> None:
        low = float(self.cfg.get("sleep_min", 2))
        high = float(self.cfg.get("sleep_max", 5))
        if high < low:
            low, high = high, low
        time.sleep(random.uniform(low, high))
        self.count += 1
        every = int(self.cfg.get("long_pause_every", 0) or 0)
        if every and self.count % every == 0:
            pause = float(self.cfg.get("long_pause_sec", 45))
            self.logger.info("Long pause %.0fs after %d requests", pause, self.count)
            time.sleep(pause)


def looks_like_block(body: bytes, status: int | None = None) -> bool:
    if status in {401, 403, 429}:
        return True
    if not body:
        return False
    if len(body) < 5000 and b"Please wait" in body:
        return True
    if b"captcha-verify" in body and b"__UNIVERSAL_DATA_FOR_REHYDRATION__" not in body:
        return True
    return False
