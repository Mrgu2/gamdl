import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gamdl.interface import AppleMusicInterface, AppleMusicSongInterface, SongCodec
from gamdl.network import NetworkConfig


class InterfaceNetworkPropagationTests(unittest.TestCase):
    def test_cover_bytes_uses_api_network_config(self):
        interface = AppleMusicInterface.__new__(AppleMusicInterface)
        interface.apple_music_api = SimpleNamespace(
            network_config=NetworkConfig(mode="custom", proxy_url="http://127.0.0.1:7890")
        )

        with patch("gamdl.interface.interface.get_response", new=AsyncMock()) as get_response_mock:
            get_response_mock.return_value = SimpleNamespace(status_code=404, content=b"")

            asyncio.run(interface.get_cover_bytes("https://example.com/cover.jpg"))

        self.assertEqual(
            get_response_mock.await_args.kwargs["network_config"],
            interface.apple_music_api.network_config,
        )

    def test_song_stream_requests_use_api_network_config(self):
        interface = AppleMusicSongInterface.__new__(AppleMusicSongInterface)
        interface.apple_music_api = SimpleNamespace(
            network_config=NetworkConfig(mode="direct", proxy_url=""),
        )
        interface._get_playlist_from_codec = lambda data, codec: {
            "uri": "audio.m3u8",
            "stream_info": {
                "codecs": "mp4a.40.2",
                "stable_variant_id": "v1",
                "audio": "audio-stereo-256",
                "average_bandwidth": 256000,
            },
        }
        interface._get_audio_session_key_metadata = lambda data: None
        interface._get_drm_uri_from_m3u8_keys = lambda m3u8_obj, drm_key: f"{drm_key}-uri"

        responses = [
            SimpleNamespace(
                text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=256000,AVERAGE-BANDWIDTH=256000,CODECS=\"mp4a.40.2\",AUDIO=\"audio-stereo-256\",STABLE-VARIANT-ID=\"v1\"\naudio.m3u8\n"
            ),
            SimpleNamespace(
                text="#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"skd://fairplay\",KEYFORMAT=\"com.apple.streamingkeydelivery\"\n"
            ),
        ]

        with patch("gamdl.interface.interface.get_response", new=AsyncMock(side_effect=responses)) as get_response_mock:
            asyncio.run(
                interface._get_stream_info(
                    {"attributes": {"extendedAssetUrls": {"enhancedHls": "https://example.com/master.m3u8"}}},
                    SongCodec.AAC,
                )
            )

        self.assertEqual(get_response_mock.await_count, 2)
        for call in get_response_mock.await_args_list:
            self.assertEqual(call.kwargs["network_config"], interface.apple_music_api.network_config)

    def test_song_stream_uses_webplayback_m3u8_master_url_before_metadata(self):
        interface = AppleMusicSongInterface.__new__(AppleMusicSongInterface)
        interface.apple_music_api = SimpleNamespace(
            network_config=NetworkConfig(mode="direct", proxy_url=""),
            get_song=AsyncMock(),
        )
        interface._get_playlist_from_codec = lambda data, codec: {
            "uri": "audio.m3u8",
            "stream_info": {
                "codecs": "mp4a.40.2",
                "stable_variant_id": "v1",
                "audio": "audio-stereo-256",
                "average_bandwidth": 256000,
            },
        }
        interface._get_audio_session_key_metadata = lambda data: None
        interface._get_drm_uri_from_m3u8_keys = lambda m3u8_obj, drm_key: f"{drm_key}-uri"

        responses = [
            SimpleNamespace(
                text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=256000,AVERAGE-BANDWIDTH=256000,CODECS=\"mp4a.40.2\",AUDIO=\"audio-stereo-256\",STABLE-VARIANT-ID=\"v1\"\naudio.m3u8\n"
            ),
            SimpleNamespace(
                text="#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"skd://fairplay\",KEYFORMAT=\"com.apple.streamingkeydelivery\"\n"
            ),
        ]

        with patch("gamdl.interface.interface.get_response", new=AsyncMock(side_effect=responses)) as get_response_mock:
            stream_info = asyncio.run(
                interface.get_stream_info(
                    SongCodec.AAC,
                    {"id": "song-123", "attributes": {}},
                    {
                        "songList": [
                            {
                                "hls-playlist-url": (
                                    "https://example.com/P123_variant.m3u8"
                                )
                            }
                        ]
                    },
                )
            )

        self.assertIsNotNone(stream_info)
        interface.apple_music_api.get_song.assert_not_called()
        self.assertEqual(
            get_response_mock.await_args_list[0].args[0],
            "https://example.com/P123_default.m3u8",
        )

    def test_library_song_stream_info_is_drm_free(self):
        interface = AppleMusicSongInterface.__new__(AppleMusicSongInterface)

        stream_info = asyncio.run(
            interface.get_stream_info(
                SongCodec.AAC_LEGACY,
                {"id": "i.library-song", "type": "library-songs", "attributes": {}},
                {
                    "songList": [
                        {
                            "songId": "i.library-song",
                            "assets": [{"URL": "https://example.com/library.m4a"}],
                        }
                    ]
                },
                is_library=True,
            )
        )

        self.assertEqual(stream_info.media_id, "i.library-song")
        self.assertEqual(stream_info.audio_track.stream_url, "https://example.com/library.m4a")
        self.assertTrue(stream_info.audio_track.drm_free)


if __name__ == "__main__":
    unittest.main()
