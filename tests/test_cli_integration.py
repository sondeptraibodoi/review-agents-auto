import io
import os
import shutil
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from leanreview.cli import main


class CLITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('php'), 'PHP not installed')
    def test_phpunit_runner_as_real_php_process(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / 'vendor' / 'bin').mkdir(parents=True)
            (repo / 'vendor' / 'bin' / 'phpunit').write_text('<?php echo "unit-smoke-ok\\n";')
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(['backend', 'check', '--repo', str(repo), '--only', 'phpunit'])
            self.assertEqual(code, 0)
            self.assertIn('unit-smoke-ok', buf.getvalue())

    def test_db_missing_env_does_not_leak_secret_or_crash(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.dict(os.environ, {'LEANREVIEW_DATABASE_URL': ''}):
            buf = io.StringIO()
            with redirect_stderr(buf):
                code = main(['db', 'inspect'])
            self.assertEqual(code, 2)
            self.assertIn('LEANREVIEW_DATABASE_URL', buf.getvalue())

    def test_backend_skips_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(['backend', 'check', '--repo', d])
            self.assertEqual(code, 2)

if __name__ == '__main__':
    unittest.main()
