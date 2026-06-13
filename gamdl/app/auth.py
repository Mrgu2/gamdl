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
from ..network import NetworkConfig, build_subprocess_env, normalize_network_config
from .paths import AppPaths, restrict_permissions
from .settings import AppSettingsStore

logger = logging.getLogger("gamdl.app.auth")

KEYRING_SERVICE = "gamdl.desktop"
KEYRING_USERNAME = "media-user-token"
APPLE_MUSIC_LOGIN_URL = "https://music.apple.com/login"
KEYRING_UNAVAILABLE_MESSAGE = (
    "当前系统凭据存储不可用，无法安全保存 Apple Music 登录态。请启用系统钥匙串后重试。"
)
KEYRING_DELETE_UNAVAILABLE_MESSAGE = (
    "当前系统凭据存储不可用，无法安全清除 Apple Music 登录态。请启用系统钥匙串后重试。"
)


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


class TokenPersistenceError(RuntimeError):
    pass


class TokenDeletionError(RuntimeError):
    pass


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
        logger.warning("Keyring %s failed; refusing insecure token persistence: %s", action, exc)

    def _is_missing_keyring_entry_error(self, exc: Exception) -> bool:
        if exc.__class__.__name__ == "PasswordDeleteError":
            return True

        keyring = self._keyring()
        if not keyring:
            return False

        errors = getattr(keyring, "errors", None)
        password_delete_error = getattr(errors, "PasswordDeleteError", None)
        return bool(password_delete_error and isinstance(exc, password_delete_error))

    def _token_absent_in_keyring(self, keyring) -> bool:
        try:
            return not keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception as exc:
            logger.warning("Keyring delete verification failed: %s", exc)
            return False

    def _cleanup_legacy_fallback_token(self) -> None:
        if not self.paths.token_fallback_path.exists():
            return
        try:
            self.paths.token_fallback_path.unlink()
        except OSError:
            logger.warning("Failed to remove legacy plaintext token file", exc_info=True)
            return
        logger.warning("Removed legacy plaintext token fallback file at startup.")

    def keyring_available(self) -> bool:
        keyring = self._keyring()
        if not keyring:
            return False
        try:
            keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception as exc:
            logger.warning("Keyring availability check failed: %s", exc)
            return False
        return True

    def get(self) -> str | None:
        keyring = self._keyring()
        if not keyring:
            return self._cached_token
        try:
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception as exc:
            logger.warning("Keyring read failed; token unavailable until keyring recovers: %s", exc)
            return self._cached_token
        self._cleanup_legacy_fallback_token()
        if token:
            self._cached_token = token
            self.paths.token_fallback_path.unlink(missing_ok=True)
            return token
        return self._cached_token

    def set(self, token: str) -> None:
        keyring = self._keyring()
        if not keyring:
            raise TokenPersistenceError(KEYRING_UNAVAILABLE_MESSAGE)
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)
        except Exception as exc:
            self._log_keyring_error("write", exc)
            raise TokenPersistenceError(KEYRING_UNAVAILABLE_MESSAGE) from exc
        self._cached_token = token
        self.paths.token_fallback_path.unlink(missing_ok=True)

    def delete(self) -> None:
        keyring = self._keyring()
        self._cached_token = None
        try:
            if not keyring:
                raise TokenDeletionError(KEYRING_DELETE_UNAVAILABLE_MESSAGE)
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception as exc:
            if self._is_missing_keyring_entry_error(exc) or self._token_absent_in_keyring(keyring):
                return
            if not isinstance(exc, TokenDeletionError):
                self._log_keyring_error("delete", exc)
                raise TokenDeletionError(KEYRING_DELETE_UNAVAILABLE_MESSAGE) from exc
            raise
        finally:
            self.paths.token_fallback_path.unlink(missing_ok=True)


class AuthManager:
    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths()
        self.settings_store = AppSettingsStore(self.paths)
        self.token_store = TokenStore(self.paths)

    def _network_config(self) -> NetworkConfig:
        settings = self.settings_store.load()
        return normalize_network_config(settings.network_mode, settings.proxy_url)

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
        restrict_permissions(self.paths.session_path, 0o600)

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
            network_config=self._network_config(),
        )
        try:
            return SessionStatus(
                connected=True,
                storefront=api.storefront,
                language=api.language,
                active_subscription=api.active_subscription,
                account_restrictions=api.account_restrictions,
            )
        finally:
            await api.close()

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

    def keyring_available(self) -> bool:
        return self.token_store.keyring_available()

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
                env=build_subprocess_env(self._network_config()),
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
            if not self.keyring_available():
                return SessionStatus(connected=False, last_error=KEYRING_UNAVAILABLE_MESSAGE)
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

    def invalidate_session(
        self,
        reason: str = "Apple Music 登录已失效，请重新登录。",
    ) -> SessionStatus:
        saved = self._load_session()
        logger.info("Invalidating saved Apple Music session: %s", reason)
        deletion_error: TokenDeletionError | None = None
        try:
            self.token_store.delete()
        except TokenDeletionError as exc:
            deletion_error = exc
        payload = {
            "connected": False,
            "login_method": saved.get("login_method"),
            "browser": saved.get("browser"),
            "storefront": saved.get("storefront"),
            "language": saved.get("language"),
            "active_subscription": False,
            "account_restrictions": None,
            "checked_at": None,
            "last_error": reason,
        }
        self._save_session(payload)
        if deletion_error is not None:
            raise deletion_error
        return SessionStatus(**payload)

    def logout(self) -> SessionStatus:
        logger.info("Clearing saved Apple Music session")
        deletion_error: TokenDeletionError | None = None
        try:
            self.token_store.delete()
        except TokenDeletionError as exc:
            deletion_error = exc
        self._clear_session()
        if deletion_error is not None:
            raise deletion_error
        return SessionStatus(connected=False, last_error="已退出登录")
