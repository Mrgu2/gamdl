from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..api import AppleMusicApi, ItunesApi
from ..network import NetworkConfig, normalize_network_config
from ..downloader import (
    AppleMusicBaseDownloader,
    AppleMusicDownloader,
    AppleMusicSongDownloader,
    ArtistAutoSelect,
    DownloadItem,
    GamdlError,
)
from ..interface import AppleMusicInterface, AppleMusicSongInterface
from ..interface import SongCodec
from ..interface.types import PlaylistTags
from ..downloader.constants import (
    ALBUM_MEDIA_TYPE,
    ARTIST_MEDIA_TYPE,
    PLAYLIST_MEDIA_TYPE,
    SONG_MEDIA_TYPE,
)
from .paths import AppPaths
from .wrapper_manager import WrapperManager

logger = logging.getLogger("gamdl.app.download")
WRAPPER_REQUIRED_CODECS = {SongCodec.ALAC, SongCodec.ATMOS}

ALLOWED_URL_TYPES = {
    "song",
    "album",
    "artist",
    "playlist",
    "library-playlist",
    "library-albums",
}


@dataclass
class DownloadJob:
    urls: list[str]
    output_path: str
    overwrite: bool = False
    save_cover: bool = True
    log_level: str = "INFO"
    save_playlist: bool = False
    language: str = "zh-CN"
    song_codec: str = SongCodec.AAC_LEGACY.value
    use_wrapper: bool = False
    wrapper_decrypt_ip: str = "127.0.0.1:10022"
    network_mode: str = "auto"
    proxy_url: str = ""
    artist_auto_select: str | None = None
    retry_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DownloadResult:
    total_urls: int = 0
    processed_urls: int = 0
    downloaded_items: int = 0
    skipped_items: int = 0
    errors: int = 0
    output_path: str | None = None
    latest_media_path: str | None = None
    latest_media_dir: str | None = None
    failed_items: list[dict[str, Any]] = field(default_factory=list)
    finished_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class DownloadService:
    def __init__(
        self,
        media_user_token: str,
        log_callback: Callable[[str], None] | None = None,
        paths: AppPaths | None = None,
    ) -> None:
        self.media_user_token = media_user_token
        self.log_callback = log_callback
        self.paths = paths or AppPaths()
        self.paths.ensure()
        self.wrapper_manager = WrapperManager()

    @staticmethod
    def _network_config(job: DownloadJob) -> NetworkConfig:
        return normalize_network_config(job.network_mode, job.proxy_url)

    def _emit(self, message: str) -> None:
        logger.info(message)
        if self.log_callback:
            self.log_callback(message)

    @staticmethod
    async def _close_client(client: Any) -> None:
        close = getattr(client, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result

    async def _create_downloader(self, job: DownloadJob) -> AppleMusicDownloader:
        codec = SongCodec(job.song_codec)
        artist_auto_select = (
            ArtistAutoSelect(job.artist_auto_select)
            if job.artist_auto_select
            else None
        )
        if codec in WRAPPER_REQUIRED_CODECS and not job.use_wrapper:
            raise RuntimeError(
                "当前所选音质需要外部 wrapper。"
                " 请先启动外部 wrapper，或切回 AAC。"
            )
        network_config = self._network_config(job)
        if job.use_wrapper and not codec.is_legacy():
            resolved_ip = await asyncio.to_thread(
                self.wrapper_manager.ensure_running,
                job.wrapper_decrypt_ip,
                network_config,
            )
            if resolved_ip != job.wrapper_decrypt_ip:
                self._emit(
                    f"已自动连接到 wrapper：{resolved_ip}（原设置为 {job.wrapper_decrypt_ip}）"
                )
                job.wrapper_decrypt_ip = resolved_ip
        apple_music_api = None
        itunes_api = None
        try:
            apple_music_api = await AppleMusicApi.create(
                storefront=None,
                language=job.language,
                media_user_token=self.media_user_token,
                network_config=network_config,
            )
            if not apple_music_api.active_subscription:
                raise RuntimeError("当前账号没有可用的 Apple Music 订阅。")

            itunes_api = ItunesApi(
                apple_music_api.storefront,
                apple_music_api.language,
                network_config=network_config,
            )
            interface = AppleMusicInterface(apple_music_api, itunes_api)
            song_interface = AppleMusicSongInterface(interface)
            base_downloader = AppleMusicBaseDownloader(
                output_path=job.output_path,
                temp_path=str(self.paths.temp_dir),
                overwrite=job.overwrite,
                save_cover=job.save_cover,
                save_playlist=job.save_playlist,
                use_wrapper=job.use_wrapper,
                wrapper_decrypt_ip=job.wrapper_decrypt_ip,
                network_config=network_config,
            )
            song_downloader = AppleMusicSongDownloader(
                base_downloader=base_downloader,
                interface=song_interface,
                codec_priority=[codec],
            )
            return AppleMusicDownloader(
                interface=interface,
                base_downloader=base_downloader,
                song_downloader=song_downloader,
                music_video_downloader=None,
                uploaded_video_downloader=None,
                artist_auto_select=artist_auto_select,
            )
        except Exception:
            await self._close_client(itunes_api)
            await self._close_client(apple_music_api)
            raise

    @staticmethod
    def _validate_url_kind(url_info) -> None:
        if not url_info:
            raise ValueError("链接无法识别。")
        kind = _url_info_kind(url_info)
        if kind not in ALLOWED_URL_TYPES:
            raise ValueError(f"首版仅支持歌曲、专辑、歌单和艺术家，暂不支持 {kind or '该链接'}。")

    async def run(self, job: DownloadJob) -> DownloadResult:
        total_inputs = len(job.retry_items) if job.retry_items else len(job.urls)
        result = DownloadResult(total_urls=total_inputs, output_path=job.output_path)
        downloader = await self._create_downloader(job)
        try:
            self._emit(
                f"已连接 Apple Music storefront: {downloader.interface.apple_music_api.storefront}"
            )

            if job.retry_items:
                await self._run_retry_items(job, downloader, result)
                result.finished_at = time.time()
                self._emit(
                    "任务完成: "
                    f"成功 {result.downloaded_items}，跳过 {result.skipped_items}，错误 {result.errors}"
                )
                return result

            for index, url in enumerate(job.urls, 1):
                await self._process_url(job, downloader, result, url, index, len(job.urls))

            result.finished_at = time.time()
            self._emit(
                "任务完成: "
                f"成功 {result.downloaded_items}，跳过 {result.skipped_items}，错误 {result.errors}"
            )
            return result
        finally:
            await self._close_downloader_resources(downloader)

    async def _close_downloader_resources(self, downloader: AppleMusicDownloader) -> None:
        interface = getattr(downloader, "interface", None)
        for client in (
            getattr(interface, "apple_music_api", None),
            getattr(interface, "itunes_api", None),
        ):
            await self._close_client(client)

    async def _run_retry_items(
        self,
        job: DownloadJob,
        downloader: AppleMusicDownloader,
        result: DownloadResult,
    ) -> None:
        for index, retry_item in enumerate(job.retry_items, 1):
            strategy = _retry_target_strategy(retry_item)
            if strategy == "playlist-track":
                await self._process_playlist_retry_item(
                    downloader,
                    result,
                    retry_item,
                    index,
                    len(job.retry_items),
                )
                continue
            if strategy == "song-context":
                await self._process_contextual_song_retry_item(
                    downloader,
                    result,
                    retry_item,
                    index,
                    len(job.retry_items),
                )
                continue

            retry_url = _retry_target_url(retry_item)
            if retry_url:
                await self._process_url(
                    job,
                    downloader,
                    result,
                    retry_url,
                    index,
                    len(job.retry_items),
                )
                continue

            result.errors += 1
            result.failed_items.append(
                {
                    "title": str(retry_item.get("title") or "Unknown Title"),
                    "kind": str(retry_item.get("kind") or "unknown"),
                    "source_url": "",
                    "retry_url": None,
                    "retry_target": None,
                    "error": "重试数据无效，缺少可识别的链接。",
                }
            )
            self._emit(f'[Retry {index}/{len(job.retry_items)}] 重试数据无效，已跳过。')

    async def _process_url(
        self,
        job: DownloadJob,
        downloader: AppleMusicDownloader,
        result: DownloadResult,
        url: str,
        index: int,
        total: int,
    ) -> None:
        prefix = f"[URL {index}/{total}]"
        self._emit(f'{prefix} 正在处理 "{url}"')
        url_info = None
        try:
            url_info = downloader.get_url_info(url)
            self._validate_url_kind(url_info)
            download_queue = await downloader.get_download_queue(url_info)
            if not download_queue:
                self._emit(f'{prefix} 没有可下载内容，已跳过。')
                result.skipped_items += 1
                return
        except Exception as exc:
            result.errors += 1
            result.failed_items.append(_build_failed_url_entry(url_info, url, exc))
            self._emit(f"{prefix} 处理失败: {exc}")
            return

        result.processed_urls += 1
        for item_index, download_item in enumerate(download_queue, 1):
            item_prefix = f"[Track {item_index}/{len(download_queue)}]"
            await self._process_download_item(
                downloader,
                result,
                download_item,
                item_prefix,
                source_url=url,
                source_kind=_url_info_kind(url_info),
            )

    async def _process_playlist_retry_item(
        self,
        downloader: AppleMusicDownloader,
        result: DownloadResult,
        retry_item: dict[str, Any],
        index: int,
        total: int,
    ) -> None:
        song_url = _retry_target_url(retry_item)
        prefix = f"[Retry {index}/{total}]"
        self._emit(f'{prefix} 正在重试歌单歌曲 "{song_url}"')

        if not song_url:
            result.errors += 1
            result.failed_items.append(
                _build_failed_retry_entry(retry_item, ValueError("缺少可重试的歌曲链接。"))
            )
            self._emit(f"{prefix} 重试失败: 缺少可重试的歌曲链接。")
            return

        try:
            url_info = downloader.get_url_info(song_url)
            self._validate_url_kind(url_info)
            if _url_info_kind(url_info) not in SONG_MEDIA_TYPE:
                raise ValueError("歌单补漏仅支持单曲链接。")
            song_id = getattr(url_info, "sub_id", None) or getattr(url_info, "id", None)
            if not song_id:
                raise ValueError("无法解析失败歌曲的 song id。")
            playlist_tags = _playlist_tags_from_dict(retry_item.get("playlist_tags"))
            if not playlist_tags:
                raise ValueError("缺少歌单上下文，无法按歌单成员重试。")
            song_response = await downloader.interface.apple_music_api.get_song(song_id)
            song_data = (song_response or {}).get("data") or []
            if not song_data:
                raise ValueError("无法获取失败歌曲的元数据。")
            download_item = await downloader.song_downloader.get_download_item(
                song_data[0],
                playlist_tags_override=playlist_tags,
            )
        except Exception as exc:
            result.errors += 1
            result.failed_items.append(_build_failed_retry_entry(retry_item, exc))
            self._emit(f"{prefix} 重试失败: {exc}")
            return

        result.processed_urls += 1
        await self._process_download_item(
            downloader,
            result,
            download_item,
            prefix,
            source_url=song_url,
            source_kind="playlist",
        )

    async def _process_contextual_song_retry_item(
        self,
        downloader: AppleMusicDownloader,
        result: DownloadResult,
        retry_item: dict[str, Any],
        index: int,
        total: int,
    ) -> None:
        song_url = _retry_target_url(retry_item)
        prefix = f"[Retry {index}/{total}]"
        self._emit(f'{prefix} 正在重试上下文单曲 "{song_url}"')

        if not song_url:
            result.errors += 1
            result.failed_items.append(
                _build_failed_retry_entry(retry_item, ValueError("缺少可重试的歌曲链接。"))
            )
            self._emit(f"{prefix} 重试失败: 缺少可重试的歌曲链接。")
            return

        source_context = _retry_source_context(retry_item)
        if not source_context:
            result.errors += 1
            result.failed_items.append(
                _build_failed_retry_entry(retry_item, ValueError("缺少下载上下文，无法按原目录规则重试。"))
            )
            self._emit(f"{prefix} 重试失败: 缺少下载上下文，无法按原目录规则重试。")
            return

        try:
            url_info = downloader.get_url_info(song_url)
            self._validate_url_kind(url_info)
            if _url_info_kind(url_info) not in SONG_MEDIA_TYPE:
                raise ValueError("上下文单曲补漏仅支持单曲链接。")
            song_id = getattr(url_info, "sub_id", None) or getattr(url_info, "id", None)
            if not song_id:
                raise ValueError("无法解析失败歌曲的 song id。")
            song_response = await downloader.interface.apple_music_api.get_song(song_id)
            song_data = (song_response or {}).get("data") or []
            if not song_data:
                raise ValueError("无法获取失败歌曲的元数据。")
            download_item = await downloader.get_single_download_item(
                song_data[0],
                source_context=source_context,
                artist_folder_name=_retry_artist_folder_name(retry_item),
            )
        except Exception as exc:
            result.errors += 1
            result.failed_items.append(_build_failed_retry_entry(retry_item, exc))
            self._emit(f"{prefix} 重试失败: {exc}")
            return

        result.processed_urls += 1
        await self._process_download_item(
            downloader,
            result,
            download_item,
            prefix,
            source_url=song_url,
            source_kind="song",
        )

    async def _process_download_item(
        self,
        downloader: AppleMusicDownloader,
        result: DownloadResult,
        download_item: DownloadItem,
        item_prefix: str,
        *,
        source_url: str,
        source_kind: str | None,
    ) -> None:
        title = _media_title(download_item)
        try:
            if download_item.error:
                raise download_item.error
            if download_item.media_metadata["type"] not in SONG_MEDIA_TYPE.union(
                ALBUM_MEDIA_TYPE,
                PLAYLIST_MEDIA_TYPE,
            ):
                raise ValueError("当前媒体类型未开放到桌面版。")
            self._emit(f'{item_prefix} 下载 "{title}"')
            await downloader.download(download_item)
            result.downloaded_items += 1
            self._consume_sidecar_failures(
                result,
                download_item,
                item_prefix=item_prefix,
                source_url=source_url,
                source_kind=source_kind,
            )
            if download_item.final_path:
                latest_media_path = str(Path(download_item.final_path).resolve())
                result.latest_media_path = latest_media_path
                result.latest_media_dir = str(Path(latest_media_path).parent)
        except GamdlError as exc:
            self._consume_sidecar_failures(
                result,
                download_item,
                item_prefix=item_prefix,
                source_url=source_url,
                source_kind=source_kind,
            )
            result.skipped_items += 1
            self._emit(f'{item_prefix} 跳过 "{title}": {exc}')
        except Exception as exc:
            result.errors += 1
            result.failed_items.append(
                _build_failed_item_entry(
                    download_item,
                    source_url=source_url,
                    source_kind=source_kind,
                    error=exc,
                )
            )
            self._emit(f'{item_prefix} 下载失败 "{title}": {exc}')

    def _consume_sidecar_failures(
        self,
        result: DownloadResult,
        download_item: DownloadItem,
        *,
        item_prefix: str,
        source_url: str,
        source_kind: str | None,
    ) -> None:
        sidecar_failures = list(getattr(download_item, "sidecar_failures", None) or [])
        if not sidecar_failures:
            return

        title = _media_title(download_item)
        for failure in sidecar_failures:
            artifact_kind = str(failure.get("artifact_kind") or "artifact")
            error_text = str(failure.get("error") or "unknown error")
            self._emit(f'{item_prefix} 辅助文件写入失败 "{title}" [{artifact_kind}]: {error_text}')

        summary = "; ".join(
            f'{str(failure.get("artifact_kind") or "artifact")}: {str(failure.get("error") or "unknown error")}'
            for failure in sidecar_failures
        )
        failed_entry = _build_failed_item_entry(
            download_item,
            source_url=source_url,
            source_kind=source_kind,
            error=RuntimeError(summary),
        )
        failed_entry["warning_only"] = True
        failed_entry["sidecar_failures"] = sidecar_failures
        result.failed_items.append(failed_entry)
        download_item.sidecar_failures.clear()

    def run_sync(self, job: DownloadJob) -> DownloadResult:
        return asyncio.run(self.run(job))


def _media_title(download_item: DownloadItem) -> str:
    metadata = getattr(download_item, "media_metadata", None) or {}
    return metadata.get("attributes", {}).get("name", "Unknown Title")


def _url_info_kind(url_info) -> str | None:
    if not url_info:
        return None
    if getattr(url_info, "sub_id", None):
        return "song"
    return url_info.type or (
        f"library-{url_info.library_type}" if url_info.library_type else None
    )


def _build_failed_url_entry(url_info, url: str, error: Exception) -> dict[str, Any]:
    kind = _url_info_kind(url_info) or "unknown"
    retry_target = _build_url_retry_target(url) if kind in ALLOWED_URL_TYPES else None
    return {
        "title": url,
        "kind": kind,
        "source_url": url,
        "retry_url": _retry_target_url(retry_target),
        "retry_target": retry_target,
        "error": str(error),
    }


def _build_failed_item_entry(
    download_item: DownloadItem,
    *,
    source_url: str,
    source_kind: str | None,
    error: Exception,
) -> dict[str, Any]:
    metadata = getattr(download_item, "media_metadata", None) or {}
    attributes = metadata.get("attributes", {}) or {}
    kind = metadata.get("type") or source_kind or "unknown"
    retry_target = None
    playlist_tags = getattr(download_item, "playlist_tags", None)
    source_context = getattr(download_item, "source_context", None)
    artist_folder_name = getattr(download_item, "artist_folder_name", None)
    if kind in SONG_MEDIA_TYPE and attributes.get("url") and source_context:
        retry_target = _build_song_context_retry_target(
            attributes["url"],
            source_context,
            artist_folder_name,
        )
    elif kind in SONG_MEDIA_TYPE and attributes.get("url") and playlist_tags:
        retry_target = _build_playlist_track_retry_target(attributes["url"], playlist_tags)
    elif kind in SONG_MEDIA_TYPE and attributes.get("url"):
        retry_target = _build_url_retry_target(attributes["url"])
    elif source_kind in ALBUM_MEDIA_TYPE.union(ARTIST_MEDIA_TYPE, PLAYLIST_MEDIA_TYPE).union(
        {"library-playlist"}
    ):
        retry_target = _build_url_retry_target(source_url)
    return {
        "title": attributes.get("name", "Unknown Title"),
        "kind": kind,
        "source_url": source_url,
        "retry_url": _retry_target_url(retry_target),
        "retry_target": retry_target,
        "error": str(error),
    }


def _build_url_retry_target(url: str | None) -> dict[str, Any] | None:
    normalized_url = str(url or "").strip()
    if not normalized_url:
        return None
    return {
        "strategy": "url",
        "url": normalized_url,
    }


def _build_playlist_track_retry_target(
    song_url: str,
    playlist_tags: PlaylistTags,
) -> dict[str, Any]:
    return {
        "strategy": "playlist-track",
        "song_url": str(song_url).strip(),
        "playlist_tags": asdict(playlist_tags),
    }


def _build_song_context_retry_target(
    song_url: str,
    source_context: str,
    artist_folder_name: str | None = None,
) -> dict[str, Any]:
    payload = {
        "strategy": "song-context",
        "song_url": str(song_url).strip(),
        "source_context": str(source_context).strip(),
    }
    normalized_artist_folder_name = str(artist_folder_name or "").strip()
    if normalized_artist_folder_name:
        payload["artist_folder_name"] = normalized_artist_folder_name
    return payload


def _retry_target_strategy(retry_target: dict[str, Any] | None) -> str:
    if not isinstance(retry_target, dict):
        return ""
    return str(retry_target.get("strategy") or "").strip().lower()


def _retry_target_url(retry_target: dict[str, Any] | None) -> str | None:
    if not isinstance(retry_target, dict):
        return None
    url = retry_target.get("song_url") or retry_target.get("url")
    normalized_url = str(url or "").strip()
    return normalized_url or None


def _retry_source_context(retry_target: dict[str, Any] | None) -> str | None:
    if not isinstance(retry_target, dict):
        return None
    normalized_context = str(retry_target.get("source_context") or "").strip()
    return normalized_context or None


def _retry_artist_folder_name(retry_target: dict[str, Any] | None) -> str | None:
    if not isinstance(retry_target, dict):
        return None
    normalized_name = str(retry_target.get("artist_folder_name") or "").strip()
    return normalized_name or None


def _playlist_tags_from_dict(payload: Any) -> PlaylistTags | None:
    if not isinstance(payload, dict):
        return None
    if not any(payload.get(key) is not None for key in PlaylistTags.__dataclass_fields__):
        return None
    return PlaylistTags(
        playlist_artist=payload.get("playlist_artist"),
        playlist_id=payload.get("playlist_id"),
        playlist_title=payload.get("playlist_title"),
        playlist_track=payload.get("playlist_track"),
    )


def _build_failed_retry_entry(retry_item: dict[str, Any], error: Exception) -> dict[str, Any]:
    retry_target = {
        "strategy": _retry_target_strategy(retry_item),
    }
    retry_url = _retry_target_url(retry_item)
    if retry_target["strategy"] == "playlist-track":
        retry_target["song_url"] = retry_url
        if isinstance(retry_item.get("playlist_tags"), dict):
            retry_target["playlist_tags"] = dict(retry_item["playlist_tags"])
    elif retry_target["strategy"] == "song-context":
        retry_target["song_url"] = retry_url
        source_context = _retry_source_context(retry_item)
        if source_context:
            retry_target["source_context"] = source_context
        artist_folder_name = _retry_artist_folder_name(retry_item)
        if artist_folder_name:
            retry_target["artist_folder_name"] = artist_folder_name
    elif retry_url:
        retry_target["url"] = retry_url
    else:
        retry_target = None

    return {
        "title": str(retry_item.get("title") or retry_url or "Unknown Title"),
        "kind": str(retry_item.get("kind") or "unknown"),
        "source_url": str(retry_item.get("source_url") or retry_url or ""),
        "retry_url": retry_url,
        "retry_target": retry_target,
        "error": str(error),
    }
