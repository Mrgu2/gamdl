import unittest
import inspect
from unittest.mock import AsyncMock, patch

from gamdl.network import NetworkConfig
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

    @patch("gamdl.downloader.downloader_base.YoutubeDL")
    def test_download_ytdlp_passes_empty_proxy_in_direct_mode(self, ytdlp_cls):
        downloader = AppleMusicBaseDownloader.__new__(AppleMusicBaseDownloader)
        downloader.network_config = NetworkConfig(mode="direct", proxy_url="")
        downloader.silent = False

        downloader._download_ytdlp("https://example.com/stream.m3u8", "/tmp/out.m4a")

        options = ytdlp_cls.call_args.args[0]
        self.assertEqual(options["proxy"], "")

    @patch("gamdl.downloader.downloader_base.YoutubeDL")
    def test_download_ytdlp_passes_custom_proxy(self, ytdlp_cls):
        downloader = AppleMusicBaseDownloader.__new__(AppleMusicBaseDownloader)
        downloader.network_config = NetworkConfig(mode="custom", proxy_url="http://127.0.0.1:7890")
        downloader.silent = True

        downloader._download_ytdlp("https://example.com/stream.m3u8", "/tmp/out.m4a")

        options = ytdlp_cls.call_args.args[0]
        self.assertEqual(options["proxy"], "http://127.0.0.1:7890")

    @patch("gamdl.downloader.downloader_base.async_subprocess", new_callable=AsyncMock)
    def test_download_nm3u8dlre_passes_network_config(self, async_subprocess_mock):
        downloader = AppleMusicBaseDownloader.__new__(AppleMusicBaseDownloader)
        downloader.full_nm3u8dlre_path = "N_m3u8DL-RE"
        downloader.full_ffmpeg_path = "ffmpeg"
        downloader.silent = True
        downloader.network_config = NetworkConfig(mode="custom", proxy_url="http://127.0.0.1:7890")

        import asyncio

        asyncio.run(
            downloader.download_nm3u8dlre(
                "https://example.com/stream.m3u8",
                "/tmp/test/out.m4a",
            )
        )

        self.assertEqual(
            async_subprocess_mock.call_args.kwargs["network_config"],
            downloader.network_config,
        )
