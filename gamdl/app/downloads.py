from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ..api import AppleMusicApi, ItunesApi
from ..downloader import (
    AppleMusicBaseDownloader,
    AppleMusicDownloader,
    AppleMusicSongDownloader,
    DownloadItem,
    GamdlError,
)
from ..interface import AppleMusicInterface, AppleMusicSongInterface
from ..interface import SongCodec
from ..downloader.constants import ALBUM_MEDIA_TYPE, PLAYLIST_MEDIA_TYPE, SONG_MEDIA_TYPE
from .paths import AppPaths
from .wrapper_manager import WrapperManager

logger = logging.getLogger("gamdl.app.download")
WRAPPER_REQUIRED_CODECS = {SongCodec.ALAC, SongCodec.ATMOS}

ALLOWED_URL_TYPES = {
    "song",
    "album",
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

    def _emit(self, message: str) -> None:
        logger.info(message)
        if self.log_callback:
            self.log_callback(message)

    async def _create_downloader(self, job: DownloadJob) -> AppleMusicDownloader:
        codec = SongCodec(job.song_codec)
        if codec in WRAPPER_REQUIRED_CODECS and not job.use_wrapper:
            raise RuntimeError(
                "当前所选音质需要外部 wrapper。"
                " 请先启动外部 wrapper，或切回 AAC。"
            )
        if job.use_wrapper and not codec.is_legacy():
            resolved_ip = await asyncio.to_thread(
                self.wrapper_manager.ensure_running,
                job.wrapper_decrypt_ip,
            )
            if resolved_ip != job.wrapper_decrypt_ip:
                self._emit(
                    f"已自动连接到 wrapper：{resolved_ip}（原设置为 {job.wrapper_decrypt_ip}）"
                )
                job.wrapper_decrypt_ip = resolved_ip

        apple_music_api = await AppleMusicApi.create(
            storefront=None,
            language=job.language,
            media_user_token=self.media_user_token,
        )
        if not apple_music_api.active_subscription:
            raise RuntimeError("当前账号没有可用的 Apple Music 订阅。")

        itunes_api = ItunesApi(apple_music_api.storefront, apple_music_api.language)
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
            artist_auto_select=None,
        )

    @staticmethod
    def _validate_url_kind(url_info) -> None:
        if not url_info:
            raise ValueError("链接无法识别。")
        kind = url_info.type or (
            f"library-{url_info.library_type}" if url_info.library_type else None
        )
        if kind not in ALLOWED_URL_TYPES:
            raise ValueError(f"首版仅支持歌曲、专辑和歌单，暂不支持 {kind or '该链接'}。")

    async def run(self, job: DownloadJob) -> DownloadResult:
        result = DownloadResult(total_urls=len(job.urls), output_path=job.output_path)
        downloader = await self._create_downloader(job)
        self._emit(f"已连接 Apple Music storefront: {downloader.interface.apple_music_api.storefront}")

        for index, url in enumerate(job.urls, 1):
            prefix = f"[URL {index}/{len(job.urls)}]"
            self._emit(f'{prefix} 正在处理 "{url}"')
            try:
                url_info = downloader.get_url_info(url)
                self._validate_url_kind(url_info)
                download_queue = await downloader.get_download_queue(url_info)
                if not download_queue:
                    self._emit(f'{prefix} 没有可下载内容，已跳过。')
                    result.skipped_items += 1
                    continue
            except Exception as exc:
                result.errors += 1
                self._emit(f"{prefix} 处理失败: {exc}")
                continue

            result.processed_urls += 1
            for item_index, download_item in enumerate(download_queue, 1):
                title = _media_title(download_item)
                item_prefix = f"[Track {item_index}/{len(download_queue)}]"
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
                    if download_item.final_path:
                        latest_media_path = str(Path(download_item.final_path).resolve())
                        result.latest_media_path = latest_media_path
                        result.latest_media_dir = str(Path(latest_media_path).parent)
                except GamdlError as exc:
                    result.skipped_items += 1
                    self._emit(f'{item_prefix} 跳过 "{title}": {exc}')
                except Exception as exc:
                    result.errors += 1
                    self._emit(f'{item_prefix} 下载失败 "{title}": {exc}')

        result.finished_at = time.time()
        self._emit(
            "任务完成: "
            f"成功 {result.downloaded_items}，跳过 {result.skipped_items}，错误 {result.errors}"
        )
        return result

    def run_sync(self, job: DownloadJob) -> DownloadResult:
        return asyncio.run(self.run(job))


def _media_title(download_item: DownloadItem) -> str:
    metadata = getattr(download_item, "media_metadata", None) or {}
    return metadata.get("attributes", {}).get("name", "Unknown Title")
