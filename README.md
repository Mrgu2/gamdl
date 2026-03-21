# Apple Music Downloader 1.0.2

[![Release](https://img.shields.io/badge/release-1.0.2-0a84ff)](https://github.com/Mrgu2/gu_music_downloader/releases)
[![License](https://img.shields.io/github/license/Mrgu2/gu_music_downloader)](https://github.com/Mrgu2/gu_music_downloader/blob/codex/alac-download/LICENSE)
[![Windows Build](https://img.shields.io/github/actions/workflow/status/Mrgu2/gu_music_downloader/build-windows.yml?branch=codex%2Falac-download&label=windows%20build)](https://github.com/Mrgu2/gu_music_downloader/actions/workflows/build-windows.yml)

<p align="center">
  <img src="assets/macos/app-icon-1024.png" alt="Apple Music Downloader app icon" width="180" />
</p>

这是一个以桌面版为中心维护的 Apple Music 下载工具 fork，基于上游 [Gamdl](https://github.com/glomatico/gamdl) 改编，由 [@Mrgu2](https://github.com/Mrgu2) 持续维护。

软件纯免费开源，无病毒，无额外广告。请确保你是从 GitHub [@Mrgu2](https://github.com/Mrgu2/gu_music_downloader) 下载该软件，以保证来源可核验、没有后门风险。

## 项目定位

`Apple Music Downloader 1.0.2` 的目标很明确：

- 提供开箱即用的桌面客户端，而不是只给命令行
- 默认覆盖最常用场景：歌曲、专辑、歌单下载
- 支持本地 FFmpeg 转换页面，面向实际整理音乐库
- 保留上游 CLI 能力，方便高级用户继续脚本化使用

当前维护分支：

- Fork 仓库：[Mrgu2/gu_music_downloader](https://github.com/Mrgu2/gu_music_downloader)
- 当前修改分支：[codex/alac-download](https://github.com/Mrgu2/gu_music_downloader/tree/codex/alac-download)
- 上游项目：[glomatico/gamdl](https://github.com/glomatico/gamdl)

## 1.0.2 发布内容

正式版 `1.0.2` 交付两套桌面客户端：

- `Apple.Music.Downloader.dmg`
  - 面向 macOS
  - 包含桌面应用和内置 `ffmpeg`
- `Apple.Music.Downloader.Windows.zip`
  - 面向 Windows
  - 保持和之前 release 一致的分发形式
  - 由 GitHub Actions 在 `windows-latest` 上构建

源码仓库仍然保留 CLI 与开发环境，适合高级用户自行构建或二次修改。

## 主要功能

### 下载

- 下载歌曲、专辑、歌单
- 支持 App 内登录
- 支持导入浏览器登录态
- 统一任务队列与日志页面
- 默认 AAC 可直接使用
- `ALAC / 杜比全景声` 继续走外部 wrapper

### 转换

- 左侧栏独立 `转换` 页面
- 支持选择单个文件或整个目录
- 支持选择输出目录
- 支持输出 `FLAC` 和 `MP3`
- 目录输入时保留原始目录结构
- 复制常见标签和封面
- 自动写入安全提示评论字段

转换策略：

- `FLAC`
  - 保持原采样率
  - 尽量保持原位宽
  - 保留曲目编号、曲目总数、碟片编号、碟片总数
- `MP3`
  - 保持原采样率
  - 使用高质量 `libmp3lame -q:a 0`
  - 对不兼容采样率直接报错，不偷偷重采样

### 诊断与安全

- 本地保存设置、日志和诊断信息
- About 页面显示 fork 来源、修改者和安全下载提示
- macOS 打包时优先使用内置 `ffmpeg`
- 缺失 `ffmpeg` 时会在界面中直接提示，不会默默失败

## 下载与安装

### macOS

1. 前往 [Releases](https://github.com/Mrgu2/gu_music_downloader/releases)
2. 下载 `Apple.Music.Downloader.dmg`
3. 将 `Apple Music Downloader.app` 拖到 `Applications`
4. 首次打开如果被 Gatekeeper 阻止，在 `系统设置 > 隐私与安全性` 里点 `仍要打开`

macOS 包说明：

- 当前发布包内置 `ffmpeg`
- 当前内置构建针对 Apple Silicon
- 如需重新打包，请参考下方构建说明

### Windows

1. 前往 [Releases](https://github.com/Mrgu2/gu_music_downloader/releases)
2. 下载 `Apple.Music.Downloader.Windows.zip`
3. 解压后运行 `Apple Music Downloader Windows.exe`

Windows 包说明：

- 继续沿用之前 release 的 zip 分发形式
- 当前构建由 GitHub Actions 自动生成
- 目前主要做过构建级验证，建议保留源码仓库用于问题复现

## 使用说明

### 下载页面

1. 登录 Apple Music 账号，或导入浏览器登录态
2. 粘贴一个或多个 Apple Music 链接
3. 确认输出目录和下载设置
4. 加入任务队列并在 `任务` 页面查看进度

### 转换页面

1. 选择输入模式：`文件` 或 `目录`
2. 选择输入路径
3. 选择输出目录
4. 选择输出格式：`FLAC` 或 `MP3`
5. 加入转换队列

评论字段会统一写入：

`软件纯免费开源，无病毒，无额外广告，请确保你是从 GitHub @Mrgu2 下载的该软件。`

## 构建说明

### macOS 本地构建

```bash
uv sync --extra desktop-build
bash scripts/build-macos-app.sh
```

构建产物：

```text
dist/Apple Music Downloader.app
dist/Apple Music Downloader.dmg
```

### Windows 构建

推荐两种方式：

1. 在 Windows 环境本地构建
2. 推送到 GitHub 后使用仓库内置 workflow 自动构建

现有 workflow：

- [Build Windows Desktop App](https://github.com/Mrgu2/gu_music_downloader/actions/workflows/build-windows.yml)

它会：

- 安装依赖
- 跑全量 `unittest`
- 使用 `Apple Music Downloader Windows.spec` 打包
- 上传 `apple-music-downloader-windows` artifact

如果你是在 Windows 本地构建，可参考：

```bash
uv sync --extra desktop-build
uv run python -m unittest discover -s tests
uv run pyinstaller --noconfirm "Apple Music Downloader Windows.spec"
```

## 开发与验证

常用本地验证命令：

```bash
uv run python -m unittest discover -s tests
uv run python -m unittest tests.test_web_gui
uv run python -m unittest tests.test_conversion
```

开发时启动桌面版：

```bash
uv run python -m gamdl.desktop_app
```

## CLI 说明

这个仓库仍然保留上游 `gamdl` CLI 能力，但当前 README 不再复制整份上游命令参考，避免桌面版文档被淹没。

如果你是高级用户，可以直接查看：

- 上游 CLI 项目：[glomatico/gamdl](https://github.com/glomatico/gamdl)
- 当前源码仓库：[Mrgu2/gu_music_downloader](https://github.com/Mrgu2/gu_music_downloader)

命令行入口仍然可用：

```bash
gamdl --help
```

## 本地数据目录

### macOS 桌面版

```text
~/Library/Application Support/Apple Music Downloader
```

这里会保存：

- 设置
- 登录态
- 日志
- 诊断包
- 临时文件

这套桌面版数据与 CLI 的配置文件分离，不会覆盖你自己的命令行配置。

## 致谢

- 上游项目：[glomatico/gamdl](https://github.com/glomatico/gamdl)
- 当前 fork 与桌面版维护者：[Mrgu2](https://github.com/Mrgu2)
- 外部 wrapper 项目：[WorldObservationLog/wrapper](https://github.com/WorldObservationLog/wrapper)
