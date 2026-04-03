Apple Music Downloader 1.1.1

更新内容
- 下载任务如果遇到 Apple 返回 `403 Invalid authentication / 40300`，现在会立即清空本地伪“已连接”会话，并提示用户重新登录
- 本地 Web UI 现在只接受 loopback `Host`，阻断通过 DNS rebinding 读取首页 token 并接管本地 API 的路径
- 本地 Web UI 的下载预览和账号状态列表在写入 `innerHTML` 前会统一转义用户可控字段，避免本地 DOM XSS
- `/api/wrapper/start` 现在会复用桌面版的本机地址校验，不再接受任意远端主机作为探测目标
- 设置页新增三档 `网络模式`（自动 / 直连 / 高级代理），并把 HTTP 请求、登录校验与 wrapper / 下载相关子进程统一接入同一套代理策略
- 自定义代理模式下会为 `localhost / 127.0.0.1 / ::1` 单独直连，同时为子进程补齐 `NO_PROXY=localhost,127.0.0.1,::1`
- 网络模式从“高级代理”切回“自动 / 直连”时会清空陈旧 `proxy_url`，避免旧代理地址被后续请求误复用
- `artist / Top Songs` 现在会稳定落在 `歌手名/Top Songs/`，文件名保留 `[title_id]` 后缀，失败重试也会继续沿用原目录上下文
- 诊断包里的 `settings.json` 现已改成白名单导出，并继续对代理凭据与常见 token / Bearer 日志做脱敏
- 依赖安全升级：提升 `yt-dlp`、`pillow` 及锁文件中的相关安全依赖版本区间

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
