import socket
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from gamdl.desktop_app import _pick_available_port, _select_folder


class DesktopAppTests(unittest.TestCase):
    def test_pick_available_port_keeps_preferred_port_when_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            preferred_port = int(probe.getsockname()[1])

        selected_port = _pick_available_port("127.0.0.1", preferred_port)
        self.assertEqual(selected_port, preferred_port)

    def test_pick_available_port_falls_back_when_preferred_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            probe.listen(1)
            busy_port = int(probe.getsockname()[1])

            selected_port = _pick_available_port("127.0.0.1", busy_port)
            self.assertNotEqual(selected_port, busy_port)
            self.assertGreater(selected_port, 0)

    def test_select_folder_returns_selected_path(self):
        with patch(
            "gamdl.desktop_app.subprocess.run",
            return_value=CompletedProcess(args=["osascript"], returncode=0, stdout="/tmp/chosen\n", stderr=""),
        ):
            self.assertEqual(_select_folder(), "/tmp/chosen")

    def test_select_folder_returns_none_on_cancel(self):
        with patch(
            "gamdl.desktop_app.subprocess.run",
            return_value=CompletedProcess(args=["osascript"], returncode=0, stdout="\n", stderr=""),
        ):
            self.assertIsNone(_select_folder())


if __name__ == "__main__":
    unittest.main()
