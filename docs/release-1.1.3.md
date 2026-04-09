Apple Music Downloader 1.1.3

更新内容
- Web GUI 任务页现在支持取消排队中和运行中的任务；已请求取消的运行中任务会明确显示“取消中，当前文件完成后停止”
- 下载与转换服务新增协作式取消检查；转换中的 `ffmpeg` 进程会在收到取消请求后主动终止，避免界面已取消但后台继续长时间运行
- 任务 API 现在会回传 `cancel_requested` 状态，前端取消按钮和状态徽标会同步反映当前取消进度，并避免重复点击
- 元数据语言的前端展示、下载提交和登录 / 浏览器导入请求现在统一读取当前表单值；即使尚未点“保存设置”，本次操作也会按当前选择即时生效
- Web GUI 设置页恢复正确的 `hidden` 显隐语义；“自定义语言代码”“代理地址”等应隐藏字段不会再被全局样式错误显示出来
- 修复无封面 `m4a/mp4` 在转换时误走 ID3 封面读取分支导致立即失败的问题；现在这类文件也能正常进入转换与取消流程
- 补充 Web GUI、设置与转换取消相关回归测试，覆盖排队取消、运行中取消、语言归一化与 `ffmpeg` 终止路径

说明
- 当前 release 对应分支：`codex/alac-download`
- 当前 release 包含两个 macOS 安装包：`Apple Music Downloader-arm64.dmg` 和 `Apple Music Downloader-x86_64.dmg`
- 不附带 Windows 安装包；如需 Windows，请自行用 agent 修改并编译
- 如需继续处理 Windows 适配或打包，推荐使用 Claude Code 或 Codex

验证
- `uv run --with pytest python -m pytest` 通过
- `MACOS_ARCH=arm64 bash scripts/build-macos-app.sh` 通过
- `MACOS_ARCH=x86_64 bash scripts/build-macos-app.sh` 通过
- 已检查两个 dmg 中的主程序与 bundled `ffmpeg` 架构分别对应 `arm64` / `x86_64`
