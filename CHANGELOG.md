# Changelog

## Unreleased

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
