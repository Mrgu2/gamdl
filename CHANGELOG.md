# Changelog

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
