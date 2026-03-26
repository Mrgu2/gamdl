import unittest
import inspect

from gamdl.downloader.downloader_base import AppleMusicBaseDownloader


class AppleMusicBaseDownloaderTests(unittest.TestCase):
    def test_output_path_default_is_machine_neutral(self):
        self.assertEqual(
            AppleMusicBaseDownloader.__init__.__defaults__[0],
            "./Apple Music",
        )

    def test_save_cover_default_is_enabled(self):
        self.assertTrue(AppleMusicBaseDownloader.__init__.__defaults__[4])

    def test_cover_size_default_uses_max_available(self):
        self.assertIsNone(
            inspect.signature(AppleMusicBaseDownloader.__init__)
            .parameters["cover_size"]
            .default
        )

    def test_wrapper_m3u8_ip_uses_matching_port_offset(self):
        downloader = AppleMusicBaseDownloader.__new__(AppleMusicBaseDownloader)
        downloader.wrapper_decrypt_ip = "127.0.0.1:10020"
        self.assertEqual(downloader.get_wrapper_m3u8_ip(), "127.0.0.1:20020")

    def test_wrapper_m3u8_ip_tracks_custom_decrypt_port(self):
        downloader = AppleMusicBaseDownloader.__new__(AppleMusicBaseDownloader)
        downloader.wrapper_decrypt_ip = "127.0.0.1:10022"
        self.assertEqual(downloader.get_wrapper_m3u8_ip(), "127.0.0.1:20022")

    def test_wrapper_m3u8_ip_defaults_host_for_port_only_input(self):
        downloader = AppleMusicBaseDownloader.__new__(AppleMusicBaseDownloader)
        downloader.wrapper_decrypt_ip = "10022"
        self.assertEqual(downloader.get_wrapper_m3u8_ip(), "127.0.0.1:20022")
