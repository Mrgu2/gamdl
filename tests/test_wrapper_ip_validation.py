import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from gamdl.app import AppPaths
from gamdl.web_gui import WebGuiHandler, WebGuiServer


class WrapperDecryptIpValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self._proxy_env_backup = {
            name: os.environ.get(name)
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        }
        self._getproxies_backup = urllib.request.getproxies
        self._getproxies_env_backup = urllib.request.getproxies_environment
        for name in self._proxy_env_backup:
            os.environ.pop(name, None)
        urllib.request.getproxies = lambda: {}
        urllib.request.getproxies_environment = lambda: {}
        self.paths = AppPaths(base_dir=Path(self.tempdir.name), app_name="GamdlTest")
        self.server = WebGuiServer(
            ("127.0.0.1", 0),
            WebGuiHandler,
            self.paths,
            log_store=None,
        )
        self.server.wrapper_manager._ports_ready = lambda host, port: False
        self.server.wrapper_manager._docker_available = lambda: False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.app_base_url = f"{self.base_url}{self.server.session_base_path}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        for name, value in self._proxy_env_backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        urllib.request.getproxies = self._getproxies_backup
        urllib.request.getproxies_environment = self._getproxies_env_backup
        self.tempdir.cleanup()

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        include_token: bool = True,
    ) -> tuple[int, bytes]:
        body = b""
        headers = {"Connection": "close"}
        if include_token:
            headers["X-Gamdl-Request-Token"] = self.server.api_request_token
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, f"{self.server.session_base_path}{path}", body=body, headers=headers)
            response = connection.getresponse()
            try:
                return response.status, response.read()
            finally:
                response.close()
        finally:
            connection.close()

    def _get_json(self, path: str) -> dict:
        status, body = self._request(path)
        self.assertEqual(status, 200)
        return json.loads(body)

    def _post_json_error(self, path: str, payload: dict) -> tuple[int, dict]:
        status, body = self._request(path, method="POST", payload=payload)
        self.assertGreaterEqual(status, 400)
        return status, json.loads(body.decode("utf-8"))

    def _post_json(self, path: str, payload: dict) -> dict:
        status, body = self._request(path, method="POST", payload=payload)
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_post_settings_rejects_invalid_wrapper_decrypt_ip(self):
        status, data = self._post_json_error(
            "/api/settings",
            {
                "output_path": str(self.paths.default_output_path),
                "wrapper_decrypt_ip": "127.0.0.1:not-a-port",
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("Wrapper 解密地址", data["error"])
        settings = self.server.settings_store.load()
        self.assertEqual(settings.wrapper_decrypt_ip, "127.0.0.1:10022")
        if self.paths.settings_path.exists():
            self.assertNotIn(
                "not-a-port",
                self.paths.settings_path.read_text(encoding="utf-8"),
            )

    def test_dirty_wrapper_decrypt_ip_does_not_break_settings_or_about(self):
        self.paths.ensure()
        self.paths.settings_path.write_text(
            json.dumps(
                {
                    "output_path": str(self.paths.default_output_path),
                    "wrapper_decrypt_ip": "127.0.0.1:not-a-port",
                }
            ),
            encoding="utf-8",
        )

        settings_data = self._get_json("/api/settings")
        about_data = self._get_json("/api/about")

        self.assertEqual(
            settings_data["settings"]["wrapper_decrypt_ip"],
            "127.0.0.1:10022",
        )
        self.assertFalse(settings_data["wrapper_status"]["available"])
        self.assertEqual(about_data["wrapper_status"]["mode"], "none")

    def test_post_settings_rejects_non_loopback_wrapper_decrypt_ip(self):
        status, data = self._post_json_error(
            "/api/settings",
            {
                "output_path": str(self.paths.default_output_path),
                "wrapper_decrypt_ip": "192.168.1.8:10022",
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("本机回环地址", data["error"])
        settings = self.server.settings_store.load()
        self.assertEqual(settings.wrapper_decrypt_ip, "127.0.0.1:10022")

    def test_post_settings_accepts_ipv6_loopback_wrapper_decrypt_ip(self):
        data = self._post_json(
            "/api/settings",
            {
                "output_path": str(self.paths.default_output_path),
                "wrapper_decrypt_ip": "::1:10022",
            },
        )

        self.assertEqual(data["settings"]["wrapper_decrypt_ip"], "::1:10022")

    def test_post_settings_rejects_ports_without_m3u8_headroom(self):
        status, data = self._post_json_error(
            "/api/settings",
            {
                "output_path": str(self.paths.default_output_path),
                "wrapper_decrypt_ip": "127.0.0.1:55536",
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("55535", data["error"])
        settings = self.server.settings_store.load()
        self.assertEqual(settings.wrapper_decrypt_ip, "127.0.0.1:10022")


if __name__ == "__main__":
    unittest.main()
