import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from gamdl.app import AppPaths, AppSettingsStore
from gamdl.network import (
    NETWORK_GUIDANCE,
    NetworkConfig,
    SOCKS_PROXY_SUPPORT_ERROR,
    add_network_guidance,
    build_subprocess_env,
    httpx_client_kwargs,
    is_network_error,
)


class NetworkSettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = AppPaths(base_dir=Path(self.tempdir.name), app_name="GamdlTest")
        self.store = AppSettingsStore(self.paths)

    def test_defaults_include_auto_network_mode(self):
        defaults = self.store.load()
        self.assertEqual(defaults.network_mode, "auto")
        self.assertEqual(defaults.proxy_url, "")

    def test_save_accepts_custom_proxy_settings(self):
        target = Path(self.tempdir.name) / "downloads"
        settings = self.store.save(
            {
                "output_path": str(target),
                "network_mode": "custom",
                "proxy_url": "http://127.0.0.1:7890",
            }
        )

        self.assertEqual(settings.network_mode, "custom")
        self.assertEqual(settings.proxy_url, "http://127.0.0.1:7890")

    def test_save_rejects_empty_proxy_url_for_custom_mode(self):
        target = Path(self.tempdir.name) / "downloads"
        with self.assertRaisesRegex(ValueError, "必须填写代理地址"):
            self.store.save(
                {
                    "output_path": str(target),
                    "network_mode": "custom",
                    "proxy_url": "",
                }
            )

    def test_save_rejects_invalid_proxy_url_for_custom_mode(self):
        target = Path(self.tempdir.name) / "downloads"
        with self.assertRaisesRegex(ValueError, "代理地址格式无效"):
            self.store.save(
                {
                    "output_path": str(target),
                    "network_mode": "custom",
                    "proxy_url": "127.0.0.1:7890",
                }
            )

    @patch("gamdl.network.supports_socks_proxy", return_value=False)
    def test_save_rejects_socks_proxy_without_runtime_support(self, _supports_socks):
        target = Path(self.tempdir.name) / "downloads"
        with self.assertRaisesRegex(ValueError, SOCKS_PROXY_SUPPORT_ERROR):
            self.store.save(
                {
                    "output_path": str(target),
                    "network_mode": "custom",
                    "proxy_url": "socks5://127.0.0.1:7890",
                }
            )

    @patch("gamdl.network.supports_socks_proxy", return_value=False)
    def test_save_preserves_socks_support_error_for_valid_socks_proxy(self, _supports_socks):
        target = Path(self.tempdir.name) / "downloads"
        with self.assertRaisesRegex(ValueError, SOCKS_PROXY_SUPPORT_ERROR):
            self.store.save(
                {
                    "output_path": str(target),
                    "network_mode": "custom",
                    "proxy_url": "socks5://127.0.0.1:7890",
                }
            )

    def test_save_clears_proxy_url_when_switching_back_to_direct(self):
        target = Path(self.tempdir.name) / "downloads"
        self.store.save(
            {
                "output_path": str(target),
                "network_mode": "custom",
                "proxy_url": "http://127.0.0.1:7890",
            }
        )

        settings = self.store.save(
            {
                "output_path": str(target),
                "network_mode": "direct",
            }
        )

        self.assertEqual(settings.network_mode, "direct")
        self.assertEqual(settings.proxy_url, "")


class NetworkRuntimeTests(unittest.TestCase):
    def test_httpx_kwargs_keep_existing_behavior_in_auto_mode(self):
        self.assertEqual(
            httpx_client_kwargs(NetworkConfig(mode="auto", proxy_url="")),
            {"trust_env": True},
        )

    def test_httpx_kwargs_disable_env_in_direct_mode(self):
        self.assertEqual(
            httpx_client_kwargs(NetworkConfig(mode="direct", proxy_url="")),
            {"trust_env": False},
        )

    @patch("gamdl.network.supports_socks_proxy", return_value=True)
    def test_httpx_kwargs_use_explicit_proxy_in_custom_mode(self, _supports_socks):
        self.assertEqual(
            httpx_client_kwargs(NetworkConfig(mode="custom", proxy_url="socks5://127.0.0.1:7890")),
            {
                "trust_env": False,
                "proxy": "socks5://127.0.0.1:7890",
                "mounts": {
                    "all://localhost": None,
                    "all://127.0.0.1": None,
                    "all://[::1]": None,
                },
            },
        )

    @patch("gamdl.network.supports_socks_proxy", return_value=False)
    def test_httpx_kwargs_reject_missing_socks_support(self, _supports_socks):
        with self.assertRaisesRegex(ValueError, SOCKS_PROXY_SUPPORT_ERROR):
            httpx_client_kwargs(NetworkConfig(mode="custom", proxy_url="socks5://127.0.0.1:7890"))

    def test_httpx_kwargs_bypass_loopback_hosts_in_custom_mode(self):
        client = httpx.AsyncClient(
            **httpx_client_kwargs(
                NetworkConfig(mode="custom", proxy_url="http://127.0.0.1:7890")
            )
        )
        try:
            mount_patterns = {
                pattern.pattern: transport for pattern, transport in client._mounts.items()
            }
        finally:
            asyncio.run(client.aclose())

        self.assertIsNone(mount_patterns["all://localhost"])
        self.assertIsNone(mount_patterns["all://127.0.0.1"])
        self.assertIsNone(mount_patterns["all://[::1]"])

    def test_build_subprocess_env_returns_none_for_auto_mode(self):
        self.assertIsNone(build_subprocess_env(NetworkConfig(mode="auto", proxy_url="")))

    def test_build_subprocess_env_clears_proxy_variables_in_direct_mode(self):
        env = build_subprocess_env(
            NetworkConfig(mode="direct", proxy_url=""),
            {
                "PATH": os.environ.get("PATH", ""),
                "http_proxy": "http://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "ALL_PROXY": "http://127.0.0.1:7890",
            },
        )

        self.assertEqual(env["PATH"], os.environ.get("PATH", ""))
        self.assertNotIn("http_proxy", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("ALL_PROXY", env)

    def test_build_subprocess_env_injects_proxy_variables_in_custom_mode(self):
        proxy_url = "http://127.0.0.1:7890"
        env = build_subprocess_env(
            NetworkConfig(mode="custom", proxy_url=proxy_url),
            {"PATH": os.environ.get("PATH", "")},
        )

        self.assertEqual(env["http_proxy"], proxy_url)
        self.assertEqual(env["https_proxy"], proxy_url)
        self.assertEqual(env["HTTP_PROXY"], proxy_url)
        self.assertEqual(env["HTTPS_PROXY"], proxy_url)
        self.assertEqual(env["ALL_PROXY"], proxy_url)
        self.assertEqual(env["all_proxy"], proxy_url)
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1,::1")
        self.assertEqual(env["no_proxy"], "localhost,127.0.0.1,::1")

    def test_build_subprocess_env_preserves_existing_no_proxy_entries_in_custom_mode(self):
        proxy_url = "http://127.0.0.1:7890"
        env = build_subprocess_env(
            NetworkConfig(mode="custom", proxy_url=proxy_url),
            {
                "PATH": os.environ.get("PATH", ""),
                "NO_PROXY": "example.com,127.0.0.1",
            },
        )

        self.assertEqual(
            env["NO_PROXY"],
            "example.com,127.0.0.1,localhost,::1",
        )
        self.assertEqual(env["no_proxy"], env["NO_PROXY"])

    def test_add_network_guidance_appends_hint_once(self):
        message = add_network_guidance("proxy connect timeout", "network")
        self.assertIn(NETWORK_GUIDANCE, message)
        self.assertEqual(add_network_guidance(message, "network"), message)

    def test_is_network_error_detects_httpx_connect_error(self):
        self.assertTrue(is_network_error(httpx.ConnectError("All connection attempts failed")))


if __name__ == "__main__":
    unittest.main()
