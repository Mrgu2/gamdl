import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from gamdl.cli.cli import main


class CliNetworkTests(unittest.TestCase):
    def tearDown(self) -> None:
        root_logger = logging.getLogger("gamdl")
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    def test_cli_passes_network_config_to_api_and_downloader(self):
        runner = CliRunner()
        fake_api = SimpleNamespace(
            storefront="us",
            language="en-US",
            active_subscription=True,
            account_restrictions=None,
            close=MagicMock(),
        )
        fake_base_downloader = SimpleNamespace(
            full_nm3u8dlre_path="N_m3u8DL-RE",
            full_ffmpeg_path="ffmpeg",
            full_mp4box_path="MP4Box",
            full_mp4decrypt_path="mp4decrypt",
        )
        fake_downloader = MagicMock()
        fake_downloader.get_url_info.return_value = None

        with (
            patch("gamdl.cli.cli.colorama.just_fix_windows_console"),
            patch("gamdl.cli.cli.prompt_path", return_value="/tmp/cookies.txt"),
            patch(
                "gamdl.cli.cli.AppleMusicApi.create_from_netscape_cookies",
                new=AsyncMock(return_value=fake_api),
            ) as create_api,
            patch("gamdl.cli.cli.ItunesApi", return_value=MagicMock()) as itunes_api_cls,
            patch(
                "gamdl.cli.cli.AppleMusicBaseDownloader",
                return_value=fake_base_downloader,
            ) as base_downloader_cls,
            patch("gamdl.cli.cli.AppleMusicSongDownloader", return_value=MagicMock()),
            patch(
                "gamdl.cli.cli.AppleMusicMusicVideoDownloader",
                return_value=MagicMock(),
            ),
            patch(
                "gamdl.cli.cli.AppleMusicUploadedVideoDownloader",
                return_value=MagicMock(),
            ),
            patch(
                "gamdl.cli.cli.AppleMusicDownloader",
                return_value=fake_downloader,
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "--no-config-file",
                    "--network-mode",
                    "custom",
                    "--proxy-url",
                    "http://127.0.0.1:7890",
                    "https://music.apple.com/us/song/test/1",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            create_api.await_args.kwargs["network_config"].mode,
            "custom",
        )
        self.assertEqual(
            create_api.await_args.kwargs["network_config"].proxy_url,
            "http://127.0.0.1:7890",
        )
        self.assertEqual(
            itunes_api_cls.call_args.kwargs["network_config"].proxy_url,
            "http://127.0.0.1:7890",
        )
        self.assertEqual(
            base_downloader_cls.call_args.kwargs["network_config"].proxy_url,
            "http://127.0.0.1:7890",
        )

    def test_cli_rejects_custom_mode_without_proxy_url(self):
        runner = CliRunner()

        with patch("gamdl.cli.cli.colorama.just_fix_windows_console"):
            result = runner.invoke(
                main,
                [
                    "--no-config-file",
                    "--network-mode",
                    "custom",
                    "https://music.apple.com/us/song/test/1",
                ],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("必须填写代理地址", result.output)

    def test_cli_closes_api_when_itunes_api_init_fails(self):
        runner = CliRunner()
        fake_api = SimpleNamespace(
            storefront="us",
            language="en-US",
            active_subscription=True,
            account_restrictions=None,
            close=AsyncMock(),
        )

        with (
            patch("gamdl.cli.cli.colorama.just_fix_windows_console"),
            patch("gamdl.cli.cli.prompt_path", return_value="/tmp/cookies.txt"),
            patch(
                "gamdl.cli.cli.AppleMusicApi.create_from_netscape_cookies",
                new=AsyncMock(return_value=fake_api),
            ),
            patch("gamdl.cli.cli.ItunesApi", side_effect=RuntimeError("itunes init failed")),
        ):
            result = runner.invoke(
                main,
                [
                    "--no-config-file",
                    "https://music.apple.com/us/song/test/1",
                ],
            )

        self.assertNotEqual(result.exit_code, 0)
        fake_api.close.assert_awaited_once()

    def test_cli_synced_lyrics_only_does_not_reference_music_video_dependency_list(self):
        runner = CliRunner()
        fake_api = SimpleNamespace(
            storefront="us",
            language="en-US",
            active_subscription=True,
            account_restrictions=None,
            close=AsyncMock(),
        )
        fake_base_downloader = SimpleNamespace(
            full_nm3u8dlre_path="N_m3u8DL-RE",
            full_ffmpeg_path="ffmpeg",
            full_mp4box_path="MP4Box",
            full_mp4decrypt_path="mp4decrypt",
        )
        fake_downloader = MagicMock()
        fake_downloader.get_url_info.return_value = None

        with (
            patch("gamdl.cli.cli.colorama.just_fix_windows_console"),
            patch("gamdl.cli.cli.prompt_path", return_value="/tmp/cookies.txt"),
            patch(
                "gamdl.cli.cli.AppleMusicApi.create_from_netscape_cookies",
                new=AsyncMock(return_value=fake_api),
            ),
            patch("gamdl.cli.cli.ItunesApi", return_value=MagicMock()),
            patch(
                "gamdl.cli.cli.AppleMusicBaseDownloader",
                return_value=fake_base_downloader,
            ),
            patch("gamdl.cli.cli.AppleMusicSongDownloader", return_value=MagicMock()),
            patch(
                "gamdl.cli.cli.AppleMusicMusicVideoDownloader",
                return_value=MagicMock(),
            ),
            patch(
                "gamdl.cli.cli.AppleMusicUploadedVideoDownloader",
                return_value=MagicMock(),
            ),
            patch(
                "gamdl.cli.cli.AppleMusicDownloader",
                return_value=fake_downloader,
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "--no-config-file",
                    "--synced-lyrics-only",
                    "https://music.apple.com/us/song/test/1",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)

    def test_cli_cleans_up_handlers_after_each_run(self):
        runner = CliRunner()
        fake_api = SimpleNamespace(
            storefront="us",
            language="en-US",
            active_subscription=True,
            account_restrictions=None,
            close=AsyncMock(),
        )
        fake_base_downloader = SimpleNamespace(
            full_nm3u8dlre_path="N_m3u8DL-RE",
            full_ffmpeg_path="ffmpeg",
            full_mp4box_path="MP4Box",
            full_mp4decrypt_path="mp4decrypt",
        )
        fake_downloader = MagicMock()
        fake_downloader.get_url_info.return_value = None

        with (
            patch("gamdl.cli.cli.colorama.just_fix_windows_console"),
            patch("gamdl.cli.cli.prompt_path", return_value="/tmp/cookies.txt"),
            patch(
                "gamdl.cli.cli.AppleMusicApi.create_from_netscape_cookies",
                new=AsyncMock(return_value=fake_api),
            ),
            patch("gamdl.cli.cli.ItunesApi", return_value=MagicMock()),
            patch(
                "gamdl.cli.cli.AppleMusicBaseDownloader",
                return_value=fake_base_downloader,
            ),
            patch("gamdl.cli.cli.AppleMusicSongDownloader", return_value=MagicMock()),
            patch(
                "gamdl.cli.cli.AppleMusicMusicVideoDownloader",
                return_value=MagicMock(),
            ),
            patch(
                "gamdl.cli.cli.AppleMusicUploadedVideoDownloader",
                return_value=MagicMock(),
            ),
            patch(
                "gamdl.cli.cli.AppleMusicDownloader",
                return_value=fake_downloader,
            ),
        ):
            first = runner.invoke(main, ["--no-config-file", "https://music.apple.com/us/song/test/1"])
            second = runner.invoke(main, ["--no-config-file", "https://music.apple.com/us/song/test/1"])

        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertEqual(logging.getLogger("gamdl").handlers, [])

    def test_cli_reports_invalid_wrapper_account_url_as_user_error(self):
        runner = CliRunner()

        with patch("gamdl.cli.cli.colorama.just_fix_windows_console"):
            result = runner.invoke(
                main,
                [
                    "--no-config-file",
                    "--use-wrapper",
                    "--wrapper-account-url",
                    "http://192.168.1.8:30020/",
                    "https://music.apple.com/us/song/test/1",
                ],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Wrapper account API 地址必须是本机回环地址", result.output)


if __name__ == "__main__":
    unittest.main()
