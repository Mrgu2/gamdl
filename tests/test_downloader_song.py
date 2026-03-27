import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gamdl.downloader import AppleMusicBaseDownloader, AppleMusicDownloader, AppleMusicSongDownloader
from gamdl.downloader.exceptions import FormatNotAvailable
from gamdl.interface import SongCodec
from gamdl.interface.types import MediaTags


class AppleMusicSongDownloaderTests(unittest.TestCase):
    def _build_base_downloader(self, tempdir: str) -> AppleMusicBaseDownloader:
        with patch.object(AppleMusicBaseDownloader, "initialize", return_value=None):
            downloader = AppleMusicBaseDownloader(
                output_path=str(Path(tempdir) / "downloads"),
                temp_path=str(Path(tempdir) / "tmp"),
            )
        downloader.cdm = object()
        return downloader

    def test_get_download_item_raises_format_not_available_when_no_stream_info(self):
        with tempfile.TemporaryDirectory() as tempdir:
            base_downloader = self._build_base_downloader(tempdir)
            interface = SimpleNamespace(
                get_media_id_of_library_media=MagicMock(return_value="song-123"),
                get_lyrics=AsyncMock(return_value=None),
                apple_music_api=SimpleNamespace(
                    get_webplayback=AsyncMock(return_value={"songList": [{"songId": "song-123"}]})
                ),
                get_tags=AsyncMock(
                    return_value=MediaTags(
                        album="Album",
                        artist="Artist",
                        title="Track Title",
                        track=1,
                    )
                ),
                get_stream_info=AsyncMock(return_value=None),
            )
            downloader = AppleMusicSongDownloader(
                base_downloader=base_downloader,
                interface=interface,
                codec_priority=[SongCodec.AAC_LEGACY],
            )
            song_metadata = {
                "id": "song-123",
                "type": "songs",
                "attributes": {"playParams": {"id": "song-123"}},
            }

            with self.assertRaisesRegex(
                FormatNotAvailable,
                "Requested format is not available for media ID: song-123",
            ):
                asyncio.run(downloader.get_download_item(song_metadata))

    def test_single_download_item_captures_format_not_available_error(self):
        song_metadata = {
            "id": "song-123",
            "type": "songs",
            "attributes": {"name": "Track Title", "playParams": {"id": "song-123"}},
        }
        base_downloader = SimpleNamespace(is_media_streamable=MagicMock(return_value=True))
        song_downloader = SimpleNamespace(
            get_download_item=AsyncMock(side_effect=FormatNotAvailable("song-123"))
        )
        downloader = AppleMusicDownloader(
            interface=MagicMock(),
            base_downloader=base_downloader,
            song_downloader=song_downloader,
            music_video_downloader=None,
            uploaded_video_downloader=None,
        )

        result = asyncio.run(downloader.get_single_download_item_no_filter(song_metadata))

        self.assertIsInstance(result.error, FormatNotAvailable)
        self.assertEqual(
            str(result.error),
            "Requested format is not available for media ID: song-123",
        )


if __name__ == "__main__":
    unittest.main()
