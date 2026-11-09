import unittest

from src.enrich_instagram import media_extra, parse_metric, parse_profile_followers


class EnrichmentTest(unittest.TestCase):
    def test_parse_metric(self):
        self.assertEqual(parse_metric("686M"), 686_000_000)
        self.assertEqual(parse_metric("58K"), 58_000)
        self.assertEqual(parse_metric("1.234"), 1_234)
        self.assertEqual(parse_metric("1,2 jt"), 1_200_000)
        self.assertEqual(parse_metric("31"), 31)

    def test_parse_profile_followers(self):
        body = (
            '<meta property="og:description" '
            'content="686M Pengikut, 170 Mengikuti, 8,639 Postingan">'
        )
        self.assertEqual(parse_profile_followers(body), 686_000_000)

    def test_media_extra(self):
        payload = {
            "items": [
                {
                    "code": "ABC123",
                    "video_duration": 12.5,
                    "play_count": 1000,
                    "like_count": 50,
                    "comment_count": 5,
                    "user": {"username": "chef"},
                    "clips_metadata": {
                        "audio_type": "original_sounds",
                        "original_sound_info": {
                            "audio_asset_id": "audio1",
                            "original_audio_title": "Original sound",
                        },
                    },
                }
            ]
        }
        extra = media_extra("123", payload)
        self.assertIsNotNone(extra)
        self.assertEqual(extra["video_id"], "ABC123")
        self.assertEqual(extra["duration_sec"], 12.5)
        self.assertEqual(extra["views"], 1000)
        self.assertEqual(extra["sound_id"], "audio1")
        self.assertTrue(extra["is_original_sound"])


if __name__ == "__main__":
    unittest.main()
