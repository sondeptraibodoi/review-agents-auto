import unittest
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
from leanreview.backend import _run, backend_check


class BackendTests(unittest.TestCase):
    def test_runs_commands_without_shell(self):
        with tempfile.TemporaryDirectory() as d:
            result = _run([sys.executable, "-c", "print('passed')"], Path(d), 5, 2000)
            self.assertEqual(result["status"], "passed")
            self.assertIn("passed", result["output_tail"])

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            result = _run([sys.executable, "-c", "import time; time.sleep(3)"], Path(d), 1, 2000)
            self.assertEqual(result["status"], "timeout")

    def test_runner_skips_missing(self):
        with tempfile.TemporaryDirectory() as d:
            result = backend_check(Path(d))
            self.assertEqual(result["runs"]["phpunit"]["status"], "skipped")
            self.assertEqual(result["runs"]["phpstan"]["status"], "skipped")

    def test_auto_detection_mocked(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "phpstan.neon").write_text("parameters:\n", encoding="utf-8")
            with patch("leanreview.backend.discover_tools", return_value={"phpunit": ["fakephp","unit"], "phpstan": ["fakephp", "stan"]}), \
                 patch("leanreview.backend._run", return_value={"status": "passed"}) as run:
                result = backend_check(repo)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(len(result["runs"]), 2)

if __name__ == '__main__':
    unittest.main()
