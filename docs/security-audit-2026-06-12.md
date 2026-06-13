# Security Audit - 2026-06-12

## Scope

This audit focused on the desktop fork's local attack surface and dependency risk:

- local Web GUI request handling
- desktop file actions
- diagnostics bundle export
- credential and session file storage
- download path generation
- lyrics XML parsing
- local decryption imports
- dependency vulnerability checks
- static security scanning

## Fixed

- Local Web API rejects JSON request bodies larger than 1 MiB.
- Opening the latest downloaded file now only accepts applications returned by the current open-with options or by the system picker.
- Diagnostics export skips symlinked log files.
- App support directories are restricted to the current user when possible.
- `settings.json` and `session.json` are restricted to the current user when possible.
- Download path components now replace empty, `.`, and `..` path segments with `_`.
- Lyrics TTML parsing now uses `defusedxml`.
- Local AES decryption imports use `Cryptodome.Cipher` from `pycryptodomex`.
- macOS desktop file actions call `/usr/bin/open` and `/usr/bin/osascript` explicitly.
- Windows custom open-with applications must be existing absolute file paths.

## Verification

Commands run:

```bash
env UV_CACHE_DIR=/tmp/uv-cache uv lock --check
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv tool run pip-audit --requirement /tmp/gamdl-mrgu2-requirements.txt --no-deps --disable-pip --skip-editable
env UV_CACHE_DIR=/tmp/uv-cache UV_TOOL_DIR=/tmp/uv-tools uv tool run bandit -r gamdl scripts -x tests -ll
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy .venv/bin/python -m unittest discover -s tests
git diff --check
```

Results:

- `pip-audit`: no known vulnerabilities found for locked third-party dependencies. The local editable package was skipped because it has no package version to audit.
- `bandit -ll`: no medium or high severity issues identified.
- `unittest`: 274 tests passed.
- `git diff --check`: passed.

## Residual Risk

- `bandit` still reports low-severity findings for intentional subprocess usage and string constants whose names include `token`. These are reviewed as acceptable for this desktop app because subprocess calls use argument lists without `shell=True`, browser/app names are fixed or validated, Windows custom applications must be absolute files, and request-token strings are not hardcoded secrets.
- The bundled `hardcoded_wvd.py` remains a functional DRM device blob inherited by the downloader workflow. It is not a user credential, but it is still sensitive project material and should not be treated as a security boundary.
