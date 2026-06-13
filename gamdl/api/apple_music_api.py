import base64
import json
import logging
import re
import typing
from http.cookiejar import MozillaCookieJar
from ipaddress import ip_address
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from ..network import NetworkConfig, httpx_client_kwargs
from ..utils import get_response, raise_for_status, safe_json
from .constants import (
    AMP_API_URL,
    APPLE_MUSIC_COOKIE_DOMAIN,
    APPLE_MUSIC_HOMEPAGE_URL,
    LICENSE_API_URL,
    WEBPLAYBACK_API_URL,
)
from .exceptions import ApiError

logger = logging.getLogger(__name__)
WRAPPER_ACCOUNT_URL_ERROR = (
    "Wrapper account API 地址必须是本机回环地址，例如 http://127.0.0.1:30020/。"
)
APPLE_MUSIC_DEVELOPER_TOKEN_ERROR = (
    "未能从 Apple Music 页面获取开发者 token。Apple Music 网页结构可能已变更，请更新应用后重试。"
)
APPLE_MUSIC_SCRIPT_URI_RE = re.compile(
    r"""<script[^>]+src=["']([^"']*(?:assets/index|index-legacy)[^"']*\.js[^"']*)["']""",
    re.IGNORECASE,
)
JWT_RE = re.compile(r"\b(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b")


def _matches_apple_music_cookie_domain(domain: str) -> bool:
    normalized_domain = domain.lstrip(".").lower()
    primary_domain = APPLE_MUSIC_COOKIE_DOMAIN.lstrip(".").lower()
    return normalized_domain == primary_domain or normalized_domain == "apple.com"


def normalize_wrapper_account_url(wrapper_account_url: str) -> str:
    candidate = str(wrapper_account_url or "").strip()
    if not candidate:
        raise ValueError(WRAPPER_ACCOUNT_URL_ERROR)
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(WRAPPER_ACCOUNT_URL_ERROR)
    hostname = parsed.hostname
    if hostname.lower() != "localhost":
        try:
            if not ip_address(hostname).is_loopback:
                raise ValueError(WRAPPER_ACCOUNT_URL_ERROR)
        except ValueError as exc:
            if str(exc) == WRAPPER_ACCOUNT_URL_ERROR:
                raise
            raise ValueError(WRAPPER_ACCOUNT_URL_ERROR) from exc
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = f"{path}/"
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(WRAPPER_ACCOUNT_URL_ERROR) from exc
    netloc_host = hostname
    if ":" in netloc_host and not netloc_host.startswith("["):
        netloc_host = f"[{netloc_host}]"
    netloc = f"{netloc_host}:{port}" if port is not None else netloc_host
    return parsed._replace(netloc=netloc, path=path, params="", query="", fragment="").geturl()


def _decode_jwt_part(part: str) -> dict:
    padded = part + "=" * ((4 - len(part) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def _is_apple_music_developer_token(token: str) -> bool:
    try:
        header_part, payload_part, _signature_part = token.split(".", 2)
        header = _decode_jwt_part(header_part)
        payload = _decode_jwt_part(payload_part)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return False
    return (
        header.get("typ") == "JWT"
        and header.get("kid") == "WebPlayKid"
        and payload.get("iss") == "AMPWebPlay"
        and "exp" in payload
    )


def _extract_apple_music_script_uris(home_page: str) -> list[str]:
    seen: set[str] = set()
    script_uris: list[str] = []
    for match in APPLE_MUSIC_SCRIPT_URI_RE.finditer(home_page):
        script_uri = match.group(1)
        if script_uri in seen:
            continue
        seen.add(script_uri)
        script_uris.append(script_uri)
    return script_uris


def _extract_developer_token(script_text: str) -> str | None:
    fallback_token: str | None = None
    for match in JWT_RE.finditer(script_text):
        token = match.group(1)
        if _is_apple_music_developer_token(token):
            return token
        if fallback_token is None:
            fallback_token = token
    return fallback_token


class AppleMusicApi:
    def __init__(
        self,
        storefront: str = "us",
        language: str = "en-US",
        media_user_token: str | None = None,
        developer_token: str | None = None,
        network_config: NetworkConfig | None = None,
    ) -> None:
        self.storefront = storefront
        self.language = language
        self.media_user_token = media_user_token
        self.token = developer_token
        self.network_config = network_config

    @classmethod
    async def create_from_netscape_cookies(
        cls,
        cookies_path: str = "./cookies.txt",
        *args,
        **kwargs,
    ) -> "AppleMusicApi":
        cookies = MozillaCookieJar(cookies_path)
        cookies.load(ignore_discard=True, ignore_expires=True)
        parse_cookie = lambda name: next(
            (
                cookie.value
                for cookie in cookies
                if cookie.name == name
                and _matches_apple_music_cookie_domain(cookie.domain)
            ),
            None,
        )

        media_user_token = parse_cookie("media-user-token")
        if not media_user_token:
            raise ValueError(
                '"media-user-token" cookie not found in cookies. '
                "Make sure you have exported the cookies from the Apple Music webpage "
                "and are logged in with an active subscription."
            )

        return await cls.create(
            storefront=None,
            media_user_token=media_user_token,
            developer_token=None,
            *args,
            **kwargs,
        )

    @classmethod
    async def create_from_wrapper(
        cls,
        wrapper_account_url: str = "http://127.0.0.1:30020/",
        *args,
        **kwargs,
    ) -> "AppleMusicApi":
        normalized_wrapper_account_url = normalize_wrapper_account_url(wrapper_account_url)
        wrapper_account_response = await get_response(
            normalized_wrapper_account_url,
            network_config=kwargs.get("network_config"),
        )
        wrapper_account_info = safe_json(wrapper_account_response)

        return await cls.create(
            storefront=None,
            media_user_token=wrapper_account_info["music_token"],
            developer_token=wrapper_account_info["dev_token"],
            *args,
            **kwargs,
        )

    @classmethod
    async def create(
        cls,
        storefront: str | None = "us",
        language: str = "en-US",
        media_user_token: str | None = None,
        developer_token: str | None = None,
        network_config: NetworkConfig | None = None,
    ) -> "AppleMusicApi":
        api = cls(
            storefront=storefront,
            language=language,
            media_user_token=media_user_token,
            developer_token=developer_token,
            network_config=network_config,
        )
        await api.initialize()
        return api

    async def initialize(self) -> None:
        await self._initialize_client()
        await self._initialize_token()
        await self._initialize_account_info()

    async def close(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.aclose()

    async def _initialize_client(self) -> None:
        self.client = httpx.AsyncClient(
            headers={
                "accept": "*/*",
                "accept-language": "en-US",
                "origin": APPLE_MUSIC_HOMEPAGE_URL,
                "priority": "u=1, i",
                "referer": APPLE_MUSIC_HOMEPAGE_URL,
                "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            },
            params={
                "l": self.language,
            },
            follow_redirects=True,
            timeout=60.0,
            **httpx_client_kwargs(self.network_config),
        )

    async def _get_token(self) -> str:
        response = await self.client.get(APPLE_MUSIC_HOMEPAGE_URL)
        home_page = response.text

        script_uris = _extract_apple_music_script_uris(home_page)
        if not script_uris:
            raise RuntimeError(APPLE_MUSIC_DEVELOPER_TOKEN_ERROR)

        for script_uri in script_uris:
            response = await self.client.get(urljoin(APPLE_MUSIC_HOMEPAGE_URL, script_uri))
            token = _extract_developer_token(response.text)
            if token:
                return token

        raise RuntimeError(APPLE_MUSIC_DEVELOPER_TOKEN_ERROR)

    async def _initialize_token(self) -> None:
        self.token = self.token or await self._get_token()
        self.client.headers.update({"authorization": f"Bearer {self.token}"})

    async def _initialize_account_info(self) -> None:
        if not self.media_user_token:
            return

        self.client.cookies.update(
            {
                "media-user-token": self.media_user_token,
            }
        )

        self.account_info = await self.get_account_info()
        self.storefront = self.account_info["meta"]["subscription"]["storefront"]

    @property
    def active_subscription(self) -> bool:
        return (
            getattr(self, "account_info", {})
            .get("meta", {})
            .get("subscription", {})
            .get("active", False)
        )

    @property
    def account_restrictions(self) -> dict | None:
        data = getattr(self, "account_info", {}).get("data", [])
        if not data:
            return None
        return data[0].get("attributes", {}).get("restrictions")

    async def get_account_info(self, meta: str = "subscription") -> dict:
        account_info = await self._amp_request(
            f"/v1/me/account",
            {
                "meta": meta,
            },
        )
        logger.debug(f"Account info: {account_info}")

        return account_info

    async def _amp_request(
        self,
        endpoint: str,
        params: dict | None = None,
    ) -> dict:
        response = await self.client.get(
            AMP_API_URL + endpoint,
            params=params or {},
        )
        response_json = safe_json(response)

        if (
            response.status_code != 200
            or response_json is None
            or "errors" in response_json
        ):
            raise ApiError(
                message=response.text,
                status_code=response.status_code,
            )

        return response_json

    async def get_song(
        self,
        song_id: str,
        extend: str = "extendedAssetUrls",
        include: str = "lyrics,albums",
    ) -> dict | None:
        song = await self._amp_request(
            f"/v1/catalog/{self.storefront}/songs/{song_id}",
            {
                "extend": extend,
                "include": include,
            },
        )
        logger.debug(f"Song: {song}")

        return song

    async def get_music_video(
        self,
        music_video_id: str,
        include: str = "albums",
    ) -> dict | None:
        music_video = await self._amp_request(
            f"/v1/catalog/{self.storefront}/music-videos/{music_video_id}",
            {
                "include": include,
            },
        )
        logger.debug(f"Music video: {music_video}")

        return music_video

    async def get_uploaded_video(
        self,
        post_id: str,
    ) -> dict | None:
        uploaded_video = await self._amp_request(
            f"/v1/catalog/{self.storefront}/uploaded-videos/{post_id}",
        )
        logger.debug(f"Uploaded video: {uploaded_video}")

        return uploaded_video

    async def get_album(
        self,
        album_id: str,
        extend: str = "extendedAssetUrls",
    ) -> dict | None:
        album = await self._amp_request(
            f"/v1/catalog/{self.storefront}/albums/{album_id}",
            {
                "extend": extend,
            },
        )
        logger.debug(f"Album: {album}")

        return album

    async def get_playlist(
        self,
        playlist_id: str,
        limit_tracks: int = 300,
        extend: str = "extendedAssetUrls",
    ) -> dict | None:
        playlist = await self._amp_request(
            f"/v1/catalog/{self.storefront}/playlists/{playlist_id}",
            {
                "limit[tracks]": limit_tracks,
                "extend": extend,
            },
        )
        logger.debug(f"Playlist: {playlist}")

        return playlist

    async def get_artist(
        self,
        artist_id: str,
        include: str = "albums,music-videos",
        views: str = "full-albums,compilation-albums,live-albums,singles,top-songs",
        limit: int = 100,
    ) -> dict | None:
        artist = await self._amp_request(
            f"/v1/catalog/{self.storefront}/artists/{artist_id}",
            {
                "include": include,
                "views": views,
                **{
                    f"limit[{_include}]": limit
                    for _include in [*include.split(","), *views.split(",")]
                },
            },
        )
        logger.debug(f"Artist: {artist}")

        return artist

    async def get_library_album(
        self,
        album_id: str,
        extend: str = "extendedAssetUrls",
    ) -> dict | None:
        album = await self._amp_request(
            f"/v1/me/library/albums/{album_id}",
            {
                "extend": extend,
            },
        )
        logger.debug(f"Library album: {album}")

        return album

    async def get_library_playlist(
        self,
        playlist_id: str,
        include: str = "tracks",
        limit: int = 100,
        extend: str = "extendedAssetUrls",
    ) -> dict | None:
        playlist = await self._amp_request(
            f"/v1/me/library/playlists/{playlist_id}",
            {
                "include": include,
                **{f"limit[{_include}]": limit for _include in include.split(",")},
                "extend": extend,
            },
        )
        logger.debug(f"Library playlist: {playlist}")

        return playlist

    async def get_library_song(
        self,
        song_id: str,
        include: str = "catalog",
        extend: str = "extendedAssetUrls",
    ) -> dict | None:
        song = await self._amp_request(
            f"/v1/me/library/songs/{song_id}",
            {
                "include": include,
                "extend": extend,
            },
        )
        logger.debug(f"Library song: {song}")

        return song

    async def get_library_music_video(
        self,
        music_video_id: str,
        include: str = "catalog",
    ) -> dict | None:
        music_video = await self._amp_request(
            f"/v1/me/library/music-videos/{music_video_id}",
            {
                "include": include,
            },
        )
        logger.debug(f"Library music video: {music_video}")

        return music_video

    async def get_search_results(
        self,
        term: str,
        types: str = "songs,music-videos,albums,playlists,artists",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        search_results = await self._amp_request(
            f"/v1/catalog/{self.storefront}/search",
            {
                "term": term,
                "types": types,
                "limit": limit,
                "offset": offset,
            },
        )
        logger.debug(f"Search results: {search_results}")

        return search_results

    async def extend_api_data(
        self,
        api_response: dict,
        extend: str = "extendedAssetUrls",
    ) -> typing.AsyncGenerator[dict, None]:
        next_uri = api_response.get("next")
        if not next_uri:
            return

        while next_uri:
            extended_api_data = await self._get_extended_api_data(
                next_uri,
                api_response.get("href"),
                extend,
            )
            yield extended_api_data
            next_uri = extended_api_data.get("next")

    async def _get_extended_api_data(
        self,
        next_uri: str,
        href_uri: str | None,
        extend: str,
    ) -> dict:
        href_params = parse_qs(urlparse(href_uri or "").query)
        next_uri_params = parse_qs(urlparse(next_uri).query)
        limit = href_params.get("limit")
        params = {
            **({"limit": limit[0]} if limit else {}),
            **{
                key: values[0] if len(values) == 1 else values
                for key, values in next_uri_params.items()
                if key != "limit"
            },
            "extend": extend,
        }
        extended_api_data = await self._amp_request(urlparse(next_uri).path, params)
        logger.debug(f"Extended API data: {extended_api_data}")

        return extended_api_data

    async def get_webplayback(
        self,
        track_id: str,
        is_library: bool = False,
    ) -> dict:
        request_body = {
            "language": self.language,
        }
        if is_library:
            request_body["universalLibraryId"] = track_id
        else:
            request_body["salableAdamId"] = track_id

        response = await self.client.post(
            WEBPLAYBACK_API_URL,
            json=request_body,
        )
        webplayback = safe_json(response)

        if (
            response.status_code != 200
            or webplayback is None
            or "dialog" in webplayback
        ):
            raise ApiError(
                message=response.text,
                status_code=response.status_code,
            )

        return webplayback

    async def get_license_exchange(
        self,
        track_id: str,
        track_uri: str,
        challenge: str,
        key_system: str = "com.widevine.alpha",
    ) -> dict:
        response = await self.client.post(
            LICENSE_API_URL,
            json={
                "challenge": challenge,
                "key-system": key_system,
                "uri": track_uri,
                "adamId": track_id,
                "isLibrary": False,
                "user-initiated": True,
            },
        )
        license_exchange = safe_json(response)

        if (
            response.status_code != 200
            or license_exchange is None
            or license_exchange.get("status") != 0
        ):
            raise ApiError(
                message=response.text,
                status_code=response.status_code,
            )

        logger.debug(f"License exchange: {license_exchange}")

        return license_exchange
