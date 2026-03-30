from __future__ import annotations

import asyncio
import json
import logging
import platform
import subprocess
from dataclasses import asdict, dataclass
from enum import Enum
from http.cookiejar import CookieJar
from pathlib import Path
import time
from typing import Iterable

from ..api import AppleMusicApi
from ..api.apple_music_api import _matches_apple_music_cookie_domain
from .paths import AppPaths

logger = logging.getLogger("gamdl.app.auth")

KEYRING_SERVICE = "gamdl.desktop"
KEYRING_USERNAME = "media-user-token"
APPLE_MUSIC_LOGIN_URL = "https://music.apple.com/login"


class StringEnum(str, Enum):
    pass


class BrowserType(StringEnum):
    CHROME = "chrome"
    EDGE = "edge"
    BRAVE = "brave"
    FIREFOX = "firefox"


BROWSER_APP_NAMES = {
    BrowserType.CHROME: "Google Chrome",
    BrowserType.EDGE: "Microsoft Edge",
    BrowserType.BRAVE: "Brave Browser",
    BrowserType.FIREFOX: "Firefox",
}


class LoginMethod(StringEnum):
    BROWSER_IMPORT = "browser-import"
    WEBVIEW = "webview"


@dataclass
class SessionStatus:
    connected: bool
    login_method: str | None = None
    browser: str | None = None
    storefront: str | None = None
    language: str | None = None
    active_subscription: bool = False
    account_restrictions: dict | None = None
    checked_at: float | None = None
    last_error: str | None = None


class TokenStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self._cached_token: str | None = None

    def _keyring(self):
        try:
            import keyring  # type: ignore
        except ImportError:
            return None
        return keyring

    def _log_keyring_error(self, action: str, exc: Exception) -> None:
        logger.warning("Keyring %s failed, falling back to token file: %s", action, exc)

    def _write_fallback_token(self, token: str) -> None:
        self.paths.ensure()
        self.paths.token_fallback_path.write_text(token, encoding="utf-8")
        try:
            self.paths.token_fallback_path.chmod(0o600)
        except OSError:
            logger.debug("Unable to tighten fallback token permissions", exc_info=True)

    def get(self) -> str | None:
        keyring = self._keyring()
        if keyring:
            try:
                token = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
            except Exception as exc:
                logger.warning("Keyring read failed, falling back to cached token or token file: %s", exc)
                if self._cached_token:
                    return self._cached_token
            else:
                if token:
                    self._cached_token = token
                    self.paths.token_fallback_path.unlink(missing_ok=True)
                    return token
        if self.paths.token_fallback_path.exists():
            token = self.paths.token_fallback_path.read_text(encoding="utf-8").strip()
            self._cached_token = token
            return token
        return self._cached_token

    def set(self, token: str) -> None:
        self._cached_token = token
        wrote_to_keyring = False
        keyring = self._keyring()
        if keyring:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)
                wrote_to_keyring = True
            except Exception as exc:
                self._log_keyring_error("write", exc)
        if wrote_to_keyring:
            self._write_fallback_token(token)
            return
        self._write_fallback_token(token)

    def delete(self) -> None:
        self._cached_token = None
        keyring = self._keyring()
        if keyring:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
            except Exception as exc:
                self._log_keyring_error("delete", exc)
        self.paths.token_fallback_path.unlink(missing_ok=True)


class AuthManager:
    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths()
        self.token_store = TokenStore(self.paths)

    def _load_session(self) -> dict:
        if not self.paths.session_path.exists():
            return {}
        try:
            return json.loads(self.paths.session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_session(self, payload: dict) -> None:
        self.paths.ensure()
        self.paths.session_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _clear_session(self) -> None:
        self.paths.session_path.unlink(missing_ok=True)

    async def _verify_token(
        self,
        token: str,
        language: str = "zh-CN",
    ) -> SessionStatus:
        api = await AppleMusicApi.create(
            storefront=None,
            language=language,
            media_user_token=token,
        )
        return SessionStatus(
            connected=True,
            storefront=api.storefront,
            language=api.language,
            active_subscription=api.active_subscription,
            account_restrictions=api.account_restrictions,
        )

    def _store_verified_session(
        self,
        token: str,
        verified: SessionStatus,
        login_method: LoginMethod,
        browser: BrowserType | None,
    ) -> SessionStatus:
        payload = asdict(verified)
        payload.update(
            {
                "login_method": login_method.value,
                "browser": browser.value if browser else None,
            }
        )
        self.token_store.set(token)
        self._save_session(payload)
        return SessionStatus(**payload)

    @staticmethod
    def _coerce_browser(value: object) -> BrowserType | None:
        if isinstance(value, BrowserType):
            return value
        if not isinstance(value, str):
            return None
        try:
            return BrowserType(value)
        except ValueError:
            return None

    @classmethod
    def _coerce_login_method(
        cls,
        value: object,
        browser: BrowserType | None,
    ) -> LoginMethod:
        if isinstance(value, LoginMethod):
            return value
        if isinstance(value, str):
            try:
                return LoginMethod(value)
            except ValueError:
                pass
        return LoginMethod.BROWSER_IMPORT if browser else LoginMethod.WEBVIEW

    def _extract_token_from_cookie_jar(self, cookie_jar: Iterable) -> str:
        for cookie in cookie_jar:
            if (
                getattr(cookie, "name", None) == "media-user-token"
                and _matches_apple_music_cookie_domain(getattr(cookie, "domain", ""))
            ):
                return cookie.value
        raise ValueError(
            '未找到 "media-user-token"。请确认当前浏览器里已经登录 Apple Music。'
        )

    def _load_browser_cookies(self, browser: BrowserType) -> CookieJar:
        try:
            import browser_cookie3  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "缺少 browser-cookie3，无法导入浏览器登录态。"
            ) from exc

        loaders = {
            BrowserType.CHROME: browser_cookie3.chrome,
            BrowserType.EDGE: browser_cookie3.edge,
            BrowserType.BRAVE: browser_cookie3.brave,
            BrowserType.FIREFOX: browser_cookie3.firefox,
        }
        return loaders[browser](domain_name=".apple.com")

    def import_from_browser(
        self,
        browser: BrowserType,
        language: str = "zh-CN",
    ) -> SessionStatus:
        logger.info("Importing Apple Music session from %s", browser.value)
        cookie_jar = self._load_browser_cookies(browser)
        token = self._extract_token_from_cookie_jar(cookie_jar)
        verified = asyncio.run(self._verify_token(token, language=language))
        status = self._store_verified_session(
            token=token,
            verified=verified,
            login_method=LoginMethod.BROWSER_IMPORT,
            browser=browser,
        )
        logger.info("Imported session from %s for storefront %s", browser.value, status.storefront)
        return status

    def _open_apple_music_login_in_browser(self, browser: BrowserType) -> None:
        app_name = BROWSER_APP_NAMES[browser]
        logger.info("Opening Apple Music login in %s", app_name)
        try:
            subprocess.run(
                ["open", "-a", app_name, APPLE_MUSIC_LOGIN_URL],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"无法打开 {app_name}。请确认浏览器已安装。") from exc

    def _wait_for_browser_login(
        self,
        browser: BrowserType,
        language: str = "zh-CN",
        timeout: int = 180,
        poll_interval: float = 1.0,
    ) -> SessionStatus:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                cookie_jar = self._load_browser_cookies(browser)
                token = self._extract_token_from_cookie_jar(cookie_jar)
                verified = asyncio.run(self._verify_token(token, language=language))
                status = self._store_verified_session(
                    token=token,
                    verified=verified,
                    login_method=LoginMethod.BROWSER_IMPORT,
                    browser=browser,
                )
                logger.info(
                    "Browser-assisted login imported session from %s for storefront %s",
                    browser.value,
                    status.storefront,
                )
                return status
            except Exception as exc:
                last_error = exc
                time.sleep(poll_interval)

        browser_name = BROWSER_APP_NAMES[browser]
        logger.warning(
            "Timed out waiting for Apple Music login in %s after %s seconds",
            browser.value,
            timeout,
        )
        raise RuntimeError(
            f"已在 {browser_name} 打开 Apple Music 登录页，但在 {timeout} 秒内未检测到可用登录态。"
        ) from last_error

    def login_with_webview(
        self,
        language: str = "zh-CN",
        browser: BrowserType = BrowserType.CHROME,
    ) -> SessionStatus:
        if platform.system() != "Darwin":
            raise RuntimeError("当前平台暂不支持浏览器辅助登录，请使用浏览器导入登录态。")
        logger.info("Starting browser-assisted Apple Music login flow")
        self.paths.ensure()
        self._open_apple_music_login_in_browser(browser)
        status = self._wait_for_browser_login(browser, language=language)
        logger.info("Browser-assisted login completed for storefront %s", status.storefront)
        return status

    def get_session_status(
        self,
        verify: bool = False,
        language: str = "zh-CN",
    ) -> SessionStatus:
        saved = self._load_session()
        token = self.token_store.get()
        if not token:
            return SessionStatus(connected=False, last_error="尚未登录")

        if not verify:
            payload = {
                "connected": True,
                "login_method": saved.get("login_method"),
                "browser": saved.get("browser"),
                "storefront": saved.get("storefront"),
                "language": saved.get("language", language),
                "active_subscription": saved.get("active_subscription", False),
                "account_restrictions": saved.get("account_restrictions"),
                "checked_at": saved.get("checked_at"),
                "last_error": saved.get("last_error"),
            }
            return SessionStatus(**payload)

        try:
            verified = asyncio.run(self._verify_token(token, language=language))
        except Exception as exc:
            payload = {
                "connected": False,
                "login_method": saved.get("login_method"),
                "browser": saved.get("browser"),
                "storefront": saved.get("storefront"),
                "language": language,
                "active_subscription": False,
                "account_restrictions": None,
                "last_error": str(exc),
            }
            self._save_session(payload)
            logger.warning("Stored session verification failed: %s", exc)
            return SessionStatus(**payload)

        browser = self._coerce_browser(saved.get("browser"))
        return self._store_verified_session(
            token=token,
            verified=verified,
            login_method=self._coerce_login_method(saved.get("login_method"), browser),
            browser=browser,
        )

    def get_media_user_token(self) -> str:
        token = self.token_store.get()
        if not token:
            raise RuntimeError("尚未登录 Apple Music。")
        return token

    def logout(self) -> SessionStatus:
        logger.info("Clearing saved Apple Music session")
        self.token_store.delete()
        self._clear_session()
        return SessionStatus(connected=False, last_error="已退出登录")
