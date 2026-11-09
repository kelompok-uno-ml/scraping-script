import unittest
from types import SimpleNamespace

from src.scrape_instagram import cookie_names, is_logged_in, page_is_login_wall


class InstagramSessionTest(unittest.TestCase):
    def test_cookie_names_from_tuple(self):
        self.assertEqual(cookie_names((("sessionid", "abc"), ("csrftoken", "x"))), {"sessionid", "csrftoken"})
        self.assertEqual(cookie_names({"sessionid": "abc"}), {"sessionid"})
        self.assertEqual(cookie_names(None), set())

    def test_logged_in_with_session_cookie(self):
        page = SimpleNamespace(url="https://www.instagram.com/", cookies=(("sessionid", "abc"),), body=b"<html></html>", status=200)
        self.assertTrue(is_logged_in(page))

    def test_login_wall_by_url(self):
        page = SimpleNamespace(url="https://www.instagram.com/accounts/login/", cookies=(), body=b"", status=200)
        self.assertTrue(page_is_login_wall(page))


if __name__ == "__main__":
    unittest.main()
