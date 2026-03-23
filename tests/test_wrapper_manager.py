import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gamdl.app.wrapper_manager import WrapperManager, prioritize_wrapper_candidates


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
        manager._docker_available = lambda: False
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


if __name__ == "__main__":
    unittest.main()
