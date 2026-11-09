import unittest

from src.common import (
    CATEGORIES,
    category_for_seed,
    creator_tier,
    iter_seed_entries,
    load_seeds,
)


class CategoryConfigTest(unittest.TestCase):
    def test_all_categories_present(self):
        seeds = load_seeds()
        self.assertEqual(set(seeds), set(CATEGORIES))
        for category in CATEGORIES:
            self.assertGreaterEqual(len(seeds[category]), 8)

    def test_seed_entries_and_lookup(self):
        seeds = load_seeds()
        entries = iter_seed_entries(seeds)
        self.assertEqual(len(entries), sum(len(value) for value in seeds.values()))
        self.assertEqual(category_for_seed(seeds, "mobilelegends"), "gaming")
        self.assertEqual(category_for_seed(seeds, "#ootd"), "fashion_beauty")
        self.assertIsNone(category_for_seed(seeds, "tidak_ada_seed_ini"))

    def test_creator_tiers(self):
        tiers = {"small_max": 10_000, "medium_max": 100_000}
        self.assertEqual(creator_tier(500, tiers), "small")
        self.assertEqual(creator_tier(10_000, tiers), "medium")
        self.assertEqual(creator_tier(100_000, tiers), "large")
        self.assertEqual(creator_tier(None, tiers), "unknown")


if __name__ == "__main__":
    unittest.main()
