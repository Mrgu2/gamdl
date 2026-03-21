import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gamdl.app import AppPaths
from gamdl.web_gui import WebGuiHandler, WebGuiServer


class WrapperDecryptIpValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
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
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.tempdir.cleanup()

    def _get_json(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.base_url}{path}") as response:
            return json.load(response)

    def _post_json_error(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        return error.exception.code, json.loads(error.exception.read().decode("utf-8"))

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


if __name__ == "__main__":
    unittest.main()
