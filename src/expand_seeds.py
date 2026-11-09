"""Suggest new seeds per category from frequently co-occurring hashtags."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from src.common import CATEGORIES, load_seeds, load_settings, resolve_path, setup_logging

GENERIC_TAGS = {
    "fyp", "foryou", "foryoupage", "fypシ", "viral", "trending", "explore",
    "beranda", "tiktok", "tiktokindonesia", "masukberanda", "racun", "racuntiktok",
}


def read_hashtags(interim_file: Path) -> dict[str, Counter]:
    counts: dict[str, Counter] = defaultdict(Counter)
    if not interim_file.exists():
        return counts
    with open(interim_file, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            category = str(row.get("category") or "").strip().lower()
            if category not in CATEGORIES:
                continue
            for tag in str(row.get("hashtags") or "").split("|"):
                tag = tag.strip().lower()
                if tag and len(tag) >= 3 and not re.fullmatch(r"\d+", tag):
                    counts[category][tag] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suggest new hashtag seeds per category.")
    parser.add_argument("--platform", choices=["tiktok", "instagram"], default="tiktok")
    parser.add_argument("--category", help="Limit to one category")
    parser.add_argument("--top", type=int, default=10, help="Number of candidates per category")
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--output", default="config/seeds_generated.yaml")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(resolve_path(settings["paths"].get("logs", "logs")) / "scrape.log")
    interim = resolve_path(settings["paths"]["interim"]) / f"{args.platform}_videos.jsonl"
    existing = load_seeds()

    selected = [args.category] if args.category else CATEGORIES
    unknown = [category for category in selected if category not in CATEGORIES]
    if unknown:
        print(f"Unknown categories: {unknown}")
        return 1

    counts = read_hashtags(interim)
    output_data: dict[str, list[str]] = {}
    for category in selected:
        known = set(existing.get(category, []))
        candidates = [
            (tag, count)
            for tag, count in counts.get(category, Counter()).most_common()
            if tag not in known and tag not in GENERIC_TAGS and count >= args.min_count
        ][: args.top]
        if candidates:
            output_data[category] = [tag for tag, _ in candidates]

    if not output_data:
        print("No candidates yet. Run parse after scraping a few seeds first.")
        return 1

    output = resolve_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(output_data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    print(f"New seed candidates -> {output}")
    for category, tags in output_data.items():
        print(f"\n[{category}]")
        for tag in tags:
            print(f"  {tag:35s} {counts[category][tag]}")
    print("\nUse with:")
    print(f"  python -m src.scrape_tiktok --seeds-file {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
