import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gamdl.network import NetworkConfig
from gamdl.app.wrapper_manager import (
    MAX_WRAPPER_DECRYPT_PORT,
    WrapperManager,
    parse_wrapper_decrypt_ip,
    prioritize_wrapper_candidates,
)


class WrapperManagerTests(unittest.TestCase):
    def test_prioritize_wrapper_candidates_prefers_exact_port(self):
        candidates = prioritize_wrapper_candidates(
            ["wrapper-latest-10020", "wrapper-latest-10022"],
            preferred_port=10022,
        )
        self.assertEqual(candidates[0].name, "wrapper-latest-10022")
        self.assertEqual(candidates[0].port, 10022)

    def test_prioritize_wrapper_candidates_falls_back_to_single_container(self):
        candidates = prioritize_wrapper_candidates(
            ["wrapper-latest-10022"],
            preferred_port=10020,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].port, 10022)

    def test_prioritize_wrapper_candidates_returns_empty_when_ambiguous(self):
        candidates = prioritize_wrapper_candidates(
            ["wrapper-latest-10022", "wrapper-latest-10024"],
            preferred_port=10020,
        )
        self.assertEqual(candidates, [])

    def test_probe_status_reports_missing_wrapper_without_docker(self):
        manager = WrapperManager()
        manager._ports_ready = lambda host, port: False
        manager._docker_available = lambda network_config=None: False
        status = manager.probe_status("127.0.0.1:10022")
        self.assertFalse(status.available)
        self.assertEqual(status.mode, "none")
        self.assertIn("默认使用 AAC", status.message)

    def test_wrapper_manager_prefers_absolute_docker_path_for_packaged_app(self):
        with patch(
            "gamdl.app.wrapper_manager.resolve_executable",
            side_effect=[
                SimpleNamespace(available=False, path=None),
                SimpleNamespace(available=True, path="/opt/homebrew/bin/docker"),
            ],
        ):
            manager = WrapperManager()

        self.assertEqual(manager.docker_bin, "/opt/homebrew/bin/docker")

    def test_docker_available_tolerates_slightly_slow_docker_cli(self):
        with tempfile.TemporaryDirectory() as tempdir:
            fake_docker = Path(tempdir) / "docker"
            fake_docker.write_text("#!/bin/sh\nsleep 3\nexit 0\n", encoding="utf-8")
            fake_docker.chmod(0o755)

            manager = WrapperManager(docker_bin=str(fake_docker))

            self.assertTrue(manager._docker_available())

    def test_start_container_tolerates_slightly_slow_docker_cli(self):
        with tempfile.TemporaryDirectory() as tempdir:
            fake_docker = Path(tempdir) / "docker"
            fake_docker.write_text("#!/bin/sh\nsleep 3\nexit 0\n", encoding="utf-8")
            fake_docker.chmod(0o755)

            manager = WrapperManager(docker_bin=str(fake_docker))

            self.assertTrue(manager._start_container("wrapper-latest-10022"))

    @patch(
        "gamdl.app.wrapper_manager.socket.getaddrinfo",
        return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 10022, 0, 0))],
    )
    @patch("gamdl.app.wrapper_manager.socket.socket")
    def test_is_port_open_supports_ipv6_loopback(self, socket_ctor, _getaddrinfo):
        probe = MagicMock()
        probe.__enter__.return_value = probe
        probe.connect_ex.return_value = 0
        socket_ctor.return_value = probe

        self.assertTrue(WrapperManager._is_port_open("::1", 10022))
        socket_ctor.assert_called_once_with(socket.AF_INET6, socket.SOCK_STREAM, 0)
        probe.connect_ex.assert_called_once_with(("::1", 10022, 0, 0))

    def test_parse_wrapper_decrypt_ip_rejects_ports_without_m3u8_headroom(self):
        with self.assertRaises(ValueError) as error:
            parse_wrapper_decrypt_ip(str(MAX_WRAPPER_DECRYPT_PORT + 1))

        self.assertIn(str(MAX_WRAPPER_DECRYPT_PORT), str(error.exception))

    @patch("gamdl.app.wrapper_manager.subprocess.run")
    def test_docker_available_clears_proxy_env_in_direct_mode(self, run_mock):
        run_mock.return_value = SimpleNamespace(returncode=0)

        manager = WrapperManager()
        manager._docker_available(NetworkConfig(mode="direct", proxy_url=""))

        env = run_mock.call_args.kwargs["env"]
        self.assertNotIn("http_proxy", env)
        self.assertNotIn("HTTPS_PROXY", env)

    @patch("gamdl.app.wrapper_manager.subprocess.run")
    def test_start_container_injects_proxy_env_in_custom_mode(self, run_mock):
        run_mock.return_value = SimpleNamespace(returncode=0)

        manager = WrapperManager()
        manager._start_container(
            "wrapper-latest-10022",
            NetworkConfig(mode="custom", proxy_url="http://127.0.0.1:7890"),
        )

        env = run_mock.call_args.kwargs["env"]
        self.assertEqual(env["http_proxy"], "http://127.0.0.1:7890")
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7890")


if __name__ == "__main__":
    unittest.main()
