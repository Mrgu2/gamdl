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


if __name__ == "__main__":
    unittest.main()
