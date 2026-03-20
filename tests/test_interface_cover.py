import unittest

from gamdl.interface.enums import CoverFormat
from gamdl.interface.interface import AppleMusicInterface


class AppleMusicInterfaceCoverTests(unittest.TestCase):
    def setUp(self):
        self.interface = AppleMusicInterface.__new__(AppleMusicInterface)
        self.metadata = {
            "attributes": {
                "artwork": {
                    "url": "https://is1-ssl.mzstatic.com/image/thumb/foo/{w}x{h}bb.jpg",
                    "width": 3000,
                    "height": 3000,
                }
            }
        }

    def test_cover_url_defaults_to_max_available_size(self):
        cover_url = self.interface.get_cover_url(
            self.metadata,
            self.metadata["attributes"]["artwork"]["url"],
            None,
            CoverFormat.JPG,
        )
        self.assertIn("/3000x3000bb.jpg", cover_url)

    def test_cover_url_caps_requested_size_to_available_size(self):
        cover_url = self.interface.get_cover_url(
            self.metadata,
            self.metadata["attributes"]["artwork"]["url"],
            4000,
            CoverFormat.JPG,
        )
        self.assertIn("/3000x3000bb.jpg", cover_url)
