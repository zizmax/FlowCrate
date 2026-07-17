import unittest
from unittest.mock import MagicMock, patch

from flowcrate import service


class BuildLaunchdPlistTests(unittest.TestCase):
    def test_label_and_program_arguments(self):
        plist = service.build_launchd_plist(
            executable="/opt/venv/bin/python",
            working_dir="/Users/tester",
            logs_dir="/Users/tester/.flowcrate/logs",
        )
        self.assertEqual(plist["Label"], "com.flowcrate.server")
        args = plist["ProgramArguments"]
        self.assertEqual(args[0], "/opt/venv/bin/python")
        self.assertIn("-m", args)
        self.assertIn("flowcrate.app", args)
        self.assertIn("--no-browser", args)

    def test_keepalive_and_runatload(self):
        plist = service.build_launchd_plist()
        self.assertTrue(plist["KeepAlive"])
        self.assertTrue(plist["RunAtLoad"])

    def test_log_paths_point_at_launchd_log(self):
        plist = service.build_launchd_plist(logs_dir="/tmp/logs")
        self.assertEqual(plist["StandardOutPath"], "/tmp/logs/launchd.log")
        self.assertEqual(plist["StandardErrorPath"], "/tmp/logs/launchd.log")

    def test_working_directory_defaults_to_home(self):
        plist = service.build_launchd_plist(working_dir="/Users/tester")
        self.assertEqual(plist["WorkingDirectory"], "/Users/tester")


class BuildSystemdUnitTests(unittest.TestCase):
    def test_execstart_working_dir_and_install_target(self):
        unit = service.build_systemd_unit(
            executable="/opt/venv/bin/python", working_dir="/home/pi"
        )
        self.assertIn("ExecStart=/opt/venv/bin/python -m flowcrate.app --no-browser", unit)
        self.assertIn("WorkingDirectory=/home/pi", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_defaults_to_current_interpreter(self):
        self.assertIn("-m flowcrate.app --no-browser", service.build_systemd_unit())


class InstallServiceTests(unittest.TestCase):
    @patch("flowcrate.service.platform.system", return_value="Windows")
    def test_install_refuses_on_unsupported_platform(self, _system):
        self.assertFalse(service.install_service())

    @patch("flowcrate.service.platform.system", return_value="Linux")
    @patch("flowcrate.service.shutil.which", return_value=None)
    def test_install_reports_when_systemctl_missing(self, _which, _system):
        self.assertFalse(service.install_service())

    @patch("flowcrate.service.platform.system", return_value="Linux")
    @patch("flowcrate.service.shutil.which", return_value="/bin/systemctl")
    @patch("flowcrate.service.ensure_dirs")
    @patch("flowcrate.service.subprocess.run")
    def test_install_writes_unit_and_enables(self, run, _ensure, _which, _system):
        import tempfile
        from pathlib import Path

        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / "flowcrate.service"
            with patch("flowcrate.service.systemd_user_unit_path", return_value=unit):
                self.assertTrue(service.install_service(url="http://raspberrypi.local:8765"))
            self.assertTrue(unit.exists())
            self.assertIn("ExecStart=", unit.read_text())
        invoked = [" ".join(call.args[0]) for call in run.call_args_list]
        self.assertTrue(any("systemctl --user enable --now" in cmd for cmd in invoked))

    @patch("flowcrate.service.platform.system", return_value="Darwin")
    @patch("flowcrate.service.ensure_dirs")
    @patch("flowcrate.service._load", return_value="bootstrap")
    @patch("flowcrate.service._unload")
    def test_install_writes_and_loads_plist(self, unload, load, _ensure, _system):
        import plistlib
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            plist = Path(tmp) / "com.flowcrate.server.plist"
            with patch("flowcrate.service.plist_path", return_value=plist):
                self.assertTrue(service.install_service(url="http://host.local:8765"))
            self.assertTrue(plist.exists())
            with plist.open("rb") as handle:
                written = plistlib.load(handle)
            self.assertEqual(written["Label"], "com.flowcrate.server")
            load.assert_called_once()

    @patch("flowcrate.service.platform.system", return_value="Darwin")
    @patch("flowcrate.service.subprocess.run")
    def test_load_falls_back_to_legacy_load(self, run, _system):
        run.return_value = MagicMock(returncode=1)
        how = service._load("/tmp/x.plist")
        self.assertEqual(how, "load")
        self.assertEqual(run.call_count, 2)


class UninstallServiceTests(unittest.TestCase):
    @patch("flowcrate.service.platform.system", return_value="Darwin")
    @patch("flowcrate.service._unload")
    def test_uninstall_is_idempotent_when_missing(self, unload, _system):
        from pathlib import Path

        with patch("flowcrate.service.plist_path", return_value=Path("/tmp/does-not-exist.plist")):
            self.assertTrue(service.uninstall_service())
        unload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
