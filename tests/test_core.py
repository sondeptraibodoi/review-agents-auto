import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from leanreview.core import (
    slice_diff, offline_findings, cache_file, redact, sensitive_path,
    skip_path, write_private_json,
)
from leanreview.cli import main

PATCH = '''diff --git a/api.php b/api.php
index 123..456 100644
--- a/api.php
+++ b/api.php
@@ -1,1 +1,3 @@
 old();
+dd($token);
+$api_key = "SECRET123";
'''
LOCK = '''diff --git a/composer.lock b/composer.lock
--- a/composer.lock
+++ b/composer.lock
@@ -1 +1 @@
-old
+new
'''


class Tests(unittest.TestCase):
    def test_secret_redaction(self):
        s = slice_diff(PATCH)
        self.assertNotIn("SECRET123", s.patch)
        self.assertIn("[REDACTED]", s.patch)

    def test_ignore_lock(self):
        self.assertEqual(slice_diff(LOCK + PATCH).files, ["api.php"])

    def test_budget(self):
        s = slice_diff(PATCH * 100, max_chars=250)
        self.assertTrue(s.truncated)
        self.assertLessEqual(len(s.patch), 250)

    def test_detect_added_only(self):
        f = offline_findings(slice_diff(PATCH).patch)
        self.assertTrue(any(x["rule"] == "debug-output" and x["line"] == 2 for x in f))
        self.assertTrue(any(x["rule"] == "secret-like" for x in f))

    def test_sensitive_paths(self):
        self.assertEqual(slice_diff('diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -0,0 +1 @@\n+PASSWORD=x\n').files, [])
        self.assertTrue(sensitive_path("secrets/private.key"))
        self.assertTrue(sensitive_path("src/.env.production"))
        self.assertTrue(skip_path("frontend/dist/assets/app.js"))
        self.assertTrue(skip_path("dist/foo.min.js"))

    def test_redact_token_signatures(self):
        self.assertNotIn("ghp_" + "a" * 40, redact("ghp_" + "a" * 40))
        self.assertNotIn("sk-" + "a" * 40, redact("sk-" + "a" * 40))

    def test_cache_path(self):
        self.assertEqual(cache_file(Path("/tmp"), "abc", "codex", "default"),
                         cache_file(Path("/tmp"), "abc", "codex", "default"))

    def test_important_files_prioritized(self):
        doc = '''diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -0,0 +1 @@\n+Docs\n'''
        a = slice_diff(doc + PATCH, max_files=1)
        self.assertEqual(a.files, ["api.php"])
        self.assertTrue(a.truncated)

    def test_line_numbers_multiple_hunks(self):
        diff = '''diff --git a/Code.php b/Code.php
--- a/Code.php
+++ b/Code.php
@@ -4,2 +4,2 @@
 foo
+dd($a);
@@ -100,1 +101,2 @@
 foo
+dd($b);
'''
        self.assertEqual([f['line'] for f in offline_findings(diff)], [5, 102])

    def test_private_cache_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '.leanreview' / 'cache' / 'foo.json'
            write_private_json(path, {'secret': 1})
            self.assertEqual(json.loads(path.read_text()), {'secret': 1})

    def test_cli_doctor(self):
        self.assertEqual(main(["doctor"]), 0)

    def test_review_empty_diff(self):
        with tempfile.TemporaryDirectory() as td:
            from subprocess import run
            run(['git', 'init', '-q', td], check=True)
            with patch('builtins.print'):
                self.assertEqual(main(['review', '--repo', td, '--format', 'json']), 0)


if __name__ == '__main__':
    unittest.main()
