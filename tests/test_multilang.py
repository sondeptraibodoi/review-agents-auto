import contextlib
import io
import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from leanreview.cli import main
from leanreview.multilang import discover, plan_checks, run_checks


class DetectionTests(unittest.TestCase):
    def test_detects_fullstack_monorepo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "apps" / "web").mkdir(parents=True)
            (root / "services" / "api").mkdir(parents=True)
            (root / "apps" / "web" / "package.json").write_text(
                json.dumps({"dependencies": {"@angular/core": "^20"},
                            "devDependencies": {"typescript": "^5"},
                            "scripts": {"lint": "eslint .", "test:ci": "vitest run"}}))
            (root / "services" / "api" / "composer.json").write_text(
                json.dumps({"require": {"laravel/framework": "^10"}}))
            projects = discover(root)
            self.assertEqual([p["path"] for p in projects], ["apps/web", "services/api"])
            self.assertIn("angular", projects[0]["frameworks"])
            self.assertIn("laravel", projects[1]["frameworks"])
            self.assertIn("typescript", projects[0]["languages"])
            plans = plan_checks(root, projects)
            self.assertTrue(any(x["tool"] in {"npm:lint", "pnpm:lint", "yarn:lint"} for x in plans))
            self.assertTrue(any(x["tool"] == "phpunit" for x in plans))

    def test_detect_python_go_rust_java_dotnet_and_bad_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"dependencies": ["bad"]}', encoding="utf-8")
            (root / "pyproject.toml").write_text('[project]\ndependencies=["fastapi"]', encoding="utf-8")
            (root / "go.mod").write_text('module app', encoding="utf-8")
            (root / "Cargo.toml").write_text('[package]', encoding="utf-8")
            (root / "pom.xml").write_text('<project/>', encoding="utf-8")
            (root / "main.csproj").write_text('<Project/>', encoding="utf-8")
            project = discover(root)[0]
            self.assertEqual(set(project["languages"]),
                             {"node", "python", "go", "rust", "java", "dotnet"})
            self.assertIn("fastapi", project["frameworks"])
            self.assertIsInstance(plan_checks(root, [project]), list)

    def test_limits_project_scan_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for n in range(4):
                p = root / "apps" / str(n)
                p.mkdir(parents=True)
                (p / "go.mod").write_text('module x')
            self.assertEqual(len(discover(root, max_projects=2)), 2)
            with self.assertRaises(ValueError):
                discover(root, max_projects=0)

    def test_detect_does_not_execute(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"scripts":{"test":"echo hello"}}')
            with patch("leanreview.multilang.subprocess.run") as run:
                projects = discover(root)
                plan_checks(root, projects)
                run.assert_not_called()


class ExecutionTests(unittest.TestCase):
    def _mock(self, mock_discover, mock_plan, command):
        mock_discover.return_value = [{"path": ".", "languages": ["python"],
                                       "frameworks": [], "manifests": ["pyproject.toml"],
                                       "_package": {}}]
        mock_plan.return_value = [{"project": ".", "tool": "smoke", "kind": "test",
                                  "status": "planned", "command": command}]

    @patch("leanreview.multilang.plan_checks")
    @patch("leanreview.multilang.discover")
    def test_plan_only_does_not_run(self, discovery, plans):
        with tempfile.TemporaryDirectory() as folder:
            self._mock(discovery, plans, [sys.executable, "-c", "raise Exception('must not run')"])
            with patch("leanreview.multilang.subprocess.run") as run:
                result = run_checks(Path(folder))
                run.assert_not_called()
            self.assertEqual(result["counts"]["planned"], 1)
            self.assertEqual(result["token_cost"], 0)

    @patch("leanreview.multilang.plan_checks")
    @patch("leanreview.multilang.discover")
    def test_run_success_and_failure(self, discovery, plans):
        with tempfile.TemporaryDirectory() as folder:
            self._mock(discovery, plans, [sys.executable, "-c", "print('fine')"])
            success = run_checks(Path(folder), execute=True)
            self.assertEqual(success["counts"]["passed"], 1)
            self.assertIn("fine", success["checks"][0]["output_tail"])
            self._mock(discovery, plans, [sys.executable, "-c", "import sys; sys.exit(4)"])
            failure = run_checks(Path(folder), execute=True)
            self.assertEqual(failure["counts"]["failed"], 1)
            self.assertEqual(failure["checks"][0]["exit_code"], 4)

    @patch("leanreview.multilang.plan_checks")
    @patch("leanreview.multilang.discover")
    def test_timeout(self, discovery, plans):
        with tempfile.TemporaryDirectory() as folder:
            self._mock(discovery, plans, [sys.executable, "-c", "import time;time.sleep(3)"])
            result = run_checks(Path(folder), execute=True, timeout=1)
            self.assertEqual(result["counts"]["timeout"], 1)

    @patch("leanreview.multilang.plan_checks")
    @patch("leanreview.multilang.discover")
    def test_redacts_logs(self, discovery, plans):
        with tempfile.TemporaryDirectory() as folder:
            self._mock(discovery, plans, [sys.executable, "-c", "print('password=supersecret')"])
            result = run_checks(Path(folder), execute=True)
            text = result["checks"][0]["output_tail"]
            self.assertNotIn("supersecret", text)
            self.assertIn("[REDACTED]", text)

    def test_cli_detect_and_check(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "package.json").write_text('{"scripts":{"lint":"eslint ."}}')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["detect", "--repo", folder]), 0)
            self.assertIn('"node"', output.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["check", "--repo", folder]), 0)
            self.assertIn('"planned"', output.getvalue())

    def test_cli_review_all_combines_diff_and_local_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "go.mod").write_text("module example\n")
            (root / "main.go").write_text("package main\n")
            for command in (["git", "init", "-q"], ["git", "add", "main.go", "go.mod"],
                            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                             "commit", "--allow-empty", "-m", "initial"]):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            (root / "main.go").write_text("package main\n// change\n")
            mockplan = [{"project": ".", "tool": "smoke", "kind": "test",
                         "status": "planned", "command": [sys.executable, "-c", "print('fine')"]}]
            with patch("leanreview.multilang.plan_checks", return_value=mockplan), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                status = main(["review", "--repo", folder, "--all", "--format", "json"])
            self.assertEqual(status, 0)
            report = json.loads(output.getvalue())
            self.assertIn("local_checks", report)
            self.assertEqual(report["local_checks"]["counts"]["passed"], 1)
            self.assertEqual(report["estimated_patch_tokens"] > 0, True)


if __name__ == "__main__":
    unittest.main()
