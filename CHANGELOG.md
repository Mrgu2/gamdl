# Changelog

## 1.1.2 - 2026-04-04

- 安全硬化：桌面版不再把 Apple Music token 回退写入明文 `.token` 文件；如果系统 keyring / Keychain 不可用，现在会直接拒绝持久化登录态并提示用户修复本机凭据存储
- 稳定性修复：如果系统 keyring / Keychain 删除失败，桌面版现在会拒绝把会话误写成“已退出 / 已失效”；Web UI 登出接口也会返回明确错误，而不是留下可被后续重新读出的旧 token
- 稳定性修复：CLI 收尾时现在只会关闭实际支持的 API 对象，不再因为替身对象或注入对象缺少异步 `close()` 而把原本成功的流程错误地以退出码 `1` 结束
- 稳定性修复：CLI 的 `--synced-lyrics-only` 不再因为未初始化的 `missing_music_video_paths` 直接崩溃
- 稳定性修复：测试与本地 Web GUI 生命周期进一步收口，日志 handler 不再持续引用已关闭流；Web GUI 服务端现在显式关闭响应连接并在关停时回收后台任务线程，减少回归测试里的残余连接与资源泄漏噪音
- 安全硬化：除首页 `/` 外，本地 Web UI 的敏感 GET / POST 现在统一要求 `X-Gamdl-Request-Token`，减少同机进程直接读取设置、任务、日志和账号状态的暴露面
- 安全硬化：本地 HTML 与 JSON 响应现在统一附带 `X-Frame-Options`、CSP、`nosniff`、`no-referrer` 与 `no-store` 安全响应头
- 安全硬化：wrapper account API 地址现在显式限制为 `localhost / 127.0.0.1 / ::1`，不再接受任意主机 URL
- 稳定性修复：wrapper account URL 的非法端口现在统一返回桌面端可读错误；Web GUI 相关测试也改为显式禁代理并关闭 opener，避免本机代理环境下的资源泄漏和回归串扰
- 稳定性修复：下载服务和 CLI 现在会在初始化中途失败时主动关闭已创建的 Apple Music / iTunes API client；macOS 桌面关闭路径也会显式等待本地 Web server 线程退出
- 安全修复：CLI 现在会把非法 `--wrapper-account-url` 收敛为明确的用户输入错误，不再直接抛出底层 `ValueError`
- 安全修复：当 keyring 删除失败时，当前进程会立即清空内存中的 token 缓存，避免“退出失败后仍可继续使用旧 token”
- 测试稳定性修复：Web GUI 的任务元数据测试现在会显式 stub 掉后台下载执行，不再意外命中本机代理或留下 `ResourceWarning: unclosed transport` 噪音
- 测试稳定性修复：失败任务重试的 Web GUI 回归现在也会等待后台任务跑到终态，避免测试线程退出后还有真实下载 worker 残留
- 稳定性修复：Web GUI、macOS 桌面版和 Windows 启动器现在一致支持 `::1` loopback 绑定，IPv6 回环地址会使用正确的 socket family 和带方括号的本地 URL
- 稳定性修复：当 keyring 报告“凭据不存在”时，登出和会话失效清理现在按幂等成功处理，不再把“条目已被外部删除”误判成“系统凭据存储不可用”
- 稳定性修复：即使 keyring 删除失败，桌面版现在也会先清掉本地 `session.json`，避免 UI 长时间残留伪“已登录”状态
- 稳定性修复：`gamdl.web_gui --no-open` 和 Windows 启动器展示的地址现在改为真实可打开的会话 URL，而不是缺少随机 session path 的裸 origin

## 1.1.1 - 2026-04-04

- 修复 Apple Music 登录过期后的误导状态：下载任务如果遇到 Apple 返回 `403 Invalid authentication / 40300`，现在会立即清空本地伪“已连接”会话，并提示用户重新登录，而不是继续显示缓存里的已登录状态
- 收紧本地 Web UI 的请求源校验：现在只接受 `Host` 为 loopback 的请求，阻断通过 DNS rebinding 读取首页 token 并接管本地 API 的路径
- 修复 `/api/wrapper/start` 的 loopback 绕过：启动 wrapper 前现在会复用桌面版的本机地址校验，不再接受任意远端主机作为探测目标
- 修复本地 Web UI 的 HTML 注入风险：下载预览和账号状态列表在写入 `innerHTML` 前现在会统一转义用户可控字段，避免 pasted URL 或账号限制字段触发本地 DOM XSS
- 修复 token 存储安全回归：keyring / Keychain 写入成功后现在会立即清理陈旧的 `.token` 明文兜底文件，不再在成功路径额外落盘敏感令牌
- 桌面版设置新增三档 `网络模式`（自动 / 直连 / 高级代理），并把 HTTP 请求、登录校验与 wrapper / 下载相关子进程统一接入同一套代理策略
- 修复高级代理下的 loopback 回归：`httpx` 显式代理现在会为 `localhost` / `127.0.0.1` / `::1` 单独直连，避免本机 wrapper / account API 被错误送进代理
- 修复高级代理对子进程的 loopback 回归：自定义代理模式现在会保留 `NO_PROXY=localhost,127.0.0.1,::1`，避免本地 wrapper / m3u8 流量误走代理；同时保留 SOCKS5 缺依赖时的精确报错
- 网络模式从“高级代理”切回“自动 / 直连”时，现在会清空陈旧的 `proxy_url`，避免旧代理地址在后续请求里被意外复用
- 设置页“启动 wrapper”按钮现在会带上当前表单里的网络设置，而不是只读取上次保存到磁盘的旧配置
- 修复 CLI 漏接 `network_mode` / `proxy_url` 的回归，命令行入口现在也会把同一套网络策略传给 API、iTunes 和下载器
- 诊断包里的 `settings.json` 现已改成白名单导出，仅保留排障必需字段，并继续对代理凭据与常见 token / Bearer 日志做脱敏
- 依赖安全升级：将 `yt-dlp` 最低版本提升到 `2026.02.21+`，将 `pillow` 提升到 `12.1.1+`，并通过重锁把 `urllib3`、`requests`、`cryptography`、`protobuf` 与 `pywidevine` 提升到已包含公开修复的安全版本区间
- 调整 `artist / Top Songs` 落盘规则：歌曲改为写入 `歌手名/Top Songs/`，并使用带 `title_id` 的逐曲文件名避免同名歌曲互相覆盖；对应封面 sidecar 也改为逐曲同名文件，避免共享 `Cover.jpg` 被覆盖
- 修复 `artist / Top Songs` 在合唱曲目下错误按曲目艺术家拆目录的问题，现统一落在用户选择的 artist 目录下
- 修复 `artist / Top Songs` 在合唱歌曲场景下按曲目艺术家错误分流目录的问题，现统一落在用户选中的 artist 目录下
- 修复 `artist / Top Songs` 失败重试丢失目录上下文的问题；现在重试单曲会继续落在原来的 `歌手名/Top Songs/` 目录，而不会退回专辑目录结构
- 修复 `artist / Top Songs` 在极小 `truncate` 配置下把 `[title_id]` 后缀截断的问题；现在即使截断长度很小，也会优先保留完整 ID 后缀，避免重名覆盖保护失效
- 修复 `artist_auto_select` 预选分组缺失时的崩溃：当某个 artist 没有 `Top Songs` 等对应 view 时，现在会返回空下载列表而不是抛 `KeyError`

## 1.1.0 - 2026-03-30

- 新增 `docs/backdoor-audit-2026-03-30.md`，记录对仓库与 `wrapper-main.zip` 的后门审计结论：未发现明确隐藏后门，但确认 wrapper 账户接口存在未鉴权令牌暴露风险
- 放宽 wrapper Docker 探测 / 启动命令超时，避免 Docker Desktop 稍慢时把可用环境误判成不可用
- 桌面版正式开放 artist 链接：下载页不再把 `Artist` 标记成 unsupported，并新增 `Top Songs / Main Albums / Singles & EPs / All Albums` 选择
- 下载任务会记录可重试的失败歌曲，任务页新增“重试失败歌曲”入口，并收敛为只重试歌曲级失败项
- artist 下载选项改为任务级输入，不再被静默持久化成全局默认值
- 对“整专中个别单曲失败”的补漏场景补回 fallback：拿不到单曲直链时，会回退到原始专辑链接并依赖跳过机制补齐，同时避免 artist / playlist 误重跑整个集合
- playlist 上下文里的失败歌曲现在会保留 `playlist_tags` 并按“歌单成员重试”执行，补漏时可继续写回 `.m3u8` 轨序，而不是退化成普通单曲下载
- 修复 playlist retry 的两条主路径回归：GUI 任务提交流程现在可显式开启 `.m3u8` 写回，且 `album?...i=<songId>` 形式的常见单曲链接会按 song 正确重试
- 调整下载后处理时序：只有媒体文件成功落盘后才会写 `.m3u8`、封面和歌词；对“文件已存在而跳过”的场景仍保留补齐歌单文件的能力
- sidecar 写入失败现在降级为 warning，不再把已成功落盘的媒体误报成失败，也不会覆盖 `MediaFileExists` 的跳过语义
- sidecar warning 现在会进入任务日志，并以可重试的 warning 形式记录到任务结果里，避免“界面显示成功但用户完全看不到封面/歌词/歌单文件失败”
- 更新 README 和 App 内置 wrapper 指南：在 Apple Silicon 场景的 Docker 命令中明确补充 `--platform linux/amd64` 兼容提示
- 收紧桌面版本地 Web API：POST 请求现在必须带本地随机令牌且限定 `application/json`，降低 localhost CSRF 和跨站状态修改风险
- 收紧桌面版 wrapper 使用面：设置层仅接受本机回环地址，文档和内置指南不再建议 `0.0.0.0` / `30022` 暴露账户接口
- 修正 token 存储策略：keyring 写入成功时不再额外落盘 `.token` 明文副本
- token fallback 改为“跨进程兜底 + 成功读 keyring 后自动清理”，避免应用重启后遇到瞬时 keyring 读失败就直接丢会话
- wrapper 端口探测现在同时支持 IPv4 / IPv6 loopback，避免 `::1` 之类配置可保存但永远连不上
- wrapper 地址校验现在会拒绝 `55535` 以上的解密端口，避免派生出的 m3u8 端口越界成不可能工作的配置
- 补齐失败重试语义：album / artist / playlist 等集合链接在 URL 级失败或缺少 song URL 时会保留父链接作为重试目标
- 修复下载页 artist 选项的输入联动：当文本框里不再包含 artist 链接时，会立即隐藏并清空该选择器，不再残留过期 UI 状态

## 1.0.7 - 2026-03-27

- 补齐桌面 Web UI 多项回归修正：日志页新增“打开日志目录”，应用日志和任务日志加入稳定滚动容器，刷新时保持贴底并避免清空用户选区
- 修复浏览器导入开关只保存不生效的问题；关闭后按钮会禁用，前端和后端都会拒绝导入浏览器登录态
- 修复下载任务前置校验：未登录时不再先入队再失败，`ALAC / 杜比全景声` 在未启用 wrapper 时会直接拦截
- 修复失败任务统计与操作：失败任务不再显示 `错误 0`，并保留“打开下载目录”
- 当歌曲没有可用流信息时显式抛出 `FormatNotAvailable`，避免 ALAC 路径下把“格式不可用”误吞成后续异常
- 补充 downloader / web GUI 回归测试，并继续提供 macOS `arm64` 与 `x86_64` 双架构 `dmg`

## 1.0.6 - 2026-03-26

- 恢复 CLI 机器无关的默认下载目录，去掉误写入作者本机绝对路径的问题
- 修复 wrapper 只填写端口时的 m3u8 地址推导，恢复 `127.0.0.1` 默认主机
- 修复 macOS PyInstaller spec 中的绝对图标路径，允许在 CI 和其他开发机上正常打包

## 1.0.5 - 2026-03-25

- macOS 登录主路径改为“浏览器辅助登录”，点击后会按所选浏览器打开 Apple Music 登录页并自动轮询导入会话
- 下载页和首次设置页的登录入口现在会沿用你选择的 `Chrome / Edge / Brave / Firefox`
- 收敛打包版内置登录窗不弹出、不抢前台的问题，不再继续依赖不稳定的嵌入式 WebKit 登录窗口
- 更新桌面版运行时提示、README 和 release 文案，使其与当前实际登录链路保持一致
- 补充 auth / web GUI / desktop runtime 相关回归测试，并重新构建 macOS `dmg`

## 1.0.4 - 2026-03-23

- 设置页新增“启动 wrapper”按钮，允许在 App 内直接尝试拉起 `wrapper-latest-10022`
- 新增 `/api/wrapper/start`，复用现有 wrapper 探测与启动逻辑，而不是只暴露配置项
- 打包后的 macOS App 现在会优先探测 `/opt/homebrew/bin/docker`、`/usr/local/bin/docker` 等常见 Docker CLI 路径
- 补充设置页与 wrapper manager 回归测试，覆盖启动按钮与 Docker 路径解析

## 1.0.3 - 2026-03-22

- 收敛最近一轮 Codex 工作流里已经补齐测试的稳定性修正
- 登录态存取在 keyring 读写删除异常时回退到本地 token 文件
- 兼容旧会话字段，缺失或脏的 `login_method` / `browser` 不再导致状态判断异常
- 新增 `wrapper_decrypt_ip` 校验与归一化，脏配置不会再把设置页 / 关于页弄坏
- 桌面版与 Windows 启动器会为所有必须依赖 wrapper 的编码预热，不再只覆盖 `ALAC`
- Wrapper 端口探测在 socket 异常下更稳，不再轻易抛出探测错误
- 桌面 Web UI 侧边栏和导航样式继续收口
- `scripts/render_poster.py` 新增 `--crop-mode`、`--render-query`、`--virtual-time-budget-ms`、`--background-threshold`
- 限定 Python 打包只收录 `gamdl`，避免把 `assets` / `marketing` 误判成顶层包

## 1.0.2 - 2026-03-21

- 继续交付 macOS `dmg` 与 Windows `zip` 两套桌面包
- Windows 包保持通过 GitHub Actions 构建
- README 按桌面版发布口径更新到 `1.0.2`
