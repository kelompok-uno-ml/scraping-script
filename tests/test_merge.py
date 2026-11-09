import os
import unittest
from datetime import timedelta

import pandas as pd

os.environ.setdefault("SCRAPE_SALT", "test-salt")

from src.common import now_utc
from src.merge import (
    cap_creator_share,
    cap_per_category,
    cap_per_creator_per_category,
    cap_per_seed,
    dedupe,
    filter_rows,
    normalize,
)


def make_record(
    video_id: str,
    creator: str,
    seed: str = "kulinerindonesia",
    caption: str = "resep enak banget",
    category: str = "kuliner",
    source_language: str | None = None,
) -> dict:
    now = now_utc()
    return {
        "platform": "tiktok",
        "category": category,
        "video_id": video_id,
        "creator_id_hash": creator,
        "follower_count": 1000,
        "post_timestamp": (now - timedelta(days=10)).isoformat(),
        "scrape_timestamp": now.isoformat(),
        "caption": caption,
        "hashtags": "resep",
        "hashtag_count": 1,
        "duration_sec": 15,
        "views": 1000,
        "likes": 100,
        "comments": 10,
        "shares": 1,
        "saves": 2,
        "sound_id": "s1",
        "sound_title": "Sound",
        "is_original_sound": True,
        "sound_usage_count": 100,
        "seed_source": seed,
        "_source_language": source_language,
    }


class MergeTest(unittest.TestCase):
    def test_normalize_and_dedupe(self):
        df = normalize(pd.DataFrame([make_record("1", "a"), make_record("1", "a")]))
        self.assertEqual(len(df), 2)
        df, removed = dedupe(df)
        self.assertEqual(len(df), 1)
        self.assertEqual(removed, 1)

    def test_filter_age_and_language(self):
        settings = {"filters": {"min_age_days": 7, "max_age_days": 90}}
        df = normalize(pd.DataFrame([
            make_record("1", "a"),
            make_record("2", "b", caption="Best pasta recipe ever with fresh tomatoes and basil"),
        ]))
        df, stats = filter_rows(df, settings)
        self.assertEqual(len(df), 1)
        self.assertEqual(stats["dropped_language"], 1)

    def test_source_language_override(self):
        settings = {"filters": {"min_age_days": 7, "max_age_days": 90}}
        df = normalize(pd.DataFrame([
            make_record("1", "a", caption="This is a game", source_language="id"),
            make_record("2", "b", caption="resep enak banget", source_language="en"),
        ]))
        df, stats = filter_rows(df, settings)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["video_id"], "1")
        self.assertEqual(stats["dropped_language"], 1)

    def test_filter_unknown_category(self):
        settings = {"filters": {"min_age_days": 7, "max_age_days": 90}}
        df = normalize(pd.DataFrame([
            make_record("1", "a"),
            make_record("2", "b", category="uncategorized"),
        ]))
        df, stats = filter_rows(df, settings)
        self.assertEqual(len(df), 1)
        self.assertEqual(stats["dropped_unknown_category"], 1)

    def test_creator_cap(self):
        rows = [make_record(str(i), "dominant") for i in range(100)]
        df = normalize(pd.DataFrame(rows))
        df, dropped = cap_creator_share(df, 0.05)
        self.assertLessEqual(len(df), 5)
        self.assertGreater(dropped, 0)

    def test_seed_cap(self):
        rows = [make_record(str(i), f"c{i}") for i in range(10)]
        df = normalize(pd.DataFrame(rows))
        df, dropped = cap_per_seed(df, 3)
        self.assertEqual(len(df), 3)
        self.assertEqual(dropped, 7)

    def test_creator_cap_per_category(self):
        rows = [make_record(str(i), "dominant") for i in range(10)]
        rows += [make_record(f"g{i}", "dominant", category="gaming") for i in range(3)]
        df = normalize(pd.DataFrame(rows))
        df, dropped = cap_per_creator_per_category(df, 5)
        self.assertEqual(int((df["category"] == "kuliner").sum()), 5)
        self.assertEqual(int((df["category"] == "gaming").sum()), 3)
        self.assertEqual(dropped, 5)

    def test_category_cap(self):
        settings = {"category_targets": {"kuliner": {"tiktok_max": 4, "instagram_max": 2}}}
        rows = [make_record(str(i), f"c{i}") for i in range(10)]
        df = normalize(pd.DataFrame(rows))
        df, dropped = cap_per_category(df, settings)
        self.assertEqual(len(df), 4)
        self.assertEqual(dropped, 6)


if __name__ == "__main__":
    unittest.main()
