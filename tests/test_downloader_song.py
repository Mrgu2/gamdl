import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gamdl.downloader import AppleMusicBaseDownloader, AppleMusicDownloader, AppleMusicSongDownloader
from gamdl.downloader.exceptions import FormatNotAvailable, MediaFileExists
from gamdl.downloader.types import DownloadItem
from gamdl.interface import SongCodec
from gamdl.interface.types import Lyrics, MediaTags, PlaylistTags


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

    def test_download_failure_does_not_write_sidecars_or_playlist_file(self):
        interface = SimpleNamespace(get_cover_bytes=AsyncMock(return_value=b"cover"))
        base_downloader = SimpleNamespace(
            overwrite=False,
            save_cover=True,
            save_playlist=True,
            write_cover_image=MagicMock(),
            update_playlist_file=MagicMock(),
            move_to_final_path=MagicMock(),
            cleanup_temp=MagicMock(),
        )
        song_downloader = SimpleNamespace(
            synced_lyrics_only=False,
            no_synced_lyrics=False,
            write_synced_lyrics=MagicMock(),
        )
        downloader = AppleMusicDownloader(
            interface=interface,
            base_downloader=base_downloader,
            song_downloader=song_downloader,
            music_video_downloader=None,
            uploaded_video_downloader=None,
        )
        download_item = DownloadItem(
            media_metadata={"id": "song-123", "type": "song"},
            lyrics=Lyrics(synced="[00:00.00]test"),
            playlist_tags=PlaylistTags(
                playlist_artist="Foo",
                playlist_id=1,
                playlist_title="Bar",
                playlist_track=2,
            ),
            random_uuid="abc123",
            final_path="/tmp/Track.m4a",
            cover_path="/tmp/Cover.jpg",
            cover_url="https://example.com/cover.jpg",
            synced_lyrics_path="/tmp/Track.lrc",
            playlist_file_path="/tmp/Playlist.m3u8",
        )

        with patch.object(downloader, "_download", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                asyncio.run(downloader.download(download_item))

        base_downloader.write_cover_image.assert_not_called()
        song_downloader.write_synced_lyrics.assert_not_called()
        base_downloader.update_playlist_file.assert_not_called()
        base_downloader.cleanup_temp.assert_called_once_with("abc123")

    def test_successful_download_writes_sidecars_and_playlist_after_media_exists(self):
        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            final_path = temp_path / "Track.m4a"
            staged_path = temp_path / "Track.staged.m4a"
            staged_path.write_text("audio", encoding="utf-8")
            cover_path = temp_path / "Cover.jpg"
            lyrics_path = temp_path / "Track.lrc"
            playlist_path = temp_path / "Playlist.m3u8"

            interface = SimpleNamespace(get_cover_bytes=AsyncMock(return_value=b"cover"))
            base_downloader = SimpleNamespace(
                overwrite=False,
                save_cover=True,
                save_playlist=True,
                write_cover_image=MagicMock(),
                update_playlist_file=MagicMock(),
                cleanup_temp=MagicMock(),
            )

            def move_to_final_path(source, target):
                Path(target).write_text(Path(source).read_text(encoding="utf-8"), encoding="utf-8")
                Path(source).unlink()

            base_downloader.move_to_final_path = MagicMock(side_effect=move_to_final_path)
            song_downloader = SimpleNamespace(
                synced_lyrics_only=False,
                no_synced_lyrics=False,
                write_synced_lyrics=MagicMock(),
            )
            downloader = AppleMusicDownloader(
                interface=interface,
                base_downloader=base_downloader,
                song_downloader=song_downloader,
                music_video_downloader=None,
                uploaded_video_downloader=None,
            )
            download_item = DownloadItem(
                media_metadata={"id": "song-123", "type": "song"},
                lyrics=Lyrics(synced="[00:00.00]test"),
                playlist_tags=PlaylistTags(
                    playlist_artist="Foo",
                    playlist_id=1,
                    playlist_title="Bar",
                    playlist_track=2,
                ),
                random_uuid="abc123",
                staged_path=str(staged_path),
                final_path=str(final_path),
                cover_path=str(cover_path),
                cover_url="https://example.com/cover.jpg",
                synced_lyrics_path=str(lyrics_path),
                playlist_file_path=str(playlist_path),
            )

            with patch.object(downloader, "_download", new=AsyncMock(return_value=None)):
                asyncio.run(downloader.download(download_item))

            self.assertTrue(final_path.exists())
            base_downloader.move_to_final_path.assert_called_once()
            base_downloader.write_cover_image.assert_called_once_with(b"cover", str(cover_path))
            song_downloader.write_synced_lyrics.assert_called_once_with(
                "[00:00.00]test",
                str(lyrics_path),
            )
            base_downloader.update_playlist_file.assert_called_once_with(
                str(playlist_path),
                str(final_path),
                2,
            )

    def test_existing_media_skip_still_updates_playlist_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            final_path = temp_path / "Track.m4a"
            final_path.write_text("audio", encoding="utf-8")
            cover_path = temp_path / "Cover.jpg"
            lyrics_path = temp_path / "Track.lrc"
            playlist_path = temp_path / "Playlist.m3u8"

            interface = SimpleNamespace(get_cover_bytes=AsyncMock(return_value=b"cover"))
            base_downloader = SimpleNamespace(
                overwrite=False,
                save_cover=True,
                save_playlist=True,
                write_cover_image=MagicMock(),
                update_playlist_file=MagicMock(),
                move_to_final_path=MagicMock(),
                cleanup_temp=MagicMock(),
            )
            song_downloader = SimpleNamespace(
                synced_lyrics_only=False,
                no_synced_lyrics=False,
                write_synced_lyrics=MagicMock(),
            )
            downloader = AppleMusicDownloader(
                interface=interface,
                base_downloader=base_downloader,
                song_downloader=song_downloader,
                music_video_downloader=None,
                uploaded_video_downloader=None,
            )
            download_item = DownloadItem(
                media_metadata={"id": "song-123", "type": "song"},
                lyrics=Lyrics(synced="[00:00.00]test"),
                playlist_tags=PlaylistTags(
                    playlist_artist="Foo",
                    playlist_id=1,
                    playlist_title="Bar",
                    playlist_track=2,
                ),
                random_uuid="abc123",
                final_path=str(final_path),
                cover_path=str(cover_path),
                cover_url="https://example.com/cover.jpg",
                synced_lyrics_path=str(lyrics_path),
                playlist_file_path=str(playlist_path),
            )

            with self.assertRaises(MediaFileExists):
                asyncio.run(downloader.download(download_item))

            base_downloader.write_cover_image.assert_called_once_with(b"cover", str(cover_path))
            song_downloader.write_synced_lyrics.assert_called_once_with(
                "[00:00.00]test",
                str(lyrics_path),
            )
            base_downloader.update_playlist_file.assert_called_once_with(
                str(playlist_path),
                str(final_path),
                2,
            )

    def test_sidecar_failure_does_not_fail_successful_media_download(self):
        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            final_path = temp_path / "Track.m4a"
            staged_path = temp_path / "Track.staged.m4a"
            staged_path.write_text("audio", encoding="utf-8")
            cover_path = temp_path / "Cover.jpg"
            lyrics_path = temp_path / "Track.lrc"
            playlist_path = temp_path / "Playlist.m3u8"

            interface = SimpleNamespace(get_cover_bytes=AsyncMock(return_value=b"cover"))
            base_downloader = SimpleNamespace(
                overwrite=False,
                save_cover=True,
                save_playlist=True,
                update_playlist_file=MagicMock(),
                cleanup_temp=MagicMock(),
            )

            def move_to_final_path(source, target):
                Path(target).write_text(Path(source).read_text(encoding="utf-8"), encoding="utf-8")
                Path(source).unlink()

            base_downloader.move_to_final_path = MagicMock(side_effect=move_to_final_path)
            base_downloader.write_cover_image = MagicMock(side_effect=RuntimeError("cover boom"))
            song_downloader = SimpleNamespace(
                synced_lyrics_only=False,
                no_synced_lyrics=False,
                write_synced_lyrics=MagicMock(),
            )
            downloader = AppleMusicDownloader(
                interface=interface,
                base_downloader=base_downloader,
                song_downloader=song_downloader,
                music_video_downloader=None,
                uploaded_video_downloader=None,
            )
            download_item = DownloadItem(
                media_metadata={"id": "song-123", "type": "song", "attributes": {"name": "Track 1"}},
                lyrics=Lyrics(synced="[00:00.00]test"),
                playlist_tags=PlaylistTags(
                    playlist_artist="Foo",
                    playlist_id=1,
                    playlist_title="Bar",
                    playlist_track=2,
                ),
                random_uuid="abc123",
                staged_path=str(staged_path),
                final_path=str(final_path),
                cover_path=str(cover_path),
                cover_url="https://example.com/cover.jpg",
                synced_lyrics_path=str(lyrics_path),
                playlist_file_path=str(playlist_path),
            )

            with (
                patch.object(downloader, "_download", new=AsyncMock(return_value=None)),
                self.assertLogs("gamdl.downloader", level="WARNING") as logs,
            ):
                result = asyncio.run(downloader.download(download_item))

            self.assertIs(result, download_item)
            self.assertTrue(final_path.exists())
            song_downloader.write_synced_lyrics.assert_called_once_with(
                "[00:00.00]test",
                str(lyrics_path),
            )
            base_downloader.update_playlist_file.assert_called_once_with(
                str(playlist_path),
                str(final_path),
                2,
            )
            self.assertTrue(any("cover" in line and "Track 1" in line for line in logs.output))

    def test_sidecar_failure_does_not_override_media_file_exists_skip(self):
        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            final_path = temp_path / "Track.m4a"
            final_path.write_text("audio", encoding="utf-8")
            cover_path = temp_path / "Cover.jpg"
            lyrics_path = temp_path / "Track.lrc"
            playlist_path = temp_path / "Playlist.m3u8"

            interface = SimpleNamespace(get_cover_bytes=AsyncMock(return_value=b"cover"))
            base_downloader = SimpleNamespace(
                overwrite=False,
                save_cover=True,
                save_playlist=True,
                update_playlist_file=MagicMock(),
                move_to_final_path=MagicMock(),
                cleanup_temp=MagicMock(),
            )
            base_downloader.write_cover_image = MagicMock(side_effect=RuntimeError("cover boom"))
            song_downloader = SimpleNamespace(
                synced_lyrics_only=False,
                no_synced_lyrics=False,
                write_synced_lyrics=MagicMock(),
            )
            downloader = AppleMusicDownloader(
                interface=interface,
                base_downloader=base_downloader,
                song_downloader=song_downloader,
                music_video_downloader=None,
                uploaded_video_downloader=None,
            )
            download_item = DownloadItem(
                media_metadata={"id": "song-123", "type": "song", "attributes": {"name": "Track 1"}},
                lyrics=Lyrics(synced="[00:00.00]test"),
                playlist_tags=PlaylistTags(
                    playlist_artist="Foo",
                    playlist_id=1,
                    playlist_title="Bar",
                    playlist_track=2,
                ),
                random_uuid="abc123",
                final_path=str(final_path),
                cover_path=str(cover_path),
                cover_url="https://example.com/cover.jpg",
                synced_lyrics_path=str(lyrics_path),
                playlist_file_path=str(playlist_path),
            )

            with self.assertLogs("gamdl.downloader", level="WARNING") as logs:
                with self.assertRaises(MediaFileExists):
                    asyncio.run(downloader.download(download_item))

            song_downloader.write_synced_lyrics.assert_called_once_with(
                "[00:00.00]test",
                str(lyrics_path),
            )
            base_downloader.update_playlist_file.assert_called_once_with(
                str(playlist_path),
                str(final_path),
                2,
            )
            self.assertTrue(any("cover" in line and "Track 1" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
