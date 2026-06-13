Apple Music Downloader 1.1.4

更新内容
- 修复 Apple Music 网页更新后登录校验失败的问题：开发者 token 解析现在会从当前 `assets/index~...js` 和 legacy 入口中识别有效 JWT，不再只匹配旧格式。
- 移植 Apple Music API 兼容性修复：分页请求不再把 `offset` 误当 `limit`，webplayback 支持 library media，library song / music-video URL 会走对应 library API，单曲 m3u8 master URL 会优先使用 webplayback 并 fallback 到 metadata。
- 支持 DRM-free library song 直接下载，避免无解密 key 时被误判为格式不可用。
- 下载核心补强：yt-dlp 下载现在按流类型直接使用 HlsFD / HttpFD，song 解密会传递 CENC 与 single content key 标记，并支持 file-backed sample 读取。
- 进一步降低 song 解密峰值内存：file-backed 解密模式会把解密后的 payload 写入临时文件，并从临时文件流式写入最终 `mdat`，不再把完整输出 payload 聚成单个 `bytes`。
- 依赖安全升级：重锁 `urllib3`、`cryptography` 与 `idna` 到包含最新安全修复的版本，处理 Dependabot 针对 `uv.lock` 报告的开放告警。
- 安全硬化：本地 Web API 现在会拒绝超大 JSON 请求体，打开最近下载文件时只接受当前文件可用打开方式或系统选择器返回的应用；诊断包导出会跳过日志目录中的 symlink；应用私有目录、配置文件和会话文件会尽量收紧为仅当前用户可读写。
- 安全修复：下载路径组件现在会把空字符串、`.` 和 `..` 这类保留路径段替换为安全占位符，避免远端元数据影响最终媒体或 playlist 文件写出下载目录。
- 安全修复：歌词 TTML 解析改用 `defusedxml`，并将本地 AES 解密实现切到 `pycryptodomex` 的 `Cryptodome` 命名空间。
- 安全硬化：桌面文件操作在 macOS 上改用系统绝对路径调用 `open` / `osascript`；Windows 自定义打开应用必须是存在的绝对文件路径，避免相对命令名走 PATH 搜索。
- 新增 `docs/security-audit-2026-06-12.md`，记录本轮安全审查范围、修复项、验证命令和剩余低风险判断。
- macOS 打包脚本现在会优先复用已生成且有效的 PNG / ICNS 图标，并把 PyInstaller 缓存固定在工作区 `build/` 下，避免系统 SVG 转换或用户目录缓存权限问题阻断 Intel / Apple Silicon 双架构构建。

说明
- 当前 release 对应分支：`codex/alac-download`
- 当前 release 包含两个 macOS 安装包：`Apple Music Downloader-arm64.dmg` 和 `Apple Music Downloader-x86_64.dmg`
- `arm64` 面向 Apple Silicon Mac，`x86_64` 面向 Intel Mac
- 两个安装包都包含桌面应用和内置 `ffmpeg`
- 不附带 Windows 安装包；如需 Windows，请自行用 agent 修改并编译
- 如需继续处理 Windows 适配或打包，推荐使用 Claude Code 或 Codex

验证
- `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy .venv/bin/python -m unittest discover -s tests` 通过
- `env UV_CACHE_DIR=/tmp/uv-cache uv lock --check` 通过
- `git diff --check` 通过
- `env UV_CACHE_DIR=/tmp/uv-cache bash scripts/build-macos-app.sh` 通过
- `env UV_CACHE_DIR=/tmp/uv-cache MACOS_ARCH=x86_64 bash scripts/build-macos-app.sh` 通过
- 已检查两个 dmg 中的主程序与 bundled `ffmpeg` 架构分别对应 `arm64` / `x86_64`
