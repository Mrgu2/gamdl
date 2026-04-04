Apple Music Downloader 1.1.2

更新内容
- 桌面版不再把 Apple Music token 回退写入明文 `.token` 文件；如果系统 keyring / Keychain 不可用，现在会直接拒绝持久化登录态
- 当 keyring 删除失败时，当前进程会立即清空内存 token 缓存；登出和会话失效清理也会先清掉本地 `session.json`，避免 UI 长时间残留伪“已登录”
- 本地 Web UI 的敏感 GET / POST 现在统一要求 `X-Gamdl-Request-Token`，并统一附带 `X-Frame-Options`、CSP、`nosniff`、`no-referrer` 与 `no-store` 安全响应头
- wrapper account API 地址现在显式限制为 `localhost / 127.0.0.1 / ::1`，非法端口和非法 `--wrapper-account-url` 会收敛成明确的用户输入错误
- CLI 收尾时现在只会关闭实际支持的 API 对象，`--synced-lyrics-only` 也不再因为未初始化状态直接崩溃
- 下载服务和桌面版在初始化或关闭中途失败时会主动回收 API client、Web server 线程与日志资源，减少残余连接和资源泄漏噪音
- Web GUI、macOS 桌面版和 Windows 启动器现在一致支持 `::1` loopback 绑定，并生成真实可打开的本地会话 URL
- Web GUI 相关测试显式禁用代理、等待后台任务终态并关闭 opener，减少本机代理环境下的回归串扰

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
