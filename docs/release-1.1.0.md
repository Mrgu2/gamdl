Apple Music Downloader 1.1.0

更新内容
- 桌面版正式开放 artist 链接下载，并新增 `Top Songs / Main Albums / Singles & EPs / All Albums` 选择
- 下载任务会记录可重试的失败歌曲，任务页新增“重试失败歌曲”，减少补漏时整集合重跑
- playlist 失败补漏现在会保留 `playlist_tags` 并继续写回 `.m3u8`，常见 `album?...i=<songId>` 单曲链接也会按 song 正确重试
- 对整专中个别单曲失败的补漏场景补回 fallback：拿不到单曲直链时会回退到原始专辑链接并依赖跳过机制补齐
- 下载后处理改为“媒体文件成功落盘后再写封面、歌词、歌单文件”，sidecar 写入失败会降级成 warning 并进入任务日志
- 收紧桌面版本地 Web API 与 wrapper 配置面：POST 现在必须带本地随机令牌且限定 `application/json`，设置层仅接受本机回环地址
- token 存储优先 keyring，成功读回 keyring 后会自动清理 `.token` 明文兜底文件

说明
- 当前 release 对应分支：`codex/alac-download`
- 当前 release 包含两个 macOS 安装包：`Apple.Music.Downloader-arm64.dmg` 和 `Apple.Music.Downloader-x86_64.dmg`
- 不附带 Windows 安装包；如需 Windows，请自行用 agent 修改并编译
- 如需继续处理 Windows 适配或打包，推荐使用 Claude Code 或 Codex

验证
- `uv run --with pytest python -m pytest` 通过
- `MACOS_ARCH=arm64 bash scripts/build-macos-app.sh` 通过
- `MACOS_ARCH=x86_64 bash scripts/build-macos-app.sh` 通过
- 已检查两个 dmg 中的主程序与 bundled `ffmpeg` 架构分别对应 `arm64` / `x86_64`
