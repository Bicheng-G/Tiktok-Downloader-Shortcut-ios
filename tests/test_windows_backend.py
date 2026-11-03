"""Exercise the real Windows launcher without contacting video platforms or Telegram."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "windows_backend.py"
spec = importlib.util.spec_from_file_location("windows_backend", SCRIPT)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class EnvTests(unittest.TestCase):
    def test_literal_quoted_values_and_bom(self):
        key = "RESOLVER_LAUNCHER_TEST"
        old = os.environ.get(key)
        try:
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / ".env"
                path.write_text(f'# comment\n{key} = "literal $value # suffix"\n', encoding="utf-8-sig")
                backend.load_env(path)
                self.assertEqual(os.environ[key], "literal $value # suffix")
                path.write_text("not an assignment", encoding="utf-8")
                with self.assertRaises(ValueError):
                    backend.load_env(path)
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


@unittest.skipUnless(sys.platform == "win32", "Windows lifecycle")
class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env_file = Path(self.temp.name) / ".env"
        self.env_file.write_text(
            "DOUYIN_API_TOKEN=offline-launcher-test-token-123456\n"
            "DOUYIN_ENGINE=auto\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_ALLOWED_USER_IDS=\n",
            encoding="utf-8",
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.addCleanup(lambda: self.command("stop"))

    def command(self, action):
        return subprocess.run(
            [sys.executable, str(SCRIPT), action, "--port", str(self.port), "--env-file", str(self.env_file)],
            capture_output=True, text=True, timeout=110,
        )

    def test_background_start_repeat_and_graceful_stop(self):
        result = self.command("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(backend.healthy(self.port))
        self.assertIn("running", self.command("start").stdout)
        self.assertIn("running", self.command("status").stdout)
        self.assertEqual(self.command("stop").returncode, 0)
        self.assertFalse(backend.healthy(self.port))
        self.assertIn("stopped", self.command("status").stdout)
        # The old process released both its event and listener.
        self.assertEqual(self.command("start").returncode, 0)

    def test_occupied_port_is_not_a_successful_launch(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", self.port))
            sock.listen()
            result = self.command("start")
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse(backend.StopEvent(self.port).exists())

    def test_invalid_configuration_does_not_leave_background_process(self):
        self.env_file.write_text("DOUYIN_API_TOKEN=short\n", encoding="utf-8")
        result = self.command("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(backend.StopEvent(self.port).exists())

    def test_windowless_autostart_runtime(self):
        import time
        child = subprocess.Popen(
            [str(Path(sys.executable).with_name("pythonw.exe")), str(SCRIPT), "run",
             "--port", str(self.port), "--env-file", str(self.env_file)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            deadline = time.monotonic() + 20
            while not backend.healthy(self.port) and time.monotonic() < deadline:
                self.assertIsNone(child.poll())
                time.sleep(0.2)
            self.assertTrue(backend.healthy(self.port))
            self.assertEqual(self.command("stop").returncode, 0)
            self.assertEqual(child.wait(timeout=10), 0)
        finally:
            if child.poll() is None:
                self.command("stop")
                child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
