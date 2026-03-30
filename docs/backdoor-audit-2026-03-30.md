# Backdoor Audit - 2026-03-30

## Scope

- Repo: `gamdl-mrgu2`
- External sample: `/Users/wenjiegu/Downloads/wrapper-main.zip`
- Audit goal: detect obvious backdoor, covert remote-control, credential exfiltration, hidden download-and-exec, or unauthorized persistence behavior

## Method

1. Enumerated repo entrypoints, subprocess usage, network endpoints, and token storage.
2. Unzipped `wrapper-main.zip` to a temporary directory and reviewed `main.c`, `wrapper.c`, `Dockerfile`, `README.md`, and build config.
3. Ran string-level scans over bundled ELF/`.so` files for suspicious domains, shells, and downloader markers.
4. Cross-checked whether suspicious code paths are active in release builds or only in debug builds.

## Verdict

No clear hidden backdoor was found in the Python repo or in the readable source inside `wrapper-main.zip`.

The code does expose two real security risks:

1. `wrapper` exposes `storefront_id`, `dev_token`, and `music_token` over an unauthenticated local HTTP endpoint.
2. The wrapper README tells users to bind the service to `0.0.0.0` and publish the account port, which can expose those tokens beyond localhost if the container is run as documented.

These are explicit, readable behaviors rather than covert backdoor behavior, but they are still sensitive enough to matter.

## Evidence

### 1. No hidden command execution path found in the Python app

- Search across `gamdl/` and `scripts/` found no `shell=True`, `os.system`, `eval`, or `exec` usage.
- Subprocess calls are limited to expected local tools such as `docker`, `osascript`, `open`, `ffmpeg`, and helper processes.
- Default desktop/web host stays on `127.0.0.1`, not `0.0.0.0`.

Relevant files:

- `gamdl/web_gui.py` sets `DEFAULT_HOST = "127.0.0.1"` and `DEFAULT_WRAPPER_DECRYPT_IP = "127.0.0.1:10022"`.
- `gamdl/api/apple_music_api.py` defaults wrapper account access to `http://127.0.0.1:30020/`.

### 2. Wrapper source does not contain obvious covert C2 logic

- The readable wrapper source opens three expected listener ports: decrypt, m3u8, and account info.
- Outbound URLs found in source are Apple endpoints used to obtain playback/account material:
  - `https://play.itunes.apple.com/WebObjects/MZPlay.woa/music/fps`
  - `https://play.itunes.apple.com/WebObjects/MZPlay.woa/wa/createMusicToken`
  - `https://sf-api-token-service.itunes.apple.com/apiToken`
- String scans over bundled ELF/`.so` files did not surface third-party C2 domains, webhook endpoints, or downloader markers beyond Apple/Android/toolchain strings.

### 3. Confirmed security issue: unauthenticated token exposure

In `wrapper-main/main.c`:

- `handle_account()` returns JSON containing `storefront_id`, `dev_token`, and `music_token`.
- `new_socket_account()` binds that account API to `args_info.host_arg` and `args_info.account_port_arg`.
- Startup caches these secrets into globals and starts the account listener thread automatically.

This means any client that can reach the account port can fetch the tokens. There is no authentication or origin check in that path.

### 4. Confirmed deployment risk: README encourages network exposure

In `wrapper-main/README.md`, the documented `docker run` commands use `-H 0.0.0.0` and publish ports including the account port. That broadens access from localhost-only to any host that can reach the container port mapping.

### 5. Build-time nuance: SSL bypass exists only in non-release builds

In `wrapper-main/main.c`, the `#ifndef MyRelease` block hooks `curl_easy_setopt` and disables SSL verification and pinning for debug builds.

In `wrapper-main/CMakeLists.txt`, the `main` target is compiled with `COMPILE_DEFINITIONS "MyRelease"`, so this bypass is not active in the normal release build. The debug GitHub workflow explicitly removes that definition.

## Findings

### Finding A: token leakage surface in wrapper account API

Severity: High

Why it matters:

- `music_token` and `dev_token` are enough for the repo's wrapper-based API path.
- The endpoint is unauthenticated.
- The accompanying README encourages a host binding that is broader than localhost.

Impact:

- If the documented container command is used on a reachable network, another machine on the same network may be able to read the tokens.

### Finding B: plaintext fallback token storage in desktop app

Severity: Medium

In `gamdl/app/auth.py`, token storage falls back to a plain text file when keyring is unavailable. This is visible behavior, not covert exfiltration, but it does increase local credential exposure if the host is already compromised or shared.

## Limitations

- The bundled `rootfs/system/lib64/*.so` files are prebuilt binaries. This audit used source review plus string-level inspection, not full reverse engineering.
- The provided `wrapper-main.zip` looks like a source snapshot rather than a ready-to-run release artifact; `Dockerfile` expects a `./wrapper` binary, but that binary is not present in this zip.
- Because of that, this audit can strongly rule out obvious source-level backdoor behavior, but cannot mathematically prove absence of malicious logic inside every bundled third-party binary.

## Reproduction Notes

Commands used during the audit included:

```bash
rg -n --hidden -S "(eval\\(|exec\\(|subprocess\\.|os\\.system|socket\\.|requests\\.|urllib|http://|https://|base64|b64decode|marshal|pickle|ctypes|dlopen|wget|curl |powershell|reg add|LaunchAgents|crontab|startup|autorun|ssh-|token|password|secret|discord|telegram|webhook|ngrok|cloudflared|reverse shell|shell=True)" gamdl tests scripts pyproject.toml
unzip -l /Users/wenjiegu/Downloads/wrapper-main.zip
rg -n --hidden -S "(system\\(|popen\\(|execv|execve|fork\\(|socket\\(|connect\\(|listen\\(|accept\\(|curl|wget|http://|https://|/bin/sh|bash|python|dlopen|dlsym|ptrace|LD_PRELOAD|setuid|chmod|chown|cron|launchd|registry|CreateProcess|WinExec|ShellExecute)" /tmp/wrapper-audit.xv3T3E/wrapper-main
find /tmp/wrapper-audit.xv3T3E/wrapper-main/rootfs/system/lib64 -type f | head -n 200 | xargs strings 2>/dev/null | rg -n -i "(https?://|[A-Za-z0-9.-]+\\.(com|net|org|cn|ru|xyz|top|io)|/bin/sh|bash -c|curl |wget |nc -e|powershell|discord|telegram|webhook|ngrok|cloudflared)"
shasum -a 256 /Users/wenjiegu/Downloads/wrapper-main.zip
```

Sample hash:

```text
106869afec2e7ee5afcf62545976c0852a7df7211dcb67908a2ec0faa14a4936  /Users/wenjiegu/Downloads/wrapper-main.zip
```

## Recommended Next Step

If you want a stricter hardening pass, the shortest path is:

1. force wrapper account API to bind only to `127.0.0.1`
2. stop publishing the account port in docs and helper commands unless absolutely required
3. add an allowlist check in the app so `wrapper_account_url` and `wrapper_decrypt_ip` default to loopback-only unless the user explicitly overrides it
