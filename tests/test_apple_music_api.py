import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from gamdl.api.apple_music_api import (
    WRAPPER_ACCOUNT_URL_ERROR,
    _matches_apple_music_cookie_domain,
    normalize_wrapper_account_url,
    AppleMusicApi,
)


def _jwt_part(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _fake_jwt(header: dict | None = None, payload: dict | None = None) -> str:
    return ".".join(
        [
            _jwt_part(header or {"typ": "JWT", "alg": "ES256", "kid": "WebPlayKid"}),
            _jwt_part(payload or {"iss": "AMPWebPlay", "iat": 1, "exp": 9999999999}),
            "signature",
        ]
    )


class AppleMusicApiCookieTests(unittest.TestCase):
    def test_accepts_music_domain_cookie(self):
        self.assertTrue(_matches_apple_music_cookie_domain(".music.apple.com"))

    def test_accepts_apple_domain_cookie(self):
        self.assertTrue(_matches_apple_music_cookie_domain(".apple.com"))

    def test_rejects_non_apple_domain_cookie(self):
        self.assertFalse(_matches_apple_music_cookie_domain(".example.com"))

    def test_create_from_netscape_cookies_accepts_apple_domain(self):
        cookie_file = tempfile.NamedTemporaryFile("w", delete=False)
        try:
            cookie_file.write("# Netscape HTTP Cookie File\n")
            cookie_file.write(
                ".apple.com\tTRUE\t/\tTRUE\t2147483647\tmedia-user-token\ttest-token\n"
            )
            cookie_file.close()

            async def run():
                original_create = AppleMusicApi.create

                async def fake_create(*args, **kwargs):
                    return kwargs

                AppleMusicApi.create = fake_create
                try:
                    return await AppleMusicApi.create_from_netscape_cookies(
                        cookie_file.name
                    )
                finally:
                    AppleMusicApi.create = original_create

            result = asyncio.run(run())
            self.assertEqual(result["media_user_token"], "test-token")
        finally:
            Path(cookie_file.name).unlink(missing_ok=True)

    def test_get_token_does_not_log_sensitive_token_value(self):
        token = _fake_jwt()

        class FakeClient:
            async def get(self, url):
                if url == "https://music.apple.com":
                    return SimpleNamespace(text='<script src="/assets/index-legacy~test.js"></script>')
                return SimpleNamespace(text=f'"{token}"')

        api = AppleMusicApi(
            storefront="us",
            language="en-US",
            media_user_token=None,
        )
        api.client = FakeClient()

        with self.assertNoLogs("gamdl.api.apple_music_api", level="DEBUG"):
            result = asyncio.run(api._get_token())

        self.assertEqual(result, token)

    def test_get_token_checks_modern_index_bundle_before_legacy_bundle(self):
        token = _fake_jwt()

        class FakeClient:
            def __init__(self):
                self.urls = []

            async def get(self, url):
                self.urls.append(url)
                if url == "https://music.apple.com":
                    return SimpleNamespace(
                        text=(
                            '<script src="/assets/index~modern.js"></script>'
                            '<script src="/assets/index-legacy~old.js"></script>'
                        )
                    )
                if url == "https://music.apple.com/assets/index~modern.js":
                    return SimpleNamespace(text=f'const developerToken="{token}";')
                return SimpleNamespace(text="")

        api = AppleMusicApi(
            storefront="us",
            language="en-US",
            media_user_token=None,
        )
        api.client = FakeClient()

        result = asyncio.run(api._get_token())

        self.assertEqual(result, token)
        self.assertEqual(
            api.client.urls,
            [
                "https://music.apple.com",
                "https://music.apple.com/assets/index~modern.js",
            ],
        )

    def test_get_webplayback_uses_universal_library_id_for_library_media(self):
        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {"songList": []}

        class FakeClient:
            def __init__(self):
                self.json = None

            async def post(self, url, json):
                self.json = json
                return FakeResponse()

        api = AppleMusicApi(
            storefront="us",
            language="en-US",
            media_user_token=None,
        )
        api.client = FakeClient()

        result = asyncio.run(api.get_webplayback("i.library-song", is_library=True))

        self.assertEqual(result, {"songList": []})
        self.assertEqual(
            api.client.json,
            {
                "language": "en-US",
                "universalLibraryId": "i.library-song",
            },
        )

    def test_extended_api_data_forwards_next_offset_without_using_it_as_limit(self):
        api = AppleMusicApi(
            storefront="us",
            language="en-US",
            media_user_token=None,
        )
        calls = []

        async def fake_amp_request(endpoint, params):
            calls.append((endpoint, params))
            return {"data": []}

        api._amp_request = fake_amp_request

        result = asyncio.run(
            api._get_extended_api_data(
                "/v1/catalog/us/albums/1/tracks?offset=300&include=catalog",
                "/v1/catalog/us/albums/1/tracks?limit=100",
                "extendedAssetUrls",
            )
        )

        self.assertEqual(result, {"data": []})
        self.assertEqual(
            calls,
            [
                (
                    "/v1/catalog/us/albums/1/tracks",
                    {
                        "limit": "100",
                        "offset": "300",
                        "include": "catalog",
                        "extend": "extendedAssetUrls",
                    },
                )
            ],
        )

    def test_normalize_wrapper_account_url_accepts_loopback(self):
        self.assertEqual(
            normalize_wrapper_account_url("http://127.0.0.1:30020"),
            "http://127.0.0.1:30020/",
        )
        self.assertEqual(
            normalize_wrapper_account_url("http://localhost:30020/account"),
            "http://localhost:30020/account/",
        )

    def test_normalize_wrapper_account_url_rejects_non_loopback(self):
        with self.assertRaisesRegex(ValueError, WRAPPER_ACCOUNT_URL_ERROR):
            normalize_wrapper_account_url("http://192.168.1.8:30020/")

    def test_normalize_wrapper_account_url_rejects_invalid_port_with_consistent_error(self):
        with self.assertRaisesRegex(ValueError, WRAPPER_ACCOUNT_URL_ERROR):
            normalize_wrapper_account_url("http://127.0.0.1:99999/")


if __name__ == "__main__":
    unittest.main()
