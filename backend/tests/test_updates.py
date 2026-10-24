from __future__ import annotations

import unittest

from app.updates import is_version_newer, release_to_deb_info


class UpdateTests(unittest.TestCase):
    def test_semver_compare(self):
        self.assertTrue(is_version_newer("0.2.0", "0.1.0"))
        self.assertFalse(is_version_newer("0.1.0", "0.1.0"))
        self.assertFalse(is_version_newer("0.1.0", "v0.2.0"))

    def test_public_release_uses_browser_url(self):
        info = release_to_deb_info(
            {
                "tag_name": "v0.2.0",
                "assets": [
                    {
                        "name": "DigitizeMyBook-0.2.0-amd64.deb",
                        "browser_download_url": "https://github.com/Anigerm/DigitizeMyBook/releases/download/v0.2.0/DigitizeMyBook-0.2.0-amd64.deb",
                        "size": 12,
                    }
                ],
            }
        )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.version, "0.2.0")
        self.assertTrue(info.deb_url.startswith("https://github.com/"))
        self.assertNotIn("api.github.com", info.deb_url)
