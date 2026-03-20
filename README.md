# Apple Music Downloader / Gamdl Fork

[![PyPI version](https://img.shields.io/pypi/v/gamdl?color=blue)](https://pypi.org/project/gamdl/)
[![Python versions](https://img.shields.io/pypi/pyversions/gamdl)](https://pypi.org/project/gamdl/)
[![License](https://img.shields.io/github/license/glomatico/gamdl)](https://github.com/glomatico/gamdl/blob/main/LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/gamdl)](https://pypi.org/project/gamdl/)

<p align="center">
  <img src="assets/macos/app-icon-1024.png" alt="Apple Music Downloader app icon" width="180" />
</p>

Adapted from [Gamdl (Glomatico's Apple Music Downloader)](https://github.com/glomatico/gamdl).

这是一个基于 Gamdl 改编的仓库，当前主要交付物是一个 macOS 桌面版 `Apple Music Downloader`，同时保留原始 CLI 能力和大部分命令行说明。

This repository is adapted from Gamdl. The current primary deliverable is a macOS desktop app called `Apple Music Downloader`, while the original CLI capabilities and most command-line reference docs are still kept in the repo.

## 项目说明 / Overview

### 桌面版当前定位 / Current Desktop Scope

- `macOS only`
- 面向歌曲、专辑、歌单下载
- 支持 App 内登录和浏览器登录态导入
- 默认音质为 AAC
- `ALAC / 杜比全景声` 需要外部 wrapper
- 本地保存登录态、日志和诊断包

- `macOS only`
- Focused on songs, albums, and playlists
- Supports in-app login and browser session import
- AAC is the default format
- `ALAC / Dolby Atmos` require an external wrapper
- Stores sessions, logs, and diagnostics locally

### 仓库包含两条使用路径 / Two Ways To Use This Repo

1. `桌面版 / Desktop app`
   - 面向最终用户和内测分发
   - For end users and internal macOS testing
2. `CLI / Command-line`
   - 继承自原始 Gamdl，适合高级用户
   - Inherited from upstream Gamdl and intended for advanced users

## ✨ 功能 / Features

- 🎵 **High-Quality Songs / 高质量歌曲** - Download songs in AAC 256kbps and other codecs
- 🎬 **High-Quality Music Videos / 高质量 MV** - Download music videos in resolutions up to 4K
- 📝 **Synced Lyrics / 同步歌词** - Download synced lyrics in LRC, SRT, or TTML formats
- 🏷️ **Rich Metadata / 完整标签** - Automatic tagging with comprehensive metadata
- 🎤 **Artist Support / 艺人页支持** - Download all albums or music videos from an artist
- ⚙️ **Highly Customizable / 高可配置** - Extensive configuration options for advanced users

## 📋 前置要求 / Prerequisites

### Required / 必需

- **Python 3.10 or higher**
- **Apple Music Cookies / Apple Music Cookies** - Export your browser cookies in Netscape format while logged in with an active subscription at the Apple Music website:
  - **Firefox**: [Export Cookies](https://addons.mozilla.org/addon/export-cookies-txt)
  - **Chromium**: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)

### Optional / 可选

Add these tools to your system PATH for additional features.  
如需更完整的功能，请把下面这些工具加入系统 PATH。

- **[FFmpeg](https://ffmpeg.org/download.html)** - Required for `ffmpeg` music video remux mode
- **[mp4decrypt](https://www.bento4.com/downloads/)** - Required for `mp4box` music video remux mode
- **[MP4Box](https://gpac.io/downloads/gpac-nightly-builds/)** - Required for `mp4box` music video remux mode
- **[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE/releases/latest)** - Required for `nm3u8dlre` download mode, which is faster than the default downloader
- **[Wrapper](#️-wrapper)** - For downloading songs in ALAC and other experimental codecs without API limitations

## 📦 安装 / Installation

**Install Gamdl via pip / 使用 pip 安装：**

```bash
pip install gamdl
```

**Setup cookies / 配置 cookies：**

1. Place your cookies file in the working directory as `cookies.txt`, or
2. Specify the path using `--cookies-path` or in the config file

## 🚀 使用 / Usage

```bash
gamdl [OPTIONS] URLS...
```

### Supported URL Types / 支持的链接类型

- Songs / 单曲
- Albums (Public/Library) / 专辑（公开库 / 资料库）
- Playlists (Public/Library) / 歌单（公开库 / 资料库）
- Music Videos / 音乐视频
- Artists / 艺人页
- Post Videos / Post 视频
- Apple Music Classical / 古典音乐链接

### Examples / 示例

**Download a song / 下载单曲：**

```bash
gamdl "https://music.apple.com/us/album/never-gonna-give-you-up-2022-remaster/1624945511?i=1624945512"
```

**Download an album / 下载专辑：**

```bash
gamdl "https://music.apple.com/us/album/whenever-you-need-somebody-2022-remaster/1624945511"
```

**Download from an artist / 从艺人页下载：**

```bash
gamdl "https://music.apple.com/us/artist/rick-astley/669771"
```

**Interactive Prompt Controls / 交互提示按键：**

| Key            | Action            |
| -------------- | ----------------- |
| **Arrow keys** | Move selection    |
| **Space**      | Toggle selection  |
| **Ctrl + A**   | Select all        |
| **Enter**      | Confirm selection |

## ⚙️ Configuration

Configure Gamdl using command-line arguments or a config file.

**Config file location:**

- Linux: `~/.gamdl/config.ini`
- Windows: `%USERPROFILE%\.gamdl\config.ini`

The file is created automatically on first run. Command-line arguments override config values.

### Configuration Options

| Option                          | Description                                                       | Default                                        |
| ------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------- |
| **General Options**             |                                                                   |                                                |
| `--read-urls-as-txt`, `-r`      | Read URLs from text files                                         | `false`                                        |
| `--config-path`                 | Config file path                                                  | `<home>/.gamdl/config.ini`                     |
| `--log-level`                   | Logging level                                                     | `INFO`                                         |
| `--log-file`                    | Log file path                                                     | -                                              |
| `--no-exceptions`               | Don't print exceptions                                            | `false`                                        |
| `--no-config-file`, `-n`        | Don't use a config file                                           | `false`                                        |
| **Apple Music Options**         |                                                                   |                                                |
| `--cookies-path`, `-c`          | Cookies file path                                                 | `./cookies.txt`                                |
| `--wrapper-account-url`         | Wrapper account URL                                               | `http://127.0.0.1:30020`                       |
| `--language`, `-l`              | Metadata language                                                 | `en-US`                                        |
| **Output Options**              |                                                                   |                                                |
| `--output-path`, `-o`           | Output directory path                                             | `/Users/wenjiegu/Downloads`                    |
| `--temp-path`                   | Temporary directory path                                          | `.`                                            |
| `--wvd-path`                    | .wvd file path                                                    | -                                              |
| `--overwrite`                   | Overwrite existing files                                          | `false`                                        |
| `--save-cover`, `--no-save-cover` | Save cover as separate file                                     | `true`                                         |
| `--save-playlist`               | Save M3U8 playlist file                                           | `false`                                        |
| **Download Options**            |                                                                   |                                                |
| `--artist-auto-select`          | Automatically select artist content to download (artist URLs)     | -                                              |
| `--nm3u8dlre-path`              | N_m3u8DL-RE executable path                                       | `N_m3u8DL-RE`                                  |
| `--mp4decrypt-path`             | mp4decrypt executable path                                        | `mp4decrypt`                                   |
| `--ffmpeg-path`                 | FFmpeg executable path                                            | `ffmpeg`                                       |
| `--mp4box-path`                 | MP4Box executable path                                            | `MP4Box`                                       |
| `--use-wrapper`                 | Use wrapper                                                       | `false`                                        |
| `--wrapper-decrypt-ip`          | Wrapper decryption server IP                                      | `127.0.0.1:10020`                              |
| `--download-mode`               | Download mode                                                     | `ytdlp`                                        |
| `--cover-format`                | Cover format                                                      | `jpg`                                          |
| **Template Options**            |                                                                   |                                                |
| `--album-folder-template`       | Album folder template                                             | `{album_artist}/{album}`                       |
| `--compilation-folder-template` | Compilation folder template                                       | `Compilations/{album}`                         |
| `--no-album-folder-template`    | No album folder template                                          | `{artist}/Unknown Album`                       |
| `--single-disc-file-template`   | Single disc file template                                         | `{track:02d} {title}`                          |
| `--multi-disc-file-template`    | Multi disc file template                                          | `{disc}-{track:02d} {title}`                   |
| `--no-album-file-template`      | No album file template                                            | `{title}`                                      |
| `--playlist-file-template`      | Playlist file template                                            | `Playlists/{playlist_artist}/{playlist_title}` |
| `--date-tag-template`           | Date tag template                                                 | `%Y-%m-%dT%H:%M:%SZ`                           |
| `--exclude-tags`                | Comma-separated tags to exclude                                   | -                                              |
| `--cover-size`                  | Cover size in pixels                                              | `max available`                                |
| `--truncate`                    | Max filename length                                               | -                                              |
| **Song Options**                |                                                                   |                                                |
| `--song-codec-priority`         | Comma-separated codec priority                                    | `aac-legacy`                                   |
| `--synced-lyrics-format`        | Synced lyrics format                                              | `lrc`                                          |
| `--no-synced-lyrics`            | Don't download synced lyrics                                      | `false`                                        |
| `--synced-lyrics-only`          | Download only synced lyrics                                       | `false`                                        |
| `--use-album-date`              | Use album release date for songs                                  | `false`                                        |
| `--fetch-extra-tags`            | Fetch extra tags from preview (normalization and smooth playback) | `false`                                        |
| **Music Video Options**         |                                                                   |                                                |
| `--music-video-codec-priority`  | Comma-separated codec priority                                    | `h264,h265`                                    |
| `--music-video-remux-mode`      | Remux mode                                                        | `ffmpeg`                                       |
| `--music-video-remux-format`    | Music video remux format                                          | `m4v`                                          |
| `--music-video-resolution`      | Max music video resolution                                        | `1080p`                                        |
| **Post Video Options**          |                                                                   |                                                |
| `--uploaded-video-quality`      | Post video quality                                                | `best`                                         |


### Template Variables

**Tags for templates and exclude-tags:**

- `album`, `album_artist`, `album_id`
- `artist`, `artist_id`
- `composer`, `composer_id`
- `date` (supports strftime format: `{date:%Y}`)
- `disc`, `disc_total`
- `media_type`
- `playlist_artist`, `playlist_id`, `playlist_title`, `playlist_track`
- `title`, `title_id`
- `track`, `track_total`

**Tags for exclude-tags only:**

- `album_sort`, `artist_sort`, `composer_sort`, `title_sort`
- `comment`, `compilation`, `copyright`, `cover`, `gapless`, `genre`, `genre_id`, `lyrics`, `rating`, `storefront`, `xid`
- `all` (special: skip all tagging)

### Logging Level

- `DEBUG`, `INFO`, `WARNING`, `ERROR`

### Download Mode

- `ytdlp`, `nm3u8dlre`

### Remux Mode

- `ffmpeg`
- `mp4box` - Preserve the original closed caption track in music videos and some other minor metadata

### Cover Format

- `jpg`
- `png`
- `raw` - Raw format as provided by the artist (requires `save_cover` to be enabled as it doesn't embed covers into files)

### Metadata Language

Use ISO 639-1 language codes (e.g., `en-US`, `es-ES`, `ja-JP`, `pt-BR`). Don't always work for music videos.

### Song Codecs

**Stable:**

- `aac-legacy` - AAC 256kbps 44.1kHz
- `aac-he-legacy` - AAC-HE 64kbps 44.1kHz

**Experimental** (may not work due to API limitations):

- `aac` - AAC 256kbps up to 48kHz
- `aac-he` - AAC-HE 64kbps up to 48kHz
- `aac-binaural` - AAC 256kbps binaural
- `aac-downmix` - AAC 256kbps downmix
- `aac-he-binaural` - AAC-HE 64kbps binaural
- `aac-he-downmix` - AAC-HE 64kbps downmix
- `atmos` - Dolby Atmos 768kbps
- `ac3` - AC3 640kbps
- `alac` - ALAC up to 24-bit/192kHz (unsupported)
- `ask` - Interactive experimental codec selection

### Synced Lyrics Format

- `lrc`
- `srt` - SubRip subtitle format (more accurate timing)
- `ttml` - Native Apple Music format (not compatible with most media players)

### Music Video Codecs

- `h264`
- `h265`
- `ask` - Interactive codec selection

### Music Video Resolutions

- H.264: `240p`, `360p`, `480p`, `540p`, `720p`, `1080p`
- H.265 only: `1440p`, `2160p`

### Music Video Remux Formats

- `m4v`, `mp4`

### Post Video Quality

- `best` - Up to 1080p with AAC 256kbps
- `ask` - Interactive quality selection

### Artist Auto-Select Options

- `main-albums`
- `compilation-albums`
- `live-albums`
- `singles-eps`
- `all-albums`
- `top-songs`
- `music-videos`

## ⚙️ Wrapper / Wrapper（ALAC / Atmos）

Use the [wrapper](https://github.com/WorldObservationLog/wrapper) to download songs in ALAC and other experimental codecs without API limitations. Cookies are not required when using the wrapper.

如果你需要 `ALAC` 或其他实验音质，可以配合 [wrapper](https://github.com/WorldObservationLog/wrapper) 使用。使用 wrapper 时通常不再依赖浏览器 cookies。

### Setup Instructions / 安装步骤

1. **Start the wrapper server** - Run the wrapper server
2. **Enable wrapper in Gamdl** - Use `--use-wrapper` flag or set `use_wrapper = true` in config
3. **Run Gamdl** - Download as usual with the wrapper enabled

## 🖥️ Desktop App / 桌面版

This fork now includes a macOS desktop shell for the local GUI.

这个 fork 现在包含一个 macOS 桌面壳，产品名为 `Apple Music Downloader`。

The app is focused on:

桌面版当前重点是：

- Songs, albums, and playlists
- In-app Apple Music login plus browser session import
- Queue-based downloads
- Local logs and diagnostics export
- macOS only
- AAC by default, external wrapper for ALAC / Dolby Atmos

- 歌曲、专辑和歌单
- App 内 Apple Music 登录，以及浏览器会话导入
- 队列式下载
- 本地日志和诊断包导出
- 仅支持 macOS
- 默认 AAC，ALAC / 杜比全景声需要外部 wrapper

### Run During Development / 开发时运行

Run the desktop app during development:

开发时可直接启动桌面版：

```bash
uv run python -m gamdl.desktop_app
```

If you only want the local HTTP UI for development, you can still run:

如果你只想启动本地 HTTP GUI 进行开发，也可以运行：

```bash
uv run python -m gamdl.web_gui
```

### Build / 打包

Build a macOS `.app` and `.dmg`:

构建 macOS `.app` 和 `.dmg`：

```bash
./scripts/build-macos-app.sh
```

Build outputs:

构建产物位置：

```text
dist/Apple Music Downloader.app
dist/Apple Music Downloader.dmg
```

The packaged app already includes the macOS app icon.

打包产物已经包含 macOS 应用图标。

Optional signing and notarization:

可选的签名与 notarization：

- Set `APPLE_DEVELOPER_IDENTITY` before building to sign the `.app`
- Set `APPLE_NOTARY_PROFILE` before building to submit the generated `.dmg` with `notarytool`
- 构建前设置 `APPLE_DEVELOPER_IDENTITY`，可以对 `.app` 签名
- 构建前设置 `APPLE_NOTARY_PROFILE`，可以用 `notarytool` 提交生成的 `.dmg`

### Desktop Storage / 桌面版本地存储

The desktop app stores its settings, session data, logs, diagnostics, and temporary files under:

桌面版会把设置、登录态、日志、诊断包和临时文件保存到：

```text
~/Library/Application Support/Apple Music Downloader
```

This is separate from the CLI `config.ini`, so desktop changes won't overwrite your manual CLI defaults.

这套桌面版存储和 CLI 的 `config.ini` 是分开的，不会覆盖你手动维护的 CLI 配置。

The desktop app remembers:

桌面版会记住：

- Output path
- Wrapper decrypt address
- Song codec
- Save cover
- Log level
- Browser import toggle
- Setup completion status

First-run desktop defaults are:

桌面版首启默认值：

- Output path: `~/Downloads/Apple Music Downloader`
- Song codec: `aac-legacy`
- Save cover: `true`
- Language: `zh-CN`
- Wrapper enabled: `false`
- Wrapper decrypt address: `127.0.0.1:10022`

### Desktop Limitations / 桌面版当前限制

- macOS only
- Songs, albums, and playlists only
- AAC works out of the box
- ALAC / Dolby Atmos require an external wrapper
- Music videos, uploaded videos, and other advanced CLI paths are still CLI-oriented

- 仅支持 macOS
- 当前只开放歌曲、专辑、歌单
- AAC 开箱即用
- ALAC / 杜比全景声需要外部 wrapper
- MV、上传视频和更多高级下载路径仍以 CLI 为主

## 🧾 CLI Reference / 命令行参考

The remaining sections below are primarily CLI reference inherited from upstream Gamdl.

下面保留的是原始 Gamdl 的 CLI 详细参考，主要服务高级用户和脚本使用场景。

## 🐍 Embedding

Use Gamdl as a library in your Python projects:

```python
import asyncio

from gamdl.api import AppleMusicApi, ItunesApi
from gamdl.downloader import (
    AppleMusicBaseDownloader,
    AppleMusicDownloader,
    AppleMusicMusicVideoDownloader,
    AppleMusicSongDownloader,
    AppleMusicUploadedVideoDownloader,
)
from gamdl.interface import (
    AppleMusicInterface,
    AppleMusicMusicVideoInterface,
    AppleMusicSongInterface,
    AppleMusicUploadedVideoInterface,
)

async def main():
    # Create AppleMusicApi instance (from cookies or wrapper)
    apple_music_api = await AppleMusicApi.create_from_netscape_cookies(
        cookies_path="cookies.txt",
    )
    itunes_api = ItunesApi(
        apple_music_api.storefront,
        apple_music_api.language,
    )

    # Check subscription
    assert apple_music_api.active_subscription

    # Set up interfaces
    interface = AppleMusicInterface(apple_music_api, itunes_api)
    song_interface = AppleMusicSongInterface(interface)
    music_video_interface = AppleMusicMusicVideoInterface(interface)
    uploaded_video_interface = AppleMusicUploadedVideoInterface(interface)

    # Set up base downloader and specialized downloaders
    base_downloader = AppleMusicBaseDownloader()
    song_downloader = AppleMusicSongDownloader(
        base_downloader=base_downloader,
        interface=song_interface,
    )
    music_video_downloader = AppleMusicMusicVideoDownloader(
        base_downloader=base_downloader,
        interface=music_video_interface,
    )
    uploaded_video_downloader = AppleMusicUploadedVideoDownloader(
        base_downloader=base_downloader,
        interface=uploaded_video_interface,
    )

    # Main downloader
    downloader = AppleMusicDownloader(
        interface=interface,
        base_downloader=base_downloader,
        song_downloader=song_downloader,
        music_video_downloader=music_video_downloader,
        uploaded_video_downloader=uploaded_video_downloader,
    )

    # Download a song
    url = "https://music.apple.com/us/album/never-gonna-give-you-up-2022-remaster/1624945511?i=1624945512"
    url_info = downloader.get_url_info(url)
    if url_info:
        download_queue = await downloader.get_download_queue(url_info)
        if download_queue:
            for download_item in download_queue:
                await downloader.download(download_item)


if __name__ == "__main__":
    asyncio.run(main())
```

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details

## 🤝 Contributing

Currently, I'm not interested in reviewing pull requests that change or add features. Only critical bug fixes will be considered. However, feel free to open issues for bugs or feature requests.
