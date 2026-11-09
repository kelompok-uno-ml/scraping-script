import os
import unittest
from datetime import timedelta

os.environ.setdefault("SCRAPE_SALT", "test-salt")

from src.common import clean_caption, is_indonesian_caption, looks_like_block, now_utc
from src.parse import map_ig_media, map_tiktok_item

SALT = "test-salt"
SCRAPE_TS = now_utc().isoformat()


class TikTokParseTest(unittest.TestCase):
    def test_maps_item(self):
        item = {
            "id": "123456",
            "desc": "Resep ayam enak @chef #masakan #resep",
            "createTime": 1700000000,
            "statsV2": {
                "playCount": "1000",
                "diggCount": "100",
                "commentCount": "10",
                "shareCount": "5",
                "collectCount": "20",
            },
            "video": {"duration": 15},
            "music": {"id": "m1", "title": "Sound Asli", "original": True, "videoCount": 500},
            "author": {"uniqueId": "chef"},
            "authorStats": {"followerCount": "12345"},
            "textExtra": [{"hashtagName": "masakan"}, {"hashtagName": "resep"}],
        }
        record = map_tiktok_item(item, "kulinerindonesia", "kuliner", SCRAPE_TS, SALT)
        self.assertIsNotNone(record)
        self.assertEqual(record["platform"], "tiktok")
        self.assertEqual(record["category"], "kuliner")
        self.assertEqual(record["video_id"], "123456")
        self.assertEqual(record["follower_count"], 12345)
        self.assertEqual(record["views"], 1000)
        self.assertEqual(record["likes"], 100)
        self.assertEqual(record["comments"], 10)
        self.assertEqual(record["shares"], 5)
        self.assertEqual(record["saves"], 20)
        self.assertEqual(record["duration_sec"], 15.0)
        self.assertEqual(record["hashtags"], "masakan|resep")
        self.assertEqual(record["hashtag_count"], 2)
        self.assertTrue(record["is_original_sound"])
        self.assertEqual(record["sound_usage_count"], 500)
        self.assertIn("@user", record["caption"])
        self.assertNotIn("@chef", record["caption"])
        self.assertEqual(len(record["creator_id_hash"]), 64)

    def test_missing_fields_become_none(self):
        record = map_tiktok_item({"id": "1", "desc": "x", "createTime": 1700000000}, "seed", "gaming", SCRAPE_TS, SALT)
        self.assertIsNotNone(record)
        self.assertIsNone(record["follower_count"])
        self.assertIsNone(record["views"])
        self.assertIsNone(record["is_original_sound"])


class InstagramParseTest(unittest.TestCase):
    def test_maps_reel(self):
        media = {
            "code": "ABC123",
            "media_type": 2,
            "like_count": 50,
            "comment_count": 5,
            "play_count": 900,
            "video_duration": 12.5,
            "taken_at": 1700000000,
            "caption": {"text": "Resep cepat #masakan"},
            "user": {"username": "chef", "follower_count": 8000},
            "music_metadata": {
                "music_info": {"id": "audio1", "title": "Lagu", "video_count": 1000}
            },
        }
        record = map_ig_media(media, "kulinerindonesia", "kuliner", SCRAPE_TS, SALT)
        self.assertIsNotNone(record)
        self.assertEqual(record["platform"], "instagram")
        self.assertEqual(record["category"], "kuliner")
        self.assertEqual(record["video_id"], "ABC123")
        self.assertEqual(record["follower_count"], 8000)
        self.assertEqual(record["views"], 900)
        self.assertEqual(record["likes"], 50)
        self.assertEqual(record["sound_id"], "audio1")
        self.assertEqual(record["hashtags"], "masakan")

    def test_skips_photo(self):
        media = {"code": "PHOTO1", "media_type": 1, "like_count": 1, "taken_at": 1700000000, "user": {"username": "x"}}
        self.assertIsNone(map_ig_media(media, "seed", "gaming", SCRAPE_TS, SALT))


class CommonTest(unittest.TestCase):
    def test_clean_caption(self):
        self.assertEqual(clean_caption("Halo @chef  enak"), "Halo @user enak")

    def test_language_filter(self):
        self.assertTrue(is_indonesian_caption("Resep ayam enak banget untuk keluarga"))
        self.assertTrue(is_indonesian_caption("ini enak"))
        self.assertFalse(is_indonesian_caption("Best pasta recipe ever with fresh tomatoes and basil"))
        self.assertFalse(is_indonesian_caption(""))

    def test_block_detection(self):
        self.assertTrue(looks_like_block(b"<html>Please wait...</html>", 200))
        self.assertFalse(looks_like_block(b"<html>" + b"x" * 6000 + b"</html>", 200))
        self.assertTrue(looks_like_block(b"{}", 429))


if __name__ == "__main__":
    unittest.main()
